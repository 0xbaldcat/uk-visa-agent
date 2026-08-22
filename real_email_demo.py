#!/usr/bin/env python3
"""Send one real PoC email through SMTP and optionally list unseen replies.

Run only after setting the VISA_AGENT_* environment variables documented in
README.md. The full workflow remains in demo.py; this script proves the live
mailbox can send the same four deliverables and inspect inbound messages.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import checklist as checklist_mod
import deliver
import real_email
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
        "holder_name": "Mei Ling Chen", "passport_number": "EK1234567",
        "expiry_date": "2029-04-30", "prior_compliant_travel": True}, "failures": []},
    "bank_statements": {"fields": {
        "account_holder_name": "Mei Ling Chen", "period_start": "2026-02-10",
        "period_end": "2026-08-18", "closing_balance": "5100.00", "currency": "GBP"},
        "failures": []},
    "travel_itinerary": {"fields": {
        "outbound_date": "2026-10-05", "return_date": "2027-01-03",
        "passenger_name": "Mei Ling Chen"}, "failures": []},
    "accommodation_proof": {"fields": {"address": "14 Bramhall Road"}, "failures": []},
    "sponsor_invitation_letter": {"fields": {
        "sponsor_name": "Hui Chen", "sponsor_address": "14 Bramhall Road",
        "relationship": "sister"}, "failures": []},
    "sponsor_status_proof": {"fields": {
        "sponsor_name": "Hui Chen", "status_type": "Indefinite Leave to Remain"},
        "failures": []},
    "self_employment_evidence": {"fields": {
        "business_name": "Chen Design Studio", "registration_id": "91310115MA1K3",
        "tax_year": "2025", "declared_income": "38000",
        "business_statement_period_start": "2026-02-10",
        "business_statement_period_end": "2026-08-18"}, "failures": []},
    "home_ties_evidence": {"fields": {
        "tie_types": "apartment mortgage; elderly mother as dependant"}, "failures": []},
}


def main():
    settings = real_email.EmailSettings.from_env()
    cl = checklist_mod.load_route("visitor_family_visit")
    st = store_mod.Store()
    case = st.create_case("real-email-smoke", cl.route_id)
    case.slots = dict(SLOTS)
    case.evidence = dict(EVIDENCE)
    st.save_case(case)

    pack = deliver.build_pack(cl, case, today=date(2026, 8, 22))
    attachments = [
        {"filename": "personalised-checklist.json", "content_type": "application/json",
         "content": pack["document_checklist"]},
        {"filename": "form-answers-draft.json", "content_type": "application/json",
         "content": pack["form_answers"]},
        {"filename": "cover-letter-draft.json", "content_type": "application/json",
         "content": pack["cover_letter"]},
        {"filename": "quality-check-report.json", "content_type": "application/json",
         "content": pack["qc_report"]},
    ]
    channel = real_email.RealEmailChannel(settings)
    sent = channel.send(
        "real-email-smoke",
        "This is the live email smoke test for the UK visitor visa PoC.",
        kind="deliver_pack",
        attachments=attachments)
    print("sent:", sent["subject"])
    print("attachments:", ", ".join(a["filename"] for a in sent["attachments"]))

    if os.environ.get("VISA_AGENT_FETCH_UNSEEN") == "1":
        for msg in real_email.fetch_unseen(settings, from_addr=settings.to_addr):
            print("unseen:", msg["from"], msg["subject"], len(msg["attachments"]))


if __name__ == "__main__":
    main()
