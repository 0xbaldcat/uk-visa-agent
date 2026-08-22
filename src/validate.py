"""Deterministic validators.

The model extracts fields; these functions decide whether they pass. Keeping the
judgement here (rather than asking a model "does this bank statement qualify?")
is what stops a confident-but-wrong model from producing a defective pack.

Every validator returns a Failure or None. Failures are data, so the composer can
turn them into a client-facing sentence without re-deriving why something failed.
"""
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, List


class Failure(object):
    """A check that did not pass.

    `advisory` separates "this is not what the Rules require" from "this departs
    from common practice". Conflating the two would have the agent telling a client
    that practitioner habit is law, which is its own kind of false statement.
    Advisory failures are reported but never block delivery.
    """

    def __init__(self, check_kind, field, message, detail=None,
                 advisory=False, note=None, source=None):
        self.check_kind = check_kind
        self.field = field
        self.message = message           # neutral, factual; no verdict language
        self.detail = detail or {}
        self.advisory = advisory
        self.note = note
        self.source = source

    def __repr__(self):
        return "Failure(%s, %s, advisory=%s, %r)" % (
            self.check_kind, self.field, self.advisory, self.message)

    def to_dict(self):
        return {"check": self.check_kind, "field": self.field,
                "message": self.message, "detail": self.detail,
                "advisory": self.advisory, "note": self.note, "source": self.source}


class UnknownCheck(Exception):
    """Raised when config names a check we have no code for.

    Deliberately fatal: silently skipping an unimplemented check would let an
    incomplete pack be reported as complete.
    """


def _parse_date(value):
    # type: (Any) -> Optional[date]
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _normalise_name(value):
    # type: (Any) -> str
    if value is None:
        return ""
    return " ".join(str(value).replace(",", " ").lower().split())


def check_field_present(check, fields, slots, today):
    failures = []
    for f in check.get("fields", []):
        v = fields.get(f)
        if v is None or str(v).strip() == "":
            failures.append(Failure("field_present", f,
                                    "could not read %s from the document" % f.replace("_", " ")))
    return failures


def check_name_matches(check, fields, slots, today):
    field = check["field"]
    got = _normalise_name(fields.get(field))
    want = _normalise_name(slots.get(check["slot"]))
    if not got or not want:
        return []          # absence is field_present's job, not ours
    got_parts, want_parts = set(got.split()), set(want.split())
    # Tolerate middle names and ordering; require meaningful overlap.
    if not want_parts.issubset(got_parts) and not got_parts.issubset(want_parts):
        return [Failure("name_matches", field,
                        "the name on the document (%s) does not match the applicant name (%s)"
                        % (fields.get(field), slots.get(check["slot"])),
                        {"document_name": fields.get(field), "applicant_name": slots.get(check["slot"])})]
    return []


def check_covers_days(check, fields, slots, today):
    start = _parse_date(fields.get(check["start_field"]))
    end = _parse_date(fields.get(check["end_field"]))
    min_days = int(check.get("min_days", 0))
    if start is None or end is None:
        return []
    covered = (end - start).days
    if covered < min_days:
        verb = "are commonly expected" if check.get("advisory") else "are required"
        return [Failure("covers_days", check["start_field"],
                        "the statement covers %d days (%s to %s); %d consecutive days %s"
                        % (covered, start, end, min_days, verb),
                        {"covered_days": covered, "required_days": min_days,
                         "period_start": str(start), "period_end": str(end)})]
    return []


def check_recent_within_days(check, fields, slots, today):
    value = _parse_date(fields.get(check["field"]))
    max_age = int(check.get("max_age_days", 0))
    if value is None or max_age <= 0:
        return []
    age = (today - value).days
    if age > max_age:
        verb = "is usually within" if check.get("advisory") else "must be within"
        return [Failure("recent_within_days", check["field"],
                        "the document is dated %s, which is %d days old; the expectation %s %d days"
                        % (value, age, verb, max_age),
                        {"document_date": str(value), "age_days": age, "max_age_days": max_age})]
    return []


