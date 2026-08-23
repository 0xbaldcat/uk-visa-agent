"""The model boundary.

The model is allowed exactly three jobs. Everything else is code:

  1. parse_reply    - free text -> structured slot values
  2. extract_fields - document -> structured fields
  3. draft_paragraph- a risk observation -> client-facing prose
  4. analyse_case   - fact ledger -> candidate whole-case observations

Two properties matter more than which model sits behind this:

  * Every output is schema-checked before it re-enters the system. A model that
    returns something unexpected produces an escalation, not a corrupted case.
  * The model is never asked "what does this visa require?" or "is this enough?".
    Those questions have no place here -- the first is answered by config, the
    second by a human.

`StubModel` makes the demo run offline and deterministically. A real adapter
implements the same three methods.
"""
from typing import Dict, Any, List, Optional


class ModelRefusal(Exception):
    """The model could not produce a usable answer. Escalate, never guess."""


class Model(object):
    def parse_reply(self, text, expected_slot, slot_spec):
        raise NotImplementedError

    def extract_fields(self, document, wanted_fields):
        raise NotImplementedError

    def draft_paragraph(self, risk, case_facts):
        raise NotImplementedError

    def analyse_case(self, context):
        raise NotImplementedError


def coerce_slot(value, slot_spec):
    """Schema check on anything the model claims a slot should be.

    Runs on the way back from the model, so a hallucinated enum value or an
    unparseable date becomes a refusal rather than silently entering the case.
    """
    if value is None:
        raise ModelRefusal("no value produced for slot %s" % slot_spec.get("id"))
    kind = slot_spec.get("type")
    if kind == "bool":
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("yes", "y", "true", "1"):
            return True
        if s in ("no", "n", "false", "0"):
            return False
        raise ModelRefusal("could not read %r as yes/no" % value)
    if kind == "number":
        try:
            return float(str(value).replace(",", "").replace("£", "").strip())
        except ValueError:
            raise ModelRefusal("could not read %r as a number" % value)
    if kind == "enum":
        allowed = slot_spec.get("values", [])
        if value not in allowed:
            raise ModelRefusal(
                "%r is not one of the permitted values %s" % (value, allowed))
        return value
    if kind == "date":
        from validate import _parse_date
        parsed = _parse_date(value)
        if parsed is None:
            raise ModelRefusal("could not read %r as a date" % value)
        return str(parsed)
    return str(value).strip()


class StubModel(Model):
    """Deterministic stand-in.

    Reads from a scripted mapping instead of calling out. Keeps the demo runnable
    and the tests hermetic, without pretending the seam does not exist.
    """

    def __init__(self, replies=None, documents=None, paragraphs=None):
        self.replies = replies or {}          # slot_id -> raw value
        self.documents = documents or {}      # document ref -> fields
        self.paragraphs = paragraphs or {}    # risk_id -> prose
        self.calls = []

    def parse_reply(self, text, expected_slot, slot_spec):
        self.calls.append(("parse_reply", expected_slot))
        if expected_slot not in self.replies:
            raise ModelRefusal("nothing scripted for slot %s" % expected_slot)
        return coerce_slot(self.replies[expected_slot], slot_spec)

    def extract_fields(self, document, wanted_fields):
        self.calls.append(("extract_fields", document))
        if document not in self.documents:
            raise ModelRefusal("nothing scripted for document %s" % document)
        raw = self.documents[document]
        # Only fields the checklist asked for are admitted; a model volunteering
        # extra fields must not widen the case record.
        return dict((k, v) for k, v in raw.items() if k in wanted_fields)

    def draft_paragraph(self, risk, case_facts):
        self.calls.append(("draft_paragraph", risk["id"]))
        return self.paragraphs.get(risk["id"])
