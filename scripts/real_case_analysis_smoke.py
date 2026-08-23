#!/usr/bin/env python3
"""Run the whole-case analysis layer through the real LLM adapter.

Requires the same OpenAI-compatible env used by the live PoC, plus
VISA_AGENT_CASE_ANALYSIS_LLM=1.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import case_analysis
import checklist as checklist_mod
import email_model
import store as store_mod


SLOTS = {
    "applicant_name": "Mei Ling Chen",
    "nationality": "Chinese",
    "trip_start": "2026-10-05",
    "trip_end": "2027-01-03",
    "visit_purpose": "family_visit",
    "has_uk_settled_relative": True,
    "employment_status": "self_employed",
    "third_party_funding": False,
    "prior_uk_refusal": False,
    "estimated_trip_cost_gbp": 4200.0,
}

EVIDENCE = {
    "passport": {"fields": {
        "holder_name": "Mei Ling Chen",
        "passport_number": "EK1234567",
        "expiry_date": "2029-04-30",
        "prior_compliant_travel": True,
    }, "failures": []},
    "bank_statements": {"fields": {
        "account_holder_name": "Mei Ling Chen",
        "period_start": "2026-02-10",
        "period_end": "2026-08-18",
        "closing_balance": "5100.00",
        "currency": "GBP",
    }, "failures": []},
    "travel_itinerary": {"fields": {
        "outbound_date": "2026-10-05",
        "return_date": "2027-01-03",
        "passenger_name": "Mei Ling Chen",
    }, "failures": []},
    "accommodation_proof": {"fields": {
        "address": "14 Bramhall Road, Manchester M20 3QT",
    }, "failures": []},
    "sponsor_invitation_letter": {"fields": {
        "sponsor_name": "Hui Chen",
        "sponsor_address": "14 Bramhall Road, Manchester M20 3QT",
        "relationship": "sister",
    }, "failures": []},
    "sponsor_status_proof": {"fields": {
        "sponsor_name": "Hui Chen",
        "status_type": "Indefinite Leave to Remain",
    }, "failures": []},
    "self_employment_evidence": {"fields": {
        "business_name": "Chen Design Studio",
        "registration_id": "91310115MA1K3",
        "tax_year": "2025",
        "declared_income": "38000",
        "business_statement_period_start": "2026-02-10",
        "business_statement_period_end": "2026-08-18",
    }, "failures": []},
    "home_ties_evidence": {"fields": {
        "tie_types": "apartment mortgage; elderly mother as dependant",
    }, "failures": []},
}


def main():
    checklist = checklist_mod.load_route("visitor_family_visit")
    store = store_mod.Store()
    case = store.create_case("llm-smoke", checklist.route_id)
    case.slots = dict(SLOTS)
    case.evidence = dict(EVIDENCE)
    store.save_case(case)
    case = store.get_case("llm-smoke")

    model = email_model.EmailDemoModel()
    if model.case_analysis_client is None:
        raise SystemExit(
            "Set VISA_AGENT_CASE_ANALYSIS_LLM=1 and VISA_AGENT_LLM_API_KEY or DEEPSEEK_API_KEY")

    analysis = case_analysis.analyse(checklist, case, model=model)
    print(json.dumps({
        "provider": getattr(model.case_analysis_client, "base_url", None),
        "model": getattr(model.case_analysis_client, "model_name", None),
        "accepted_count": len(analysis["observations"]),
        "analysis_dimensions": analysis["analysis_dimensions"],
        "candidate_source": analysis["candidate_source"],
        "model_error": analysis["model_error"],
        "rejected_count": len(analysis["rejected"]),
        "observations": analysis["observations"],
        "rejected_reasons": [item["reason"] for item in analysis["rejected"]],
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
