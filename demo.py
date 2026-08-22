#!/usr/bin/env python3
"""End-to-end demo: the deliberately difficult client from the research doc.

Freelancer, funds only just adequate, sister settled in Manchester, wants to stay
three months. The point is that the agent surfaces the risk cluster and asks for
strengthening evidence rather than just collecting what it was handed.

Runs offline and deterministically -- the model seam is stubbed.
"""
import json
import os
import sys
import argparse
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import channels
import checklist as checklist_mod
import deliver
import diagnose
import engine as engine_mod
import facts
import llm
import store as store_mod

TODAY = date(2026, 8, 22)
NOW = datetime(2026, 8, 22, 10, 0, 0)

CLIENT_REPLIES = {
    "applicant_name": "Mei Ling Chen",
    "nationality": "Chinese",
    "trip_start": "2026-10-05",
    "trip_end": "2027-01-03",          # ~90 days: trips the long_stay risk
    "visit_purpose": "family_visit",
    "has_uk_settled_relative": "yes",
    "employment_status": "self_employed",
    "third_party_funding": "no",
    "prior_uk_refusal": "no",
    "estimated_trip_cost_gbp": "4200",
}

DOCUMENTS = {
    "passport.jpg": {
        "holder_name": "Mei Ling Chen", "passport_number": "EK1234567",
        "expiry_date": "2029-04-30", "nationality": "Chinese",
        "prior_compliant_travel": True,
    },
    # First attempt: only 2 months of statements. Advisory, not blocking.
    "statements-short.pdf": {
        "account_holder_name": "Mei Ling Chen", "period_start": "2026-06-01",
        "period_end": "2026-08-01", "closing_balance": "5100.00", "currency": "GBP",
    },
    # Second attempt after the agent pushes back.
    "statements-6mo.pdf": {
        "account_holder_name": "Mei Ling Chen", "period_start": "2026-02-10",
        "period_end": "2026-08-18", "closing_balance": "5100.00", "currency": "GBP",
    },
    # Wrong name -- a blocking failure the agent must catch.
    "itinerary-wrong.pdf": {
        "outbound_date": "2026-10-05", "return_date": "2027-01-03",
        "passenger_name": "M. L. Chen",
    },
    "itinerary.pdf": {
        "outbound_date": "2026-10-05", "return_date": "2027-01-03",
        "passenger_name": "Mei Ling Chen",
    },
    "accommodation.pdf": {
        "address": "14 Bramhall Road, Manchester M20 3QT",
        "host_name": "Hui Chen", "stay_start": "2026-10-05", "stay_end": "2027-01-03",
    },
    "invitation.pdf": {
        "sponsor_name": "Hui Chen", "sponsor_address": "14 Bramhall Road, Manchester M20 3QT",
        "relationship": "sister", "stay_start": "2026-10-05", "stay_end": "2027-01-03",
        "funding_offered": "accommodation only",
    },
    "sponsor-brp.jpg": {"sponsor_name": "Hui Chen", "status_type": "Indefinite Leave to Remain"},
    "business-reg.pdf": {
        "business_name": "Chen Design Studio", "registration_id": "91310115MA1K3",
        "tax_year": "2025", "declared_income": "38000",
        "business_statement_period_start": "2026-02-10",
        "business_statement_period_end": "2026-08-18",
    },
    "home-ties.pdf": {"tie_types": "apartment mortgage; elderly mother as dependant"},
}

NARRATIVE = {
    # Model-written prose. Every factual token here traces to client-supplied data,
    # so it passes the fabrication guard.
    "long_stay": ("The applicant plans to arrive on 2026-10-05 and depart on "
                  "2027-01-03. Her design business, Chen Design Studio, continues "
                  "to operate during this period."),
}

BANNER = "=" * 72


def hr(title):
    print("\n" + BANNER)
    print(title)
    print(BANNER)


