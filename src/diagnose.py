"""Risk diagnosis.

Turns a case profile into a list of observations with remediation questions.

What this module deliberately does NOT do: score, rank by severity, estimate
approval odds, or say whether evidence is sufficient. The genuine visitor test has
no hard standard, so a sufficiency verdict would be both unverifiable and, if
wrong, extremely costly to the client. Coverage is reported; strength is a human
judgement. (Research §5.2 defence 6.)
"""
from datetime import date
from typing import Dict, Any, List, Optional

from validate import _parse_date


def stay_length_days(slots):
    # type: (Dict[str, Any]) -> Optional[int]
    start = _parse_date(slots.get("trip_start"))
    end = _parse_date(slots.get("trip_end"))
    if start is None or end is None:
        return None
    return (end - start).days


def home_tie_coverage(checklist, case):
    # type: (Any, Any) -> Dict[str, Any]
    """Which home-tie categories have at least one passing piece of evidence.

    Counts only evidence that actually passed validation -- an uploaded but failing
    document does not establish a tie.
    """
    covered, uncovered = [], []
    for tie in checklist.home_ties():
        hit = None
        for ev_id in tie.get("satisfied_by", []):
            claim = _normalise_claim(ev_id)
            rec = case.evidence.get(claim["evidence"])
            if rec and not rec.get("failures") and _claim_supported(claim, rec.get("fields") or {}):
                hit = claim["evidence"]
                break
        if hit:
            covered.append({"id": tie["id"], "label": tie["label"], "evidence": hit})
        else:
            uncovered.append({"id": tie["id"], "label": tie["label"]})
    return {"covered": covered, "uncovered": uncovered, "count": len(covered)}


def _normalise_claim(raw):
    if isinstance(raw, str):
        return {"evidence": raw}
    return dict(raw)


def _claim_supported(claim, fields):
    required_fields = claim.get("fields") or []
    for field in required_fields:
        value = fields.get(field)
        if value is None or str(value).strip() == "":
            return False

    field = claim.get("field")
    if "equals" in claim:
        return fields.get(field) == claim["equals"]
    if "contains_any" in claim:
        value = str(fields.get(field) or "").lower()
        return any(str(token).lower() in value for token in claim["contains_any"])
    if field:
        value = fields.get(field)
        return value is not None and str(value).strip() != ""
    return True


COMPUTED = {
    "stay_length_days": lambda cl, case: stay_length_days(case.slots),
    "home_tie_coverage": lambda cl, case: home_tie_coverage(cl, case)["count"],
}


def _trigger_fires(trigger, checklist, case):
    # type: (Dict[str, Any], Any, Any) -> bool
    if "computed" in trigger:
        fn = COMPUTED.get(trigger["computed"])
        if fn is None:
            raise KeyError("config references unknown computed value %r" % trigger["computed"])
        value = fn(checklist, case)
        if value is None:
            return False
        if "gt" in trigger:
            return value > trigger["gt"]
        if "lt" in trigger:
            return value < trigger["lt"]
        if "gte" in trigger:
            return value >= trigger["gte"]
        if "lte" in trigger:
            return value <= trigger["lte"]
        return bool(value)

    slot_value = case.slots.get(trigger.get("slot"))
    if slot_value is None:
        return False
    if "equals" in trigger:
        return slot_value == trigger["equals"]
    if "in" in trigger:
        return slot_value in trigger["in"]
    return bool(slot_value)


def active_risks(checklist, case):
    # type: (Any, Any) -> List[Dict[str, Any]]
    """Observations that currently apply to this case, in config order."""
    out = []
    for rf in checklist.data.get("risk_factors", []):
        try:
            fires = _trigger_fires(rf.get("trigger", {}), checklist, case)
        except KeyError:
            # An unimplemented trigger must not silently evaluate to "no risk".
            raise
        if fires:
            out.append({
                "id": rf["id"],
                "label": rf["label"],
                "observation": " ".join(rf["observation"].split()),
                "ask": " ".join(rf["ask"].split()),
                "relates_to": rf.get("relates_to", []),
                "source": rf.get("source"),
            })
    return out


def funds_picture(case):
    # type: (Any) -> Optional[Dict[str, Any]]
    """Side-by-side of declared trip cost and evidenced balance.

    Returns the comparison as data. There is no statutory minimum for visitors
    (research §2.4), so this deliberately produces no verdict -- the numbers are
    put in front of a human, who decides.
    """
    cost = case.slots.get("estimated_trip_cost_gbp")
    rec = case.evidence.get("bank_statements") or {}
    balance = (rec.get("fields") or {}).get("closing_balance")
    if cost is None or balance is None:
        return None
    try:
        cost_f, bal_f = float(cost), float(str(balance).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return {
        "estimated_trip_cost_gbp": cost_f,
        "evidenced_balance": bal_f,
        "currency": (rec.get("fields") or {}).get("currency"),
        "difference": round(bal_f - cost_f, 2),
        "note": ("No minimum balance is set in the Immigration Rules for visitors. "
                 "These figures are presented for the adviser to weigh against the "
                 "applicant's income and existing commitments."),
    }
