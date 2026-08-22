"""Persistence. The DB is the single source of truth for case state.

Conversation history is *not* state. Every turn re-reads the case row and
re-derives the next action, so a client who replies out of order, re-sends a
document, or goes quiet for three days cannot drift the case.

Also holds the two things that are cheap now and painful to retrofit:
inbound de-duplication and an outbox.
"""
import json
import sqlite3
from datetime import date
from typing import Optional, Dict, Any, List, Tuple

from state import Stage
import validate

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    slots TEXT NOT NULL DEFAULT '{}',
    evidence TEXT NOT NULL DEFAULT '{}',
    escalation_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Inbound de-duplication. WhatsApp webhooks retry; email threads fork and
-- re-deliver. Without this a retry re-processes a document and can re-ask a
-- question the client already answered.
CREATE TABLE IF NOT EXISTS inbound (
    dedupe_key TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    body TEXT
);

-- Outbox. Compose and persist first, send second, mark sent third, so a crash
-- mid-send degrades to a duplicate we can detect rather than a silent drop.
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    body TEXT NOT NULL,
    action_kind TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS email_message_cases (
    message_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Case(object):
    """In-memory view of a case row."""

    def __init__(self, id, route_id, stage, slots, evidence, escalation_reason=None):
        self.id = id
        self.route_id = route_id
        self.stage = Stage(stage)
        self.slots = slots                     # slot_id -> value
        self.evidence = evidence               # evidence_id -> {"fields":{...}, "failures":[...]}
        self.escalation_reason = escalation_reason

    @staticmethod
    def _blocking(rec):
        return [f for f in (rec.get("failures") or []) if not f.get("advisory")]

    def first_failed_evidence(self, checklist):
        # type: (Any) -> Optional[Tuple[str, List[Dict[str, Any]]]]
        """Earliest required evidence item with a *blocking* failure.

        Advisory failures (departures from practice rather than from the Rules)
        are reported but never send the client back for a replacement -- telling
        someone their document is unusable because it differs from custom would be
        both wrong and, for them, expensive.

        Ordered by the checklist, not by upload time, so the client is asked to fix
        things in a stable order across turns.
        """
        for ev in checklist.required_evidence(self.slots):
            rec = self.evidence.get(ev["id"])
            if rec and self._blocking(rec):
                return ev["id"], self._blocking(rec)
        return None

    def outstanding(self, checklist):
        """(satisfied, missing, failing) evidence ids -- used by the report."""
        satisfied, missing, failing = [], [], []
        for ev in checklist.required_evidence(self.slots):
            rec = self.evidence.get(ev["id"])
            if rec is None:
                missing.append(ev["id"])
            elif self._blocking(rec):
                failing.append(ev["id"])
            else:
                # Advisory-only items count as satisfied; the note travels with the
                # QC report so the adviser still sees it.
                satisfied.append(ev["id"])
        return satisfied, missing, failing

    def is_complete(self, checklist):
        _, missing, failing = self.outstanding(checklist)
        return not missing and not failing


class Store(object):
    def __init__(self, path=":memory:"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- cases ----------------------------------------------------------

    def create_case(self, case_id, route_id):
        self.conn.execute(
            "INSERT INTO cases (id, route_id, stage) VALUES (?, ?, ?)",
            (case_id, route_id, Stage.INTAKE.value))
        self.conn.commit()
        self.log(case_id, "case_created", {"route_id": route_id})
        return self.get_case(case_id)

    def get_case(self, case_id):
        # type: (str) -> Optional[Case]
        row = self.conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            return None
        return Case(row["id"], row["route_id"], row["stage"],
                    json.loads(row["slots"]), json.loads(row["evidence"]),
                    row["escalation_reason"])

    def save_case(self, case):
        self.conn.execute(
            "UPDATE cases SET stage=?, slots=?, evidence=?, escalation_reason=?, "
            "updated_at=datetime('now') WHERE id=?",
            (case.stage.value, json.dumps(case.slots), json.dumps(case.evidence),
             case.escalation_reason, case.id))
        self.conn.commit()

    # --- inbound dedupe -------------------------------------------------

    def seen_inbound(self, dedupe_key):
        # type: (str) -> bool
        row = self.conn.execute(
            "SELECT 1 FROM inbound WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        return row is not None

    def record_inbound(self, dedupe_key, case_id, channel, body):
        # type: (str, str, str, str) -> bool
        """Returns False if this message was already processed."""
        try:
            self.conn.execute(
                "INSERT INTO inbound (dedupe_key, case_id, channel, body) VALUES (?,?,?,?)",
                (dedupe_key, case_id, channel, body))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            self.log(case_id, "inbound_duplicate_ignored", {"dedupe_key": dedupe_key})
            return False

    # --- outbox ---------------------------------------------------------

    def enqueue(self, case_id, channel, body, action_kind=None):
        cur = self.conn.execute(
            "INSERT INTO outbox (case_id, channel, body, action_kind) VALUES (?,?,?,?)",
            (case_id, channel, body, action_kind))
        self.conn.commit()
        return cur.lastrowid

    def pending_outbox(self):
        return self.conn.execute(
            "SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY id").fetchall()

    def mark_sent(self, outbox_id):
        self.conn.execute(
            "UPDATE outbox SET sent_at = datetime('now') WHERE id = ?", (outbox_id,))
        self.conn.commit()

    # --- audit ----------------------------------------------------------

    def log(self, case_id, kind, detail=None):
        self.conn.execute("INSERT INTO audit (case_id, kind, detail) VALUES (?,?,?)",
                          (case_id, kind, json.dumps(detail or {})))
        self.conn.commit()

    def audit_trail(self, case_id):
        return self.conn.execute(
            "SELECT * FROM audit WHERE case_id = ? ORDER BY id", (case_id,)).fetchall()

    # --- email threading -----------------------------------------------

    def remember_email_message(self, message_id, case_id):
        if not message_id:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO email_message_cases (message_id, case_id) VALUES (?, ?)",
            (message_id, case_id))
        self.conn.commit()

    def case_for_email_message(self, message_id):
        if not message_id:
            return None
        row = self.conn.execute(
            "SELECT case_id FROM email_message_cases WHERE message_id = ?",
            (message_id,)).fetchone()
        return None if row is None else row["case_id"]