def check_date_after(check, fields, slots, today):
    value = _parse_date(fields.get(check["field"]))
    if value is None:
        return []
    if "relative_to_slot" in check:
        ref = _parse_date(slots.get(check["relative_to_slot"]))
        ref_label = check["relative_to_slot"].replace("_", " ")
    else:
        ref = _parse_date(fields.get(check["relative_to_field"]))
        ref_label = check["relative_to_field"].replace("_", " ")
    if ref is None:
        return []
    if not value > ref:
        return [Failure("date_after", check["field"],
                        "%s (%s) is not after %s (%s)"
                        % (check["field"].replace("_", " "), value, ref_label, ref),
                        {"value": str(value), "reference": str(ref)})]
    return []


def check_date_before(check, fields, slots, today):
    value = _parse_date(fields.get(check["field"]))
    if "relative_to_slot" in check:
        ref = _parse_date(slots.get(check["relative_to_slot"]))
        ref_label = check["relative_to_slot"].replace("_", " ")
    else:
        ref = _parse_date(fields.get(check["relative_to_field"]))
        ref_label = check["relative_to_field"].replace("_", " ")
    if value is None or ref is None:
        return []
    if not value < ref:
        return [Failure("date_before", check["field"],
                        "%s (%s) is not before %s (%s)"
                        % (check["field"].replace("_", " "), value, ref_label, ref),
                        {"value": str(value), "reference": str(ref)})]
    return []


def check_date_matches_slot(check, fields, slots, today):
    value = _parse_date(fields.get(check["field"]))
    ref = _parse_date(slots.get(check["slot"]))
    tol = int(check.get("tolerance_days", 0))
    if value is None or ref is None:
        return []
    drift = abs((value - ref).days)
    if drift > tol:
        return [Failure("date_matches_slot", check["field"],
                        "%s on the document (%s) differs from the stated %s (%s) by %d days"
                        % (check["field"].replace("_", " "), value,
                           check["slot"].replace("_", " "), ref, drift),
                        {"document_value": str(value), "stated_value": str(ref),
                         "drift_days": drift, "tolerance_days": tol})]
    return []


def check_cross_document_consistency(check, fields, slots, today):
    """Compare a field against the same fact on another document.

    Inconsistency between the applicant's and sponsor's accounts is an explicit
    caseworker concern, so we surface it rather than quietly picking one value.
    """
    others = (check.get("_other_fields") or {})
    mine = _normalise_name(fields.get(check["field"]))
    theirs = _normalise_name(others.get(check.get("other_field")))
    if not mine or not theirs:
        return []
    mine_parts, theirs_parts = set(mine.split()), set(theirs.split())
    if not mine_parts.issubset(theirs_parts) and not theirs_parts.issubset(mine_parts):
        return [Failure("cross_document_consistency", check["field"],
                        "%s differs between documents: %r here, %r on %s"
                        % (check["field"].replace("_", " "), fields.get(check["field"]),
                           others.get(check.get("other_field")), check.get("other_evidence")),
                        {"this_value": fields.get(check["field"]),
                         "other_value": others.get(check.get("other_field")),
                         "other_evidence": check.get("other_evidence")})]
    return []


REGISTRY = {
    "date_matches_slot": check_date_matches_slot,
    "cross_document_consistency": check_cross_document_consistency,
    "field_present": check_field_present,
    "name_matches": check_name_matches,
    "covers_days": check_covers_days,
    "recent_within_days": check_recent_within_days,
    "date_after": check_date_after,
    "date_before": check_date_before,
}


def run_checks(checks, fields, slots, today=None):
    # type: (List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], Optional[date]) -> List[Failure]
    today = today or date.today()
    failures = []
    for check in checks:
        kind = check.get("kind")
        fn = REGISTRY.get(kind)
        if fn is None:
            raise UnknownCheck(
                "config references check %r with no implementation; refusing to "
                "report this item as passing" % kind)
        produced = fn(check, fields, slots, today)
        for f in produced:
            # Severity and provenance are properties of the configured rule, not of
            # the check implementation, so they are stamped on here in one place.
            f.advisory = bool(check.get("advisory", False))
            f.note = check.get("note")
            f.source = check.get("source")
        failures.extend(produced)
    return failures


def blocking(failures):
    return [f for f in failures if not f.advisory]


def advisories(failures):
    return [f for f in failures if f.advisory]
