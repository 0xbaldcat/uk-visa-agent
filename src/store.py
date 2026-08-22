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

CREATE TABLE IF NOT EXISTS email_sender_cases (
    sender TEXT NOT NULL,
    case_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (sender, case_id)
);

CREATE TABLE IF NOT EXISTS ingress_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    raw_message_id TEXT,
    event_type TEXT NOT NULL,
    prompt_version TEXT,
    schema_version TEXT,
    model_name TEXT,
    candidate_json TEXT NOT NULL DEFAULT '{}',
    accepted_json TEXT NOT NULL DEFAULT '{}',
    rejected_json TEXT NOT NULL DEFAULT '{}',
    validation_errors TEXT NOT NULL DEFAULT '[]',
    repair_attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS document_extraction_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    evidence_id TEXT,
    document_ref TEXT,
    provider TEXT,
    model_name TEXT,
    raw_text TEXT NOT NULL DEFAULT '',
    candidate_json TEXT NOT NULL DEFAULT '{}',
    accepted_json TEXT NOT NULL DEFAULT '{}',
    rejected_json TEXT NOT NULL DEFAULT '{}',
    validation_errors TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS validation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    evidence_id TEXT,
    check_kind TEXT,
    result TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS generation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    generation_type TEXT,
    model_name TEXT,
    referenced_facts TEXT NOT NULL DEFAULT '[]',
    output_ref TEXT,
    guard_result TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workflow_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    before_state TEXT,
    after_state TEXT,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS adviser_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    note TEXT,
    reviewer TEXT,
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
        self._ensure_trace_columns()
        self._backfill_email_sender_cases()
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

    # --- trace events ---------------------------------------------------

    def record_ingress_event(self, case_id, trace):
        cur = self.conn.execute(
            "INSERT INTO ingress_events "
            "(case_id, raw_message_id, event_type, prompt_version, schema_version, "
            "model_name, candidate_json, accepted_json, rejected_json, validation_errors, "
            "repair_attempts, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (case_id,
             trace.get("raw_message_id"),
             trace.get("event_type"),
             trace.get("prompt_version"),
             trace.get("schema_version"),
             trace.get("model_name"),
             json.dumps(trace.get("candidate_json") or {}),
             json.dumps(trace.get("accepted_json") or {}),
             json.dumps(trace.get("rejected_json") or {}),
             json.dumps(trace.get("validation_errors") or []),
             int(trace.get("repair_attempts") or 0),
             trace.get("status") or "rejected"))
        self.conn.commit()
        return cur.lastrowid

    def record_document_extraction_event(self, case_id, evidence_id, document_ref, trace):
        cur = self.conn.execute(
            "INSERT INTO document_extraction_events "
            "(case_id, evidence_id, document_ref, provider, model_name, raw_text, "
            "candidate_json, accepted_json, rejected_json, validation_errors, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (case_id,
             evidence_id,
             document_ref,
             trace.get("provider"),
             trace.get("model_name"),
             trace.get("raw_text") or "",
             json.dumps(trace.get("candidate_json") or {}),
             json.dumps(trace.get("accepted_json") or {}),
             json.dumps(trace.get("rejected_json") or {}),
             json.dumps(trace.get("validation_errors") or []),
             trace.get("status") or "rejected"))
        self.conn.commit()
        return cur.lastrowid

    def record_workflow_event(self, case_id, event_type, before_state=None,
                              after_state=None, detail=None):
        self.conn.execute(
            "INSERT INTO workflow_events "
            "(case_id, event_type, before_state, after_state, detail) VALUES (?,?,?,?,?)",
            (case_id, event_type, before_state, after_state, json.dumps(detail or {})))
        self.conn.commit()

    def record_validation_event(self, case_id, evidence_id, check_kind, result, detail=None):
        self.conn.execute(
            "INSERT INTO validation_events "
            "(case_id, evidence_id, check_kind, result, detail) VALUES (?,?,?,?,?)",
            (case_id, evidence_id, check_kind, result, json.dumps(detail or {})))
        self.conn.commit()

    def record_adviser_review(self, case_id, decision, note=None, reviewer=None):
        cur = self.conn.execute(
            "INSERT INTO adviser_reviews (case_id, decision, note, reviewer) "
            "VALUES (?,?,?,?)",
            (case_id, decision, note, reviewer))
        self.conn.commit()
        self.log(case_id, "adviser_review_recorded", {
            "review_id": cur.lastrowid,
            "decision": decision,
            "reviewer": reviewer,
            "note": note,
        })
        return cur.lastrowid

    def latest_adviser_review(self, case_id):
        return self.conn.execute(
            "SELECT * FROM adviser_reviews WHERE case_id = ? ORDER BY id DESC LIMIT 1",
            (case_id,)).fetchone()

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

    def remember_email_sender(self, sender, case_id):
        if not sender:
            return
        normalized = sender.strip().lower()
        if not normalized:
            return
        self.conn.execute(
            "INSERT INTO email_sender_cases (sender, case_id) VALUES (?, ?) "
            "ON CONFLICT(sender, case_id) DO UPDATE SET last_seen_at=datetime('now')",
            (normalized, case_id))
        self.conn.commit()

    def active_cases_for_email_sender(self, sender):
        if not sender:
            return []
        normalized = sender.strip().lower()
        rows = self.conn.execute(
            "SELECT esc.case_id FROM email_sender_cases esc "
            "JOIN cases c ON c.id = esc.case_id "
            "WHERE esc.sender = ? AND c.stage NOT IN (?, ?) "
            "ORDER BY esc.last_seen_at DESC, esc.created_at DESC",
            (normalized, Stage.HUMAN_REVIEW.value, Stage.ESCALATED.value)).fetchall()
        return [row["case_id"] for row in rows]

    def _ensure_trace_columns(self):
        columns = set(row["name"] for row in self.conn.execute(
            "PRAGMA table_info(document_extraction_events)").fetchall())
        additions = [
            ("model_name", "TEXT"),
            ("raw_text", "TEXT NOT NULL DEFAULT ''"),
            ("rejected_json", "TEXT NOT NULL DEFAULT '{}'"),
        ]
        for name, ddl in additions:
            if name not in columns:
                self.conn.execute(
                    "ALTER TABLE document_extraction_events ADD COLUMN %s %s" % (
                        name, ddl))

    def _backfill_email_sender_cases(self):
        rows = self.conn.execute(
            "SELECT case_id, detail FROM audit WHERE kind = 'case_routed'").fetchall()
        for row in rows:
            try:
                detail = json.loads(row["detail"] or "{}")
            except ValueError:
                continue
            sender = detail.get("from")
            if sender:
                self.remember_email_sender(sender, row["case_id"])
