"""Whole-case analysis layer.

This sits between document QC and human review. It is deliberately not a visa
outcome engine: it prepares evidence-backed observations and follow-up questions
for an adviser. Rules compute facts, an optional model may propose observations,
and code validates every reference before anything reaches the review pack.
"""
from typing import Any, Dict, List, Optional

import diagnose

ALLOWED_LIMBS = {
    "will_leave",
    "not_live_in_uk",
    "permitted_purpose",
    "funds",
    "sponsor_consistency",
    "travel_history",
}

FORBIDDEN_TERMS = (
    "approved",
    "refused",
    "guaranteed",
    "probability",
    "chance",
    "likely to succeed",
    "unlikely to succeed",
    "sufficient",
    "insufficient",
    "meets the requirements",
    "does not meet the requirements",
)


def build_fact_context(checklist, case):
    # type: (Any, Any) -> Dict[str, Any]
    facts = {}
    for slot_id, value in (case.slots or {}).items():
        facts["intake.%s" % slot_id] = value
    stay_days = diagnose.stay_length_days(case.slots)
    if stay_days is not None:
        facts["intake.trip_length_days"] = stay_days
    funds = diagnose.funds_picture(case)
    if funds:
        facts["computed.funds_difference_gbp"] = funds["difference"]
    ties = diagnose.home_tie_coverage(checklist, case)
    facts["computed.home_tie_coverage_count"] = ties["count"]
    for ev_id, rec in (case.evidence or {}).items():
        fields = rec.get("fields") or {}
        for field, value in fields.items():
            facts["%s.%s" % (ev_id, field)] = value
    return facts


def analyse(checklist, case, model=None, limit=5):
    # type: (Any, Any, Optional[Any], int) -> Dict[str, Any]
    facts = build_fact_context(checklist, case)
    candidates = []
    candidate_source = "deterministic_fallback"
    model_error = None
    if model is not None and hasattr(model, "analyse_case"):
        try:
            model_candidates = model.analyse_case({
                "facts": facts,
                "allowed_limbs": sorted(ALLOWED_LIMBS),
            }) or []
            if model_candidates:
                candidates = model_candidates
                candidate_source = "model"
        except Exception as exc:
            model_error = str(exc)
            candidates = []
    if not candidates:
        candidates = deterministic_candidates(checklist, case, facts)

    accepted, rejected = [], []
    for candidate in candidates:
        ok, reason = validate_observation(candidate, facts)
        if ok:
            accepted.append(normalise_observation(candidate))
        else:
            rejected.append({"candidate": candidate, "reason": reason})
        if len(accepted) >= limit:
            break
    return {
        "facts": facts,
        "observations": accepted,
        "rejected": rejected,
        "limits": [
            "Whole-case analysis surfaces evidence-backed questions for adviser review.",
            "It does not predict an outcome, score the case, or decide sufficiency.",
            "Each observation must cite facts already present in the case record.",
        ],
        "candidate_source": candidate_source,
        "model_error": model_error,
    }


def deterministic_candidates(checklist, case, facts):
    # type: (Any, Any, Dict[str, Any]) -> List[Dict[str, Any]]
    out = []
    stay_days = facts.get("intake.trip_length_days")
    if stay_days and stay_days >= 60:
        out.append({
            "limb": "not_live_in_uk",
            "observation": (
                "The planned visit is long for a visitor case, so the adviser should "
                "check whether the explanation, work or business arrangements, "
                "accommodation and budget tell a consistent story."
            ),
            "evidence_refs": [{"source": "intake.trip_length_days", "value": stay_days}],
            "missing_context": "Reason for the long stay and how commitments continue during the visit.",
            "question": (
                "Can you explain why this visit needs to last this long, and what "
                "work, business or family commitments continue while you are away?"
            ),
        })

    cost = facts.get("intake.estimated_trip_cost_gbp")
    balance = facts.get("bank_statements.closing_balance")
    try:
        cost_f = float(cost) if cost is not None else None
        balance_f = float(str(balance).replace(",", "")) if balance is not None else None
    except (TypeError, ValueError):
        cost_f, balance_f = None, None
    if cost_f is not None and balance_f is not None and balance_f <= cost_f * 1.5:
        out.append({
            "limb": "funds",
            "observation": (
                "The evidenced closing balance is close to the declared trip cost. "
                "There is no fixed minimum balance, but the adviser may need context "
                "on regular income, existing commitments and any unusual deposits."
            ),
            "evidence_refs": [
                {"source": "intake.estimated_trip_cost_gbp", "value": cost},
                {"source": "bank_statements.closing_balance", "value": balance},
            ],
            "missing_context": "Regular income, fixed costs and source of any large recent deposits.",
            "question": (
                "Can you explain your regular monthly income and major fixed costs, "
                "and identify any large recent deposits in the bank statement?"
            ),
        })

    if facts.get("intake.has_uk_settled_relative") is True:
        out.append({
            "limb": "will_leave",
            "observation": (
                "The case involves a settled relative in the UK, so the adviser "
                "should check that home-country ties and the purpose of visit are "
                "clearly evidenced without making a sufficiency judgement."
            ),
            "evidence_refs": [
                {"source": "intake.has_uk_settled_relative", "value": True},
                {"source": "computed.home_tie_coverage_count",
                 "value": facts.get("computed.home_tie_coverage_count")},
            ],
            "missing_context": "How home-country work, business, property, family or study commitments continue.",
            "question": (
                "Which home-country commitments are most important to highlight, "
                "and can you provide documents that evidence them clearly?"
            ),
        })
    return out


def validate_observation(candidate, facts):
    # type: (Dict[str, Any], Dict[str, Any]) -> (bool, str)
    if candidate.get("limb") not in ALLOWED_LIMBS:
        return False, "unknown limb"
    text = " ".join(str(candidate.get(key) or "") for key in (
        "observation", "missing_context", "question"))
    lowered = text.lower()
    for term in FORBIDDEN_TERMS:
        if term in lowered:
            return False, "forbidden outcome or sufficiency language: %s" % term
    refs = candidate.get("evidence_refs") or []
    if not refs:
        return False, "missing evidence_refs"
    for ref in refs:
        source = ref.get("source")
        if source not in facts:
            return False, "unknown evidence ref: %s" % source
        if not _same_value(ref.get("value"), facts[source]):
            return False, "evidence ref value mismatch: %s" % source
    if not candidate.get("question"):
        return False, "missing follow-up question"
    return True, ""


def normalise_observation(candidate):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    return {
        "limb": candidate["limb"],
        "observation": " ".join(str(candidate.get("observation") or "").split()),
        "evidence_refs": list(candidate.get("evidence_refs") or []),
        "missing_context": " ".join(str(candidate.get("missing_context") or "").split()),
        "question": " ".join(str(candidate.get("question") or "").split()),
    }


def _same_value(left, right):
    if left == right:
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    try:
        return float(str(left).replace(",", "")) == float(str(right).replace(",", ""))
    except (TypeError, ValueError):
        return str(left) == str(right)
