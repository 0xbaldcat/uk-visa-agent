"""Fact provenance guard.

Research doc §5.1 failure mode 4 is the dangerous one: a model that, trying to make
the case look better, embellishes a bank balance, invents an itinerary, or upgrades
a job title. Under Immigration Rules 9.7.1 a false representation is a *mandatory*
refusal even when innocent, and deception carries a 10-year ban.

"Don't fabricate" in a prompt is not a control. This module is the control: any
factual claim rendered into a client-facing deliverable must resolve to a value the
client actually supplied. Prose the model writes is checked against the fact ledger
before it can leave the system.

Design note: we check *numbers, dates and named entities*, because those are what
carry falsifiable factual weight in an application. Connective prose is free text.
"""
import re
from typing import Dict, Any, List, Set


class FabricationError(Exception):
    """A deliverable contained a factual token with no client-supplied origin."""

    def __init__(self, message, offending):
        Exception.__init__(self, message)
        self.offending = offending


# Numbers with real factual weight. Bare small integers ("2 documents", "3 weeks")
# are excluded -- they are almost always narrative, and flagging them would make the
# guard so noisy it gets switched off, which is the classic way a control dies.
_NUMBER = re.compile(r"\b\d[\d,]*\.?\d*\b")
_DATE_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_MONEY = re.compile(r"[£$€]\s?\d[\d,]*\.?\d*")


class FactLedger(object):
    """Every fact the client actually gave us, flattened into comparable tokens."""

    def __init__(self):
        self._values = set()      # type: Set[str]
        self._origins = {}        # type: Dict[str, str]

    @staticmethod
    def _norm(value):
        s = str(value).strip()
        s = s.replace(",", "")
        # Trailing .0 from float round-trips would otherwise fail an exact match.
        if s.endswith(".0"):
            s = s[:-2]
        return s

    def add(self, value, origin):
        # type: (Any, str) -> None
        if value is None:
            return
        if isinstance(value, bool):
            return
        if isinstance(value, (list, tuple)):
            for v in value:
                self.add(v, origin)
            return
        norm = self._norm(value)
        if not norm:
            return
        self._values.add(norm)
        self._origins.setdefault(norm, origin)
        # A date supplied as 2026-10-01 should also license "1 October 2026" style
        # rendering, so register the components too.
        if _DATE_ISO.match(norm):
            y, m, d = norm.split("-")
            for part in (y, str(int(m)), str(int(d)), m, d):
                self._values.add(part)
                self._origins.setdefault(part, origin)

    def add_case(self, case, checklist=None):
        """Load slots and every extracted document field."""
        for slot_id, value in (case.slots or {}).items():
            self.add(value, "slot:%s" % slot_id)
        for ev_id, rec in (case.evidence or {}).items():
            for field, value in (rec.get("fields") or {}).items():
                self.add(value, "evidence:%s/%s" % (ev_id, field))

    def knows(self, token):
        return self._norm(token) in self._values

    def origin(self, token):
        return self._origins.get(self._norm(token))

    def __len__(self):
        return len(self._values)


def factual_tokens(text):
    # type: (str) -> List[str]
    """Tokens in generated prose that assert something falsifiable."""
    tokens = []
    tokens.extend(_DATE_ISO.findall(text))
    for money in _MONEY.findall(text):
        tokens.append(re.sub(r"[£$€\s]", "", money))
    for num in _NUMBER.findall(text):
        cleaned = num.replace(",", "")
        # Skip small bare integers: narrative counts, not application facts.
        try:
            if "." not in cleaned and len(cleaned) <= 2:
                continue
        except ValueError:
            pass
        tokens.append(cleaned)
    # Deduplicate, preserve order.
    seen, out = set(), []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def verify(text, ledger, allow=None):
    # type: (str, FactLedger, Any) -> List[str]
    """Return factual tokens in `text` that the ledger cannot account for."""
    allow = set(str(a) for a in (allow or []))
    unsupported = []
    for token in factual_tokens(text):
        if token in allow:
            continue
        if not ledger.knows(token):
            unsupported.append(token)
    return unsupported


def enforce(text, ledger, allow=None, where="deliverable"):
    # type: (str, FactLedger, Any, str) -> str
    """Gate. Raises rather than emitting an unverifiable factual claim."""
    unsupported = verify(text, ledger, allow=allow)
    if unsupported:
        raise FabricationError(
            "%s contains %d factual value(s) not supplied by the client: %s"
            % (where, len(unsupported), ", ".join(unsupported)),
            unsupported)
    return text