def show(step, result):
    if result is None:
        print("\n[%s] duplicate ignored (no re-ask)" % step)
        return
    action = result["action"]
    sent = result["sent"] or {}
    via = sent.get("channel", "-")
    if sent.get("kind") == "template":
        via += " (template: window closed)"
    print("\n[%s] action=%s via=%s" % (step, action.kind, via))
    print("  agent> " + result["body"].replace("\n", "\n         "))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the UK visitor visa agent PoC demo.")
    parser.add_argument("--email-only", action="store_true",
                        help="route the whole conversation through email, without WhatsApp")
    args = parser.parse_args(argv)
    conversation_channel = "email" if args.email_only else "whatsapp"

    cl = checklist_mod.load_route("visitor_family_visit")
    st = store_mod.Store()
    wa = channels.WhatsAppChannel(approved_templates=["visa_docs_reminder"])
    em = channels.EmailChannel()
    router = channels.Router(
        wa, em,
        preferred_conversation_channel=("email" if args.email_only else "whatsapp"))
    model = llm.StubModel(replies=CLIENT_REPLIES, documents=DOCUMENTS,
                          paragraphs=NARRATIVE)
    eng = engine_mod.Engine(st, cl, model, router=router, today=TODAY)

    case = st.create_case("case-001", cl.route_id)
    if not args.email_only:
        wa.note_inbound("case-001", NOW)

    hr("1. INTAKE  -  slot by slot, order derived from state")
    n = 0
    while True:
        c = st.get_case("case-001")
        if cl.first_missing_slot(c.slots) is None:
            break
        n += 1
        res = eng.handle_reply(
            "case-001", "(client reply)", "%s:intake-%d" % (conversation_channel, n),
            channel=conversation_channel, now=NOW)
        show("intake %d" % n, res)
        if n > 20:
            break

    hr("2. DEDUPLICATION  -  a retried webhook must not re-ask")
    show("retry", eng.handle_reply(
        "case-001", "(client reply)", "%s:intake-1" % conversation_channel,
        channel=conversation_channel, now=NOW))

    hr("3. RISK DIAGNOSIS  -  observations, not verdicts")
    c = st.get_case("case-001")
    for r in diagnose.active_risks(cl, c):
        print("\n  * %s" % r["label"])
        print("    observed: %s" % r["observation"])
        print("    agent asks: %s" % r["ask"])
    print("\n  stay length: %d days" % diagnose.stay_length_days(c.slots))
    print("  NOTE: no score, no probability, no sufficiency verdict.")

    hr("4. COLLECTION  -  including a document that fails validation")
    first = [
        ("passport", "passport.jpg"),
        ("bank_statements", "statements-short.pdf"),     # advisory only: does not block
        ("travel_itinerary", "itinerary-wrong.pdf"),     # blocking: name mismatch
    ]
    for i, (ev_id, doc) in enumerate(first):
        res = eng.handle_document(
            "case-001", ev_id, doc, "%s:doc-%d" % (conversation_channel, i),
            channel=conversation_channel, now=NOW)
        show("upload %s" % doc, res)

    if args.email_only:
        hr("5. EMAIL-ONLY FALLBACK  -  no WhatsApp account required")
    else:
        hr("5. WHATSAPP WINDOW  -  30h later, chase must degrade to a template")
    later = NOW + timedelta(hours=30)
    res = eng.handle_reply(
        "case-001", "(silence, then a nudge)", "%s:late" % conversation_channel,
        channel=conversation_channel, now=later)
    show("late chase", res)

    hr("6. REMEDIATION AND THE REST OF COLLECTION")
    if not args.email_only:
        wa.note_inbound("case-001", later)       # client replies: window reopens
    rest = [
        ("travel_itinerary", "itinerary.pdf"),           # corrected
        ("accommodation_proof", "accommodation.pdf"),
        ("sponsor_invitation_letter", "invitation.pdf"),
        ("sponsor_status_proof", "sponsor-brp.jpg"),
        ("self_employment_evidence", "business-reg.pdf"),
        ("home_ties_evidence", "home-ties.pdf"),
    ]
    for i, (ev_id, doc) in enumerate(rest):
        res = eng.handle_document(
            "case-001", ev_id, doc, "%s:doc2-%d" % (conversation_channel, i),
            channel=conversation_channel, now=later)
        show("upload %s" % doc, res)

    hr("7. QC REPORT  -  computed, not asserted")
    c = st.get_case("case-001")
    report = deliver.qc_report(cl, c, today=TODAY)
    print("\n  pack_complete      : %s" % report["pack_complete"])
    print("  accepted           : %s" % ", ".join(report["accepted"]))
    print("  outstanding        : %s" % (", ".join(report["outstanding"]) or "none"))
    print("  needs_replacement  : %s" % (", ".join(report["needs_replacement"]) or "none"))
    print("  unsourced rules    : %s" % (", ".join(report["unsourced_rules"]) or "none"))
    for a in report["advisories"]:
        print("  advisory           : %s (%s)" % (a["message"], a["note"]))
    print("  home ties covered  : %d of %d"
          % (report["home_tie_coverage"]["count"],
             len(report["home_tie_coverage"]["covered"]) + len(report["home_tie_coverage"]["uncovered"])))
    fp = report["funds_picture"]
    if fp:
        print("  funds              : trip approx %s vs evidenced balance %s"
              % (fp["estimated_trip_cost_gbp"], fp["evidenced_balance"]))
        print("                       %s" % fp["note"])

    hr("8. FABRICATION GUARD  -  the control that is not a prompt")
    ledger = facts.FactLedger()
    ledger.add_case(c)
    print("\n  ledger holds %d client-supplied values" % len(ledger))
    good = NARRATIVE["long_stay"]
    print("\n  model prose using only client facts:")
    print("    %s" % good)
    print("    -> unsupported tokens: %s" % (facts.verify(good, ledger) or "none"))
    bad = ("The applicant has held a balance of 25000.00 since 2024-01-01 and earns "
           "120000 annually.")
    print("\n  model prose that embellishes:")
    print("    %s" % bad)
    print("    -> unsupported tokens: %s" % facts.verify(bad, ledger))
    try:
        facts.enforce(bad, ledger, where="cover letter")
    except facts.FabricationError as exc:
        print("    -> BLOCKED: %s" % exc)

    hr("9. DELIVERY PACK  -  four deliverables, code-rendered")
    pack = deliver.build_pack(cl, c, narrative=NARRATIVE, today=TODAY)
    print("\n  -- personalised checklist --")
    for item in pack["document_checklist"]["items"]:
        print("   [%-18s] %s" % (item["status"], item["label"]))
        print("        why: %s" % item["why"])
        print("        src: %s" % item["source"])
    print("\n  -- cover letter sections --")
    for s in pack["cover_letter"]["sections"]:
        tag = "model-drafted" if s["generated"] else "code-rendered"
        print("   [%s] %s" % (tag, s["heading"]))
        print("        %s" % s["body"])
    print("\n   disclaimer: %s" % pack["cover_letter"]["disclaimer"])
    print("\n  -- form answers --")
    for row in pack["form_answers"]["rows"]:
        print("   %-38s %s" % (row["question"] + ":", row["answer"]))
    delivered = em.sink[-1]
    print("\n  -- email attachments actually sent --")
    for attachment in delivered["attachments"]:
        print("   %s" % attachment["filename"])

    hr("10. HUMAN REVIEW GATE")
    c = st.get_case("case-001")
    print("\n  stage before handoff : %s" % c.stage.value)
    print("  pack_complete        : %s" % report["pack_complete"])
    print("  -> queued for adviser review; agent does not sign off its own work.")
    for limit in report["limits"]:
        print("     limit: %s" % limit)

    hr("11. AUDIT TRAIL")
    for row in st.audit_trail("case-001")[:8]:
        print("  %s  %s" % (row["kind"], row["detail"]))
    print("  ... %d entries total" % len(st.audit_trail("case-001")))


if __name__ == "__main__":
    main()
