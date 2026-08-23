"""Whole-case analysis layer.

This sits between document QC and human review. It is deliberately not a visa
outcome engine: it prepares evidence-backed observations and follow-up questions
for an adviser. Rules compute facts, an optional model may propose observations,
and code validates every reference before anything reaches the review pack.
"""
import os
import re
from typing import Any, Dict, List, Optional

import diagnose
import yaml

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

_RUBRIC = None


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


def load_rubric(path=None):
    global _RUBRIC
    use_cache = path is None
    if use_cache and _RUBRIC is not None:
        return _RUBRIC
    if path is None:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "config", "case_analysis_rubric.yaml")
    with open(path) as fh:
        rubric = yaml.safe_load(fh) or {}
    if use_cache:
        _RUBRIC = rubric
    return rubric


def analysis_dimensions(rubric=None):
    return (rubric or load_rubric()).get("dimensions", [])


def analyse(checklist, case, model=None, limit=None, application_date=None, rubric=None):
    # type: (Any, Any, Optional[Any], Optional[int], Optional[Any], Optional[Dict[str, Any]]) -> Dict[str, Any]
    rubric = rubric or load_rubric()
    limit = limit or int((rubric.get("meta") or {}).get("max_observations", 5))
    facts = build_fact_context(checklist, case)
    if application_date is not None:
        facts["computed.application_date"] = str(application_date)
    candidates = []
    candidate_source = "deterministic_fallback"
    model_error = None
    if model is not None and hasattr(model, "analyse_case"):
        try:
            model_candidates = model.analyse_case({
                "facts": facts,
                "allowed_limbs": sorted(ALLOWED_LIMBS),
                "rubric": rubric,
                "analysis_dimensions": analysis_dimensions(rubric),
                "global_rules": rubric.get("global_rules", []),
                "allowed_question_actions": rubric.get("allowed_question_actions", []),
                "prohibited_question_actions": rubric.get("prohibited_question_actions", []),
                "output_contract": rubric.get("output_contract", {}),
                "time_basis": (rubric.get("meta") or {}).get("time_basis"),
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
        ok, reason = validate_observation(candidate, facts, rubric=rubric)
        if ok:
            accepted.append(normalise_observation(candidate))
        else:
            rejected.append({"candidate": candidate, "reason": reason})
        if len(accepted) >= limit:
            break
    return {
        "facts": facts,
        "observations": accepted,
        "follow_up_questions": follow_up_questions(accepted),
        "rejected": rejected,
        "limits": [
            "Whole-case analysis surfaces evidence-backed observations and optional questions for adviser review.",
            "It does not predict an outcome, score the case, or decide sufficiency.",
            "Each observation must cite facts already present in the case record.",
        ],
        "candidate_source": candidate_source,
        "model_error": model_error,
        "analysis_dimensions": analysis_dimensions(rubric),
        "rubric_meta": rubric.get("meta", {}),
    }


def deterministic_candidates(checklist, case, facts):
    # type: (Any, Any, Dict[str, Any]) -> List[Dict[str, Any]]
    out = []
    stay_days = facts.get("intake.trip_length_days")
    if stay_days and stay_days >= 60:
        out.append({
            "dimension_id": "purpose_duration_and_activities",
            "limb": "not_live_in_uk",
            "observation_type": "purpose_duration_alignment",
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
            "source_refs": ["appendix_v:V_4_2_b_to_d", "caseworker_guidance:7_2"],
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
            "dimension_id": "financial_resources_and_trip_cost",
            "limb": "funds",
            "observation_type": "missing_context",
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
            "source_refs": ["appendix_v:V_4_2_e", "caseworker_guidance:8_1"],
        })

    if facts.get("intake.has_uk_settled_relative") is True:
        out.append({
            "dimension_id": "personal_circumstances_and_will_leave",
            "limb": "will_leave",
            "observation_type": "missing_context",
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
            "source_refs": ["appendix_v:V_4_2_a", "caseworker_guidance:7_2"],
        })
    return out


def validate_observation(candidate, facts, rubric=None):
    # type: (Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]) -> (bool, str)
    rubric = rubric or load_rubric()
    dimensions = dict((dim.get("id"), dim) for dim in analysis_dimensions(rubric))
    output_contract = rubric.get("output_contract", {})

    dimension_id = candidate.get("dimension_id")
    if dimension_id not in dimensions:
        return False, "unknown dimension_id: %s" % dimension_id
    dimension = dimensions[dimension_id]

    if candidate.get("limb") not in ALLOWED_LIMBS:
        return False, "unknown limb"
    if candidate.get("limb") not in (dimension.get("legal_limbs") or []):
        return False, "limb not allowed for dimension: %s" % dimension_id

    allowed_types = output_contract.get("allowed_observation_types", [])
    if candidate.get("observation_type") not in allowed_types:
        return False, "unknown observation_type: %s" % candidate.get("observation_type")

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
    source_refs = candidate.get("source_refs") or []
    if not source_refs:
        return False, "missing source_refs"
    allowed_source_refs = set(_source_ref_strings(dimension.get("source_refs") or []))
    if not any(str(ref) in allowed_source_refs for ref in source_refs):
        return False, "source_ref not allowed for dimension: %s" % dimension_id
    if candidate.get("question"):
        bad_action = _prohibited_question_action(candidate.get("question", ""), rubric)
        if bad_action:
            return False, "prohibited question action: %s" % bad_action
        bad_window = _unsupported_time_window(candidate.get("question", ""), candidate, dimension)
        if bad_window:
            return False, "unsupported time window: %s" % bad_window
    return True, ""


def normalise_observation(candidate):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    return {
        "dimension_id": candidate["dimension_id"],
        "limb": candidate["limb"],
        "observation_type": candidate["observation_type"],
        "observation": " ".join(str(candidate.get("observation") or "").split()),
        "evidence_refs": list(candidate.get("evidence_refs") or []),
        "missing_context": " ".join(str(candidate.get("missing_context") or "").split()),
        "question": " ".join(str(candidate.get("question") or "").split()),
        "source_refs": list(candidate.get("source_refs") or []),
    }


def follow_up_questions(observations):
    questions = []
    for item in observations:
        question = item.get("question")
        if not question:
            continue
        questions.append({
            "dimension_id": item.get("dimension_id"),
            "limb": item.get("limb"),
            "question": question,
            "evidence_refs": list(item.get("evidence_refs") or []),
            "source_refs": list(item.get("source_refs") or []),
        })
    return questions


def _source_ref_strings(raw_refs):
    out = []
    for ref in raw_refs:
        if isinstance(ref, dict):
            for key, value in ref.items():
                out.append("%s:%s" % (key, value))
        else:
            out.append(str(ref))
    return out


def _prohibited_question_action(question, rubric):
    lowered = str(question or "").lower()
    checks = [
        ("future-dated bank statement", ["future", "bank statement"]),
        ("future-month statement", ["future month", "statement"]),
        ("near-departure statement", ["statement", "departure"]),
        ("close-to-travel statement", ["statement", "close to", "travel"]),
        ("31-day visitor rule", ["31 day"]),
        ("six-month mandatory statement rule", ["six months", "must"]),
        ("minimum balance", ["minimum balance"]),
        ("guarantee outcome", ["guarantee"]),
    ]
    for label, tokens in checks:
        if all(token in lowered for token in tokens):
            return label
    for action in rubric.get("prohibited_question_actions", []):
        if "future-dated bank statement" in action and (
                "future-dated bank statement" in lowered or
                ("future" in lowered and "statement" in lowered)):
            return action
        if "future month" in action and "future month" in lowered:
            return action
        if "close to the travel date" in action and (
                "close to the travel date" in lowered or
                "near departure" in lowered):
            return action
    return None


def _unsupported_time_window(question, candidate, dimension):
    cited_values = set()
    for ref in candidate.get("evidence_refs") or []:
        cited_values.add(str(ref.get("value")).lower())
    allowed_windows = set(_allowed_time_windows(dimension))
    for window in _time_windows(question):
        if window["text"] in allowed_windows:
            continue
        if window["number"] in cited_values:
            continue
        return window["text"]
    return None


def _allowed_time_windows(dimension):
    out = []
    if dimension.get("id") == "travel_and_immigration_pattern":
        out.extend(["last 12 months", "past 12 months", "previous 12 months"])
    if dimension.get("id") == "financial_resources_and_trip_cost":
        out.append("3 months")
    return out


def _time_windows(question):
    lowered = str(question or "").lower()
    numbers = {
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
        "eleven": "11", "twelve": "12",
    }
    pattern = re.compile(
        r"\b(last|past|previous|within|next|future)\s+"
        r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
        r"(day|days|week|weeks|month|months|year|years)\b")
    out = []
    for match in pattern.finditer(lowered):
        number = numbers.get(match.group(2), match.group(2))
        unit = match.group(3)
        if not unit.endswith("s"):
            unit = unit + "s"
        out.append({
            "text": "%s %s %s" % (match.group(1), number, unit),
            "number": number,
        })
    before_pattern = re.compile(
        r"\bin the\s+"
        r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
        r"(day|days|week|weeks|month|months|year|years)\s+before\b")
    for match in before_pattern.finditer(lowered):
        number = numbers.get(match.group(1), match.group(1))
        unit = match.group(2)
        if not unit.endswith("s"):
            unit = unit + "s"
        out.append({
            "text": "previous %s %s" % (number, unit),
            "number": number,
        })
    return out


def _same_value(left, right):
    if left == right:
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        return _as_bool(left) is _as_bool(right)
    try:
        return float(str(left).replace(",", "")) == float(str(right).replace(",", ""))
    except (TypeError, ValueError):
        return str(left) == str(right)


def _as_bool(value):
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return object()
