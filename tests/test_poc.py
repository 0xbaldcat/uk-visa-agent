"""Tests for the invariants the design rests on.

These are not coverage tests. Each one pins a property that, if it broke, would
let the system do the specific harmful thing the architecture exists to prevent.
"""
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timedelta
from email.message import EmailMessage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import channels
import checklist as checklist_mod
import deliver
import diagnose
import document_extract
import email_bridge
import email_model
import engine as engine_mod
import facts
import llm
import real_email
import state
import store as store_mod
import validate

TODAY = date(2026, 8, 22)
NOW = datetime(2026, 8, 22, 10, 0, 0)

SLOTS = {
    "applicant_name": "Mei Ling Chen", "nationality": "Chinese",
    "trip_start": "2026-10-05", "trip_end": "2027-01-03",
    "visit_purpose": "family_visit", "has_uk_settled_relative": True,
    "employment_status": "self_employed", "third_party_funding": False,
    "prior_uk_refusal": False, "estimated_trip_cost_gbp": 4200.0,
}

COMPLETE_EVIDENCE = {
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


def load():
    return checklist_mod.load_route("visitor_family_visit")


def make_case(slots=None, evidence=None):
    st = store_mod.Store()
    case = st.create_case("t1", "visitor_family_visit")
    case.slots = dict(slots if slots is not None else SLOTS)
    case.evidence = dict(evidence or {})
    st.save_case(case)
    return st, st.get_case("t1")


class RefusingDraftModel(llm.StubModel):
    def draft_paragraph(self, risk, case_facts):
        raise llm.ModelRefusal("model unavailable")


class FileAwareModel(llm.StubModel):
    def extract_fields(self, document, wanted_fields):
        if os.path.exists(document):
            with open(document, "rb") as fh:
                assert fh.read() == b"PDFDATA"
            return {"tie_types": "apartment mortgage; elderly mother as dependant"}
        return super().extract_fields(document, wanted_fields)


def simple_pdf_bytes(lines):
    text = "\\n".join(lines)
    stream = "BT /F1 12 Tf 72 720 Td (%s) Tj ET" % text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return ("%%PDF-1.4\n"
            "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            "3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
            "/MediaBox [0 0 612 792] /Contents 5 0 R >> endobj\n"
            "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
            "5 0 obj << /Length %d >> stream\n%s\nendstream endobj\n"
            "trailer << /Root 1 0 R >>\n%%%%EOF\n" % (len(stream), stream)).encode("latin-1")


def write_minimal_docx(path, lines):
    text = "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % line for line in lines)
    xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s</w:body></w:document>' % text)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


class TestProvenance(unittest.TestCase):
    """Every requirement must trace to a source, or it must not be used."""

    def test_shipped_config_is_fully_sourced(self):
        self.assertEqual(load().unsourced(), [])

    def test_dangling_source_id_counts_as_unsourced(self):
        cl = load()
        cl.data["evidence"][0]["source"] = "no_such_source"
        self.assertIn("evidence:passport", cl.unsourced())

    def test_volatile_values_carry_their_date(self):
        entry = load().volatile_value("fee_standard_visitor_6m_gbp")
        self.assertIn("as_of", entry)
        self.assertIn("value", entry)


class TestNoFabrication(unittest.TestCase):
    """The control against the highest-cost failure mode."""

    def test_client_supplied_facts_pass(self):
        _, case = make_case()
        ledger = facts.FactLedger()
        ledger.add_case(case)
        self.assertEqual(facts.verify("Arriving 2026-10-05, departing 2027-01-03.", ledger), [])

    def test_invented_figures_are_blocked(self):
        _, case = make_case()
        ledger = facts.FactLedger()
        ledger.add_case(case)
        with self.assertRaises(facts.FabricationError):
            facts.enforce("A balance of 25000.00 has been held since 2024-01-01.", ledger)

    def test_cover_letter_rejects_unfounded_model_prose(self):
        cl = load()
        _, case = make_case()
        with self.assertRaises(facts.FabricationError):
            deliver.cover_letter(cl, case, narrative={
                "settled_relative_in_uk": "She has owned property worth 850000 since 2019-03-02."})

    def test_undrafted_risk_is_omitted_not_argued_against_the_client(self):
        """The letter is read by the caseworker.

        Falling back to the internal risk observation would have the applicant
        stating their own weakness in Home Office framing.
        """
        cl = load()
        _, case = make_case()
        letter = deliver.cover_letter(cl, case, narrative=None)
        bodies = " ".join(s["body"] for s in letter["sections"]).lower()
        self.assertNotIn("raises doubt", bodies)
        self.assertNotIn("higher-scrutiny", bodies)
        self.assertTrue(letter["risks_not_yet_addressed"])

    def test_cover_letter_marks_which_prose_the_model_wrote(self):
        cl = load()
        _, case = make_case()
        letter = deliver.cover_letter(cl, case, narrative={
            "long_stay": "The applicant departs on 2027-01-03."})
        generated = [s for s in letter["sections"] if s["generated"]]
        self.assertEqual(len(generated), 1)


class TestNoSufficiencyVerdict(unittest.TestCase):
    """The agent reports coverage, never strength."""

    def test_risks_carry_no_score_or_probability(self):
        cl = load()
        _, case = make_case()
        for risk in diagnose.active_risks(cl, case):
            for banned in ("score", "probability", "likelihood", "sufficient", "rating"):
                self.assertNotIn(banned, risk)

    def test_funds_picture_states_there_is_no_minimum(self):
        cl = load()
        _, case = make_case(evidence={"bank_statements": {
            "fields": {"closing_balance": "5100.00", "currency": "GBP"}, "failures": []}})
        picture = diagnose.funds_picture(case)
        self.assertIn("No minimum balance", picture["note"])
        self.assertNotIn("verdict", picture)

    def test_qc_report_disclaims_outcome_prediction(self):
        cl = load()
        _, case = make_case()
        limits = " ".join(deliver.qc_report(cl, case, today=TODAY)["limits"]).lower()
        self.assertIn("not a prediction", limits)


class TestAdvisoryVersusBlocking(unittest.TestCase):
    """Practice must never be enforced as law."""

    def test_practice_shortfall_does_not_block_delivery(self):
        cl = load()
        fails = validate.run_checks(
            [{"kind": "covers_days", "start_field": "ps", "end_field": "pe",
              "min_days": 180, "advisory": True}],
            {"ps": "2026-06-01", "pe": "2026-08-01"}, {}, TODAY)
        self.assertEqual(validate.blocking(fails), [])
        self.assertEqual(len(validate.advisories(fails)), 1)

    def test_advisory_wording_does_not_claim_a_requirement(self):
        fails = validate.run_checks(
            [{"kind": "covers_days", "start_field": "ps", "end_field": "pe",
              "min_days": 180, "advisory": True}],
            {"ps": "2026-06-01", "pe": "2026-08-01"}, {}, TODAY)
        self.assertNotIn("are required", fails[0].message)

    def test_advisory_only_item_still_counts_as_satisfied(self):
        cl = load()
        _, case = make_case(evidence={"passport": {
            "fields": {}, "failures": [{"advisory": True, "message": "note"}]}})
        satisfied, _, failing = case.outstanding(cl)
        self.assertIn("passport", satisfied)
        self.assertNotIn("passport", failing)


class TestDeterminism(unittest.TestCase):
    """Same state, same next step -- the anti-drift property."""

    def test_next_action_is_pure(self):
        cl = load()
        _, case = make_case()
        first = state.next_action(case, cl)
        for _ in range(20):
            self.assertEqual(state.next_action(case, cl), first)

    def test_blocking_failure_outranks_missing_evidence(self):
        cl = load()
        _, case = make_case(evidence={"passport": {
            "fields": {}, "failures": [{"advisory": False, "message": "unreadable"}]}})
        action = state.next_action(case, cl)
        self.assertEqual(action.kind, "request_resupply")
        self.assertEqual(action.evidence_id, "passport")

    def test_illegal_transition_raises(self):
        with self.assertRaises(state.IllegalTransition):
            state.transition(state.Stage.INTAKE, state.Stage.ASSEMBLING)

    def test_delivering_pack_moves_case_to_human_review(self):
        cl = load()
        st, case = make_case(evidence=COMPLETE_EVIDENCE)
        case.stage = state.Stage.COLLECTING
        st.save_case(case)
        eng = engine_mod.Engine(st, cl, llm.StubModel(), today=TODAY)

        result = eng._respond(st.get_case("t1"), state.Action("deliver_pack"))
        saved = st.get_case("t1")

        self.assertEqual(result["action"].kind, "deliver_pack")
        self.assertEqual(saved.stage, state.Stage.HUMAN_REVIEW)
        self.assertEqual(state.next_action(saved, cl).kind, "await_human")

    def test_deliver_pack_email_carries_four_rendered_deliverables(self):
        cl = load()
        st, case = make_case(evidence=COMPLETE_EVIDENCE)
        case.stage = state.Stage.COLLECTING
        st.save_case(case)
        email_sink = []
        router = channels.Router(channels.WhatsAppChannel(), channels.EmailChannel(email_sink))
        eng = engine_mod.Engine(st, cl, llm.StubModel(), router=router, today=TODAY)

        result = eng._respond(st.get_case("t1"), state.Action("deliver_pack"))

        self.assertEqual(result["sent"]["channel"], "email")
        self.assertEqual(
            [a["filename"] for a in result["sent"]["attachments"]],
            ["personalised-checklist.json", "form-answers-draft.json",
             "cover-letter-draft.json", "quality-check-report.json"])
        self.assertEqual(email_sink[-1]["attachments"], result["sent"]["attachments"])

    def test_deliver_pack_degrades_when_narrative_model_refuses(self):
        cl = load()
        st, case = make_case(evidence=COMPLETE_EVIDENCE)
        case.stage = state.Stage.COLLECTING
        st.save_case(case)
        router = channels.Router(channels.WhatsAppChannel(), channels.EmailChannel())
        eng = engine_mod.Engine(st, cl, RefusingDraftModel(), router=router, today=TODAY)

        result = eng._respond(st.get_case("t1"), state.Action("deliver_pack"))
        saved = st.get_case("t1")
        cover = [
            a["content"] for a in result["sent"]["attachments"]
            if a["filename"] == "cover-letter-draft.json"
        ][0]
        audit_kinds = [row["kind"] for row in st.audit_trail("t1")]

        self.assertEqual(result["sent"]["channel"], "email")
        self.assertEqual(saved.stage, state.Stage.HUMAN_REVIEW)
        self.assertIn("long_stay", cover["risks_not_yet_addressed"])
        self.assertIn("narrative_draft_refused", audit_kinds)


class TestModelBoundary(unittest.TestCase):
    """A misbehaving model must degrade, not corrupt."""

    def test_hallucinated_enum_is_refused(self):
        spec = {"id": "employment_status", "type": "enum", "values": ["employed"]}
        with self.assertRaises(llm.ModelRefusal):
            llm.coerce_slot("astronaut", spec)

    def test_unparseable_date_is_refused(self):
        with self.assertRaises(llm.ModelRefusal):
            llm.coerce_slot("sometime next spring", {"id": "trip_start", "type": "date"})

    def test_model_cannot_widen_the_case_record(self):
        model = llm.StubModel(documents={"d.pdf": {
            "holder_name": "Mei Ling Chen", "secret_note": "looks weak"}})
        got = model.extract_fields("d.pdf", ["holder_name"])
        self.assertEqual(got, {"holder_name": "Mei Ling Chen"})

    def test_unreadable_document_becomes_resupply_not_pass(self):
        cl = load()
        st, case = make_case()
        eng = engine_mod.Engine(st, cl, llm.StubModel(), today=TODAY)
        eng.handle_document("t1", "passport", "blurry.jpg", "k1")
        case = st.get_case("t1")
        self.assertTrue(case.evidence["passport"]["failures"])
        self.assertEqual(state.next_action(case, cl).kind, "request_resupply")


class TestDocumentExtraction(unittest.TestCase):
    def test_text_pdf_fields_are_extracted_for_requested_schema_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "passport.pdf")
            with open(path, "wb") as fh:
                fh.write(simple_pdf_bytes([
                    "Holder Name: Mei Ling Chen",
                    "Passport Number: EK1234567",
                    "Expiry Date: 2029-04-30",
                    "Secret Note: do not admit",
                ]))

            fields = email_model.EmailDemoModel().extract_fields(
                path, ["holder_name", "passport_number", "expiry_date"])

        self.assertEqual(fields, {
            "holder_name": "Mei Ling Chen",
            "passport_number": "EK1234567",
            "expiry_date": "2029-04-30",
        })

    def test_docx_fields_are_extracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bank_statements.docx")
            write_minimal_docx(path, [
                "Account Holder Name: Mei Ling Chen",
                "Period Start: 2026-02-10",
                "Period End: 2026-08-18",
                "Closing Balance: 5100.00",
                "Currency: GBP",
            ])

            fields = email_model.EmailDemoModel().extract_fields(
                path, ["account_holder_name", "period_start", "period_end",
                       "closing_balance", "currency"])

        self.assertEqual(fields["account_holder_name"], "Mei Ling Chen")
        self.assertEqual(fields["closing_balance"], "5100.00")

    def test_image_without_ocr_sidecar_refuses_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "passport.jpg")
            with open(path, "wb") as fh:
                fh.write(b"not really an image")

            with self.assertRaises(llm.ModelRefusal):
                email_model.EmailDemoModel().extract_fields(path, ["holder_name"])

    def test_image_ocr_sidecar_fields_are_extracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "home_ties_evidence.jpg")
            with open(path, "wb") as fh:
                fh.write(b"image bytes")
            with open(path + ".ocr.txt", "w") as fh:
                fh.write("Tie Types: apartment mortgage; elderly mother as dependant")

            fields = email_model.EmailDemoModel().extract_fields(path, ["tie_types"])

        self.assertEqual(
            fields, {"tie_types": "apartment mortgage; elderly mother as dependant"})


class TestUnimplementedCheckIsFatal(unittest.TestCase):
    def test_unknown_check_raises_rather_than_passing(self):
        with self.assertRaises(validate.UnknownCheck):
            validate.run_checks([{"kind": "vibe_check"}], {}, {}, TODAY)

    def test_unknown_computed_trigger_raises(self):
        cl = load()
        _, case = make_case()
        cl.data["risk_factors"][0]["trigger"] = {"computed": "nonexistent", "gt": 1}
        with self.assertRaises(KeyError):
            diagnose.active_risks(cl, case)


class TestIdempotency(unittest.TestCase):
    def test_duplicate_inbound_is_ignored(self):
        cl = load()
        st, _ = make_case(slots={})
        eng = engine_mod.Engine(st, cl, llm.StubModel(replies={"applicant_name": "Mei Ling Chen"}),
                                today=TODAY)
        first = eng.handle_reply("t1", "Mei Ling Chen", "wa:1")
        second = eng.handle_reply("t1", "Mei Ling Chen", "wa:1")
        self.assertIsNotNone(first)
        self.assertIsNone(second)


class TestWhatsAppWindow(unittest.TestCase):
    def test_free_form_blocked_after_window(self):
        wa = channels.WhatsAppChannel(approved_templates=["visa_docs_reminder"])
        wa.note_inbound("c", NOW)
        with self.assertRaises(channels.OutsideWindow):
            wa.send("c", "hello", kind="session", now=NOW + timedelta(hours=25))

    def test_unapproved_template_is_rejected(self):
        wa = channels.WhatsAppChannel(approved_templates=[])
        with self.assertRaises(channels.OutsideWindow):
            wa.send("c", "hello", kind="template", template_name="not_approved")

    def test_router_degrades_to_template_rather_than_dropping(self):
        wa = channels.WhatsAppChannel(approved_templates=["visa_docs_reminder"])
        wa.note_inbound("c", NOW)
        router = channels.Router(wa, channels.EmailChannel())
        msg = router.send("c", "request_evidence", "send docs", now=NOW + timedelta(hours=30))
        self.assertEqual(msg["kind"], "template")

    def test_documents_go_by_email(self):
        router = channels.Router(channels.WhatsAppChannel(), channels.EmailChannel())
        msg = router.send("c", "deliver_pack", "your pack", attachments=["qc.pdf"])
        self.assertEqual(msg["channel"], "email")

    def test_email_only_mode_routes_conversation_to_email(self):
        sink = []
        router = channels.Router(
            channels.WhatsAppChannel(), channels.EmailChannel(sink),
            preferred_conversation_channel="email")
        msg = router.send("c", "request_evidence", "send docs")
        self.assertEqual(msg["channel"], "email")
        self.assertEqual(sink[-1]["body"], "send docs")


class TestConditionalRequirements(unittest.TestCase):
    def test_sponsor_documents_required_only_with_a_uk_relative(self):
        cl = load()
        with_rel = [e["id"] for e in cl.required_evidence(SLOTS)]
        self.assertIn("sponsor_invitation_letter", with_rel)
        without = dict(SLOTS, has_uk_settled_relative=False)
        self.assertNotIn("sponsor_invitation_letter",
                         [e["id"] for e in cl.required_evidence(without)])

    def test_employment_letter_tracks_employment_status(self):
        cl = load()
        employed = dict(SLOTS, employment_status="employed")
        ids = [e["id"] for e in cl.required_evidence(employed)]
        self.assertIn("employment_letter", ids)
        self.assertNotIn("self_employment_evidence", ids)


class TestCrossDocumentConsistency(unittest.TestCase):
    def test_sponsor_name_mismatch_is_flagged(self):
        fails = validate.run_checks(
            [{"kind": "cross_document_consistency", "field": "sponsor_name",
              "other_evidence": "sponsor_status_proof", "other_field": "sponsor_name",
              "_other_fields": {"sponsor_name": "Wei Zhang"}}],
            {"sponsor_name": "Hui Chen"}, {}, TODAY)
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0].check_kind, "cross_document_consistency")

    def test_later_sponsor_status_upload_revalidates_invitation(self):
        cl = load()
        docs = {
            "invitation.pdf": {
                "sponsor_name": "Hui Chen", "sponsor_address": "14 Bramhall Road",
                "relationship": "sister"},
            "sponsor-status.pdf": {
                "sponsor_name": "Wei Zhang", "status_type": "Indefinite Leave to Remain"},
        }
        st, case = make_case()
        case.evidence = dict((k, v) for k, v in COMPLETE_EVIDENCE.items()
                             if k not in ("sponsor_invitation_letter", "sponsor_status_proof"))
        case.stage = state.Stage.COLLECTING
        st.save_case(case)
        eng = engine_mod.Engine(st, cl, llm.StubModel(documents=docs), today=TODAY)

        eng.handle_document("t1", "sponsor_invitation_letter", "invitation.pdf", "d1")
        before = st.get_case("t1").evidence["sponsor_invitation_letter"]["failures"]
        eng.handle_document("t1", "sponsor_status_proof", "sponsor-status.pdf", "d2")
        after = st.get_case("t1").evidence["sponsor_invitation_letter"]["failures"]

        self.assertEqual(before, [])
        self.assertEqual(after[0]["check"], "cross_document_consistency")


class TestEvidenceCoverage(unittest.TestCase):
    def test_home_tie_claims_require_matching_extracted_facts(self):
        cl = load()
        evidence = dict(COMPLETE_EVIDENCE)
        evidence["passport"] = {"fields": {
            "holder_name": "Mei Ling Chen", "passport_number": "EK1234567",
            "expiry_date": "2029-04-30"}, "failures": []}
        evidence["home_ties_evidence"] = {"fields": {"tie_types": "community club"},
                                          "failures": []}
        _, case = make_case(evidence=evidence)

        coverage = diagnose.home_tie_coverage(cl, case)

        self.assertEqual([t["id"] for t in coverage["covered"]], ["employment_tie"])
        self.assertIn("family_tie", [t["id"] for t in coverage["uncovered"]])
        self.assertIn("property_tie", [t["id"] for t in coverage["uncovered"]])
        self.assertIn("travel_history_tie", [t["id"] for t in coverage["uncovered"]])

    def test_self_employment_bundle_missing_tax_and_business_statements_blocks(self):
        cl = load()
        ev = cl.evidence("self_employment_evidence")
        fields = {"business_name": "Chen Design Studio"}
        failures = validate.run_checks(ev["checks"], fields, SLOTS, today=TODAY)

        self.assertEqual(
            sorted(f.field for f in validate.blocking(failures)),
            ["business_statement_period_end", "business_statement_period_start",
             "declared_income", "registration_id", "tax_year"])


class TestDemoScripts(unittest.TestCase):
    def test_email_only_demo_runs_without_whatsapp_window(self):
        root = os.path.dirname(HERE)
        result = subprocess.run(
            [sys.executable, "demo.py", "--email-only"],
            cwd=root, check=True, text=True, capture_output=True)
        self.assertIn("EMAIL-ONLY FALLBACK", result.stdout)
        self.assertIn("[late chase] action=request_resupply via=email", result.stdout)
        self.assertIn("personalised-checklist.json", result.stdout)


class TestRealEmailAdapter(unittest.TestCase):
    def test_build_message_attaches_json_deliverables(self):
        settings = real_email.EmailSettings(
            smtp_host="smtp.example.test", smtp_port=587,
            imap_host="imap.example.test", imap_port=993,
            username="agent@example.test", password="not-used",
            from_addr="agent@example.test", to_addr="client@example.test")

        msg = real_email.build_message(
            settings, "Pack", "Ready",
            attachments=[{"filename": "quality-check-report.json",
                          "content_type": "application/json",
                          "content": {"pack_complete": True}}])

        self.assertEqual(msg["From"], "agent@example.test")
        self.assertEqual(msg["To"], "client@example.test")
        attachments = [part for part in msg.iter_attachments()]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "quality-check-report.json")
        self.assertIn(b"pack_complete", attachments[0].get_content())

    def test_email_settings_require_explicit_env(self):
        old = dict(os.environ)
        try:
            for key in list(os.environ):
                if key.startswith("VISA_AGENT_"):
                    del os.environ[key]
            with self.assertRaises(RuntimeError):
                real_email.EmailSettings.from_env()
        finally:
            os.environ.clear()
            os.environ.update(old)


class TestEmailBridge(unittest.TestCase):
    def _raw_email(self, message_id="<client-1@example.test>",
                   subject="[visa-agent:t1] Re: documents", body="Attached.",
                   attachments=None, in_reply_to=None, references=None):
        msg = EmailMessage()
        msg["From"] = "client@example.test"
        msg["To"] = "agent@example.test"
        msg["Subject"] = subject
        msg["Message-ID"] = message_id
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = " ".join(references)
        msg.set_content(body)
        for filename, content_type, content in attachments or []:
            maintype, subtype = content_type.split("/", 1)
            msg.add_attachment(
                content, maintype=maintype, subtype=subtype, filename=filename)
        return msg.as_bytes()

    def test_parse_email_keeps_raw_attachment_bytes_and_case_token(self):
        parsed = email_bridge.parse_email(self._raw_email(
            attachments=[("home_ties_evidence.pdf", "application/pdf", b"PDFDATA")]))

        self.assertEqual(parsed.case_id, "t1")
        self.assertEqual(parsed.message_id, "<client-1@example.test>")
        self.assertEqual(parsed.attachments[0].content, b"PDFDATA")
        self.assertEqual(parsed.attachments[0].evidence_id, "home_ties_evidence")

    def test_poll_email_attachment_advances_engine_and_sends_pack(self):
        cl = load()
        evidence = dict(COMPLETE_EVIDENCE)
        del evidence["home_ties_evidence"]
        st, case = make_case(evidence=evidence)
        case.stage = state.Stage.COLLECTING
        st.save_case(case)
        sink = []
        router = channels.Router(
            channels.WhatsAppChannel(), channels.EmailChannel(sink),
            preferred_conversation_channel="email")
        model = llm.StubModel(documents={
            "home_ties_evidence.pdf": {
                "tie_types": "apartment mortgage; elderly mother as dependant"}})
        eng = engine_mod.Engine(st, cl, model, router=router, today=TODAY)
        poller = email_bridge.EmailPoller(eng, st)

        results = poller.poll_raw([self._raw_email(
            attachments=[("home_ties_evidence.pdf", "application/pdf", b"PDFDATA")])])

        self.assertEqual(results[-1]["action"].kind, "deliver_pack")
        self.assertEqual(st.get_case("t1").stage, state.Stage.HUMAN_REVIEW)
        self.assertEqual(len(sink[-1]["attachments"]), 4)
        self.assertEqual(sink[-1]["in_reply_to"], "<client-1@example.test>")
        self.assertIn("<client-1@example.test>", sink[-1]["references"])

    def test_poll_email_uses_rfc_message_id_for_dedupe(self):
        cl = load()
        evidence = dict(COMPLETE_EVIDENCE)
        del evidence["home_ties_evidence"]
        st, case = make_case(evidence=evidence)
        case.stage = state.Stage.COLLECTING
        st.save_case(case)
        router = channels.Router(
            channels.WhatsAppChannel(), channels.EmailChannel(),
            preferred_conversation_channel="email")
        model = llm.StubModel(documents={
            "home_ties_evidence.pdf": {
                "tie_types": "apartment mortgage; elderly mother as dependant"}})
        eng = engine_mod.Engine(st, cl, model, router=router, today=TODAY)
        poller = email_bridge.EmailPoller(eng, st)
        raw = self._raw_email(
            attachments=[("home_ties_evidence.pdf", "application/pdf", b"PDFDATA")])

        first = poller.poll_raw([raw])
        second = poller.poll_raw([raw])

        self.assertTrue(first)
        self.assertEqual(second, [])

    def test_poll_email_can_save_attachment_bytes_before_document_handling(self):
        cl = load()
        evidence = dict(COMPLETE_EVIDENCE)
        del evidence["home_ties_evidence"]
        st, case = make_case(evidence=evidence)
        case.stage = state.Stage.COLLECTING
        st.save_case(case)
        router = channels.Router(
            channels.WhatsAppChannel(), channels.EmailChannel(),
            preferred_conversation_channel="email")
        eng = engine_mod.Engine(st, cl, FileAwareModel(), router=router, today=TODAY)
        with tempfile.TemporaryDirectory() as tmp:
            poller = email_bridge.EmailPoller(eng, st, attachment_dir=tmp)
            results = poller.poll_raw([self._raw_email(
                attachments=[("home_ties_evidence.pdf", "application/pdf", b"PDFDATA")])])

            self.assertEqual(results[-1]["action"].kind, "deliver_pack")
            self.assertTrue(os.path.exists(
                os.path.join(tmp, "t1", "0-home_ties_evidence.pdf")))

    def test_poll_email_pdf_attachment_extracts_fields_through_email_model(self):
        cl = load()
        evidence = dict(COMPLETE_EVIDENCE)
        del evidence["home_ties_evidence"]
        st, case = make_case(evidence=evidence)
        case.stage = state.Stage.COLLECTING
        st.save_case(case)
        sink = []
        router = channels.Router(
            channels.WhatsAppChannel(), channels.EmailChannel(sink),
            preferred_conversation_channel="email")
        eng = engine_mod.Engine(st, cl, email_model.EmailDemoModel(),
                                router=router, today=TODAY)

        with tempfile.TemporaryDirectory() as tmp:
            poller = email_bridge.EmailPoller(eng, st, attachment_dir=tmp)
            results = poller.poll_raw([self._raw_email(
                attachments=[(
                    "home_ties_evidence.pdf", "application/pdf",
                    simple_pdf_bytes([
                        "Tie Types: apartment mortgage; elderly mother as dependant",
                    ]))])])

        self.assertEqual(results[-1]["action"].kind, "deliver_pack")
        self.assertEqual(st.get_case("t1").evidence["home_ties_evidence"]["fields"], {
            "tie_types": "apartment mortgage; elderly mother as dependant"})
        self.assertEqual(len(sink[-1]["attachments"]), 4)

    def test_reply_header_mapping_resolves_case_without_subject_token(self):
        cl = load()
        st, case = make_case(slots={})
        st.save_case(case)
        sink = []
        router = channels.Router(
            channels.WhatsAppChannel(), channels.EmailChannel(sink),
            preferred_conversation_channel="email")
        eng = engine_mod.Engine(
            st, cl, llm.StubModel(replies={"applicant_name": "Mei Ling Chen"}),
            router=router, today=TODAY)
        poller = email_bridge.EmailPoller(
            eng, st, message_to_case={"<agent-prev@example.test>": "t1"})

        results = poller.poll_raw([self._raw_email(
            message_id="<client-reply@example.test>",
            subject="Re: Visa preparation update",
            body="Mei Ling Chen",
            in_reply_to="<agent-prev@example.test>",
            references=["<agent-prev@example.test>"])])

        self.assertEqual(st.get_case("t1").slots["applicant_name"], "Mei Ling Chen")
        self.assertEqual(results[-1]["sent"]["in_reply_to"], "<client-reply@example.test>")

    def test_empty_case_can_complete_intake_from_ten_plain_email_replies(self):
        cl = load()
        st, case = make_case(slots={})
        st.save_case(case)
        sink = []
        router = channels.Router(
            channels.WhatsAppChannel(), channels.EmailChannel(sink),
            preferred_conversation_channel="email")
        eng = engine_mod.Engine(st, cl, email_model.EmailDemoModel(),
                                router=router, today=TODAY)
        poller = email_bridge.EmailPoller(eng, st)
        replies = [
            "Mei Ling Chen",
            "Chinese",
            "2026-10-05",
            "2027-01-03",
            "family visit",
            "yes",
            "self employed",
            "no",
            "no",
            "4200",
        ]

        for index, body in enumerate(replies):
            poller.poll_raw([self._raw_email(
                message_id="<client-intake-%d@example.test>" % index,
                subject="[visa-agent:t1] intake",
                body=body)])

        case = st.get_case("t1")
        self.assertEqual(case.slots, SLOTS)
        self.assertEqual(sink[-1]["case_id"], "t1")
        self.assertIn("Passport biographic page", sink[-1]["body"])

    def test_gmail_quote_text_does_not_pollute_plain_reply(self):
        cl = load()
        st, case = make_case(slots={})
        st.save_case(case)
        router = channels.Router(
            channels.WhatsAppChannel(), channels.EmailChannel(),
            preferred_conversation_channel="email")
        eng = engine_mod.Engine(st, cl, email_model.EmailDemoModel(),
                                router=router, today=TODAY)
        poller = email_bridge.EmailPoller(eng, st)

        poller.poll_raw([self._raw_email(
            body=("Mei Ling Chen\n\n"
                  "On Sat, Aug 22, 2026 at 10:00 AM Visa Agent wrote:\n"
                  "> To start, what's your full name?"))])

        self.assertEqual(st.get_case("t1").slots["applicant_name"], "Mei Ling Chen")

    def test_outlook_quote_text_does_not_pollute_plain_reply(self):
        cl = load()
        st, case = make_case(slots={})
        st.save_case(case)
        router = channels.Router(
            channels.WhatsAppChannel(), channels.EmailChannel(),
            preferred_conversation_channel="email")
        eng = engine_mod.Engine(st, cl, email_model.EmailDemoModel(),
                                router=router, today=TODAY)
        poller = email_bridge.EmailPoller(eng, st)

        poller.poll_raw([self._raw_email(
            body=("Mei Ling Chen\n\n"
                  "-----Original Message-----\n"
                  "From: Visa Agent <agent@example.test>\n"
                  "Sent: Saturday, August 22, 2026 10:00 AM"))])

        self.assertEqual(st.get_case("t1").slots["applicant_name"], "Mei Ling Chen")

    def test_single_attachment_email_produces_one_outbound_response(self):
        cl = load()
        evidence = dict(COMPLETE_EVIDENCE)
        del evidence["passport"]
        del evidence["bank_statements"]
        st, case = make_case(evidence=evidence)
        case.stage = state.Stage.COLLECTING
        st.save_case(case)
        sink = []
        router = channels.Router(
            channels.WhatsAppChannel(), channels.EmailChannel(sink),
            preferred_conversation_channel="email")
        model = llm.StubModel(documents={"passport.pdf": {
            "holder_name": "Mei Ling Chen", "passport_number": "EK1234567",
            "expiry_date": "2029-04-30", "prior_compliant_travel": True}})
        eng = engine_mod.Engine(st, cl, model, router=router, today=TODAY)
        poller = email_bridge.EmailPoller(eng, st)

        results = poller.poll_raw([self._raw_email(
            body="Attached as requested.",
            attachments=[("passport.pdf", "application/pdf", b"PDFDATA")])])

        self.assertEqual(len(results), 1)
        self.assertEqual(len(sink), 1)
        self.assertIn("Personal bank statements", sink[0]["body"])
        self.assertNotIn("Passport biographic page", sink[0]["body"])

    def test_multi_attachment_email_produces_one_outbound_response(self):
        cl = load()
        evidence = dict(COMPLETE_EVIDENCE)
        del evidence["passport"]
        del evidence["bank_statements"]
        del evidence["travel_itinerary"]
        st, case = make_case(evidence=evidence)
        case.stage = state.Stage.COLLECTING
        st.save_case(case)
        sink = []
        router = channels.Router(
            channels.WhatsAppChannel(), channels.EmailChannel(sink),
            preferred_conversation_channel="email")
        model = llm.StubModel(documents={
            "passport.pdf": {
                "holder_name": "Mei Ling Chen", "passport_number": "EK1234567",
                "expiry_date": "2029-04-30", "prior_compliant_travel": True},
            "bank_statements.pdf": {
                "account_holder_name": "Mei Ling Chen", "period_start": "2026-02-10",
                "period_end": "2026-08-18", "closing_balance": "5100.00",
                "currency": "GBP"},
        })
        eng = engine_mod.Engine(st, cl, model, router=router, today=TODAY)
        poller = email_bridge.EmailPoller(eng, st)

        results = poller.poll_raw([self._raw_email(
            body="Attached as requested.",
            attachments=[
                ("passport.pdf", "application/pdf", b"PDFDATA"),
                ("bank_statements.pdf", "application/pdf", b"PDFDATA"),
            ])])

        self.assertEqual(len(results), 1)
        self.assertEqual(len(sink), 1)
        self.assertIn("Travel booking", sink[0]["body"])
        self.assertNotIn("Passport biographic page", sink[0]["body"])
        self.assertNotIn("Personal bank statements", sink[0]["body"])

    def test_reply_mapping_persists_across_poller_instances(self):
        cl = load()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "cases.sqlite3")
            st1 = store_mod.Store(db_path)
            case = st1.create_case("t1", "visitor_family_visit")
            st1.save_case(case)
            sink1 = []
            router1 = channels.Router(
                channels.WhatsAppChannel(), channels.EmailChannel(sink1),
                preferred_conversation_channel="email")
            eng1 = engine_mod.Engine(
                st1, cl, email_model.EmailDemoModel(), router=router1, today=TODAY)
            poller1 = email_bridge.EmailPoller(eng1, st1)
            poller1.poll_raw([self._raw_email(
                message_id="<client-first@example.test>",
                subject="[visa-agent:t1] first",
                body="Mei Ling Chen")])
            outgoing_id = sink1[-1]["message_id"]

            st2 = store_mod.Store(db_path)
            sink2 = []
            router2 = channels.Router(
                channels.WhatsAppChannel(), channels.EmailChannel(sink2),
                preferred_conversation_channel="email")
            eng2 = engine_mod.Engine(
                st2, cl, email_model.EmailDemoModel(), router=router2, today=TODAY)
            poller2 = email_bridge.EmailPoller(eng2, st2)
            poller2.poll_raw([self._raw_email(
                message_id="<client-second@example.test>",
                subject="Re: Visa preparation update",
                body="Chinese",
                in_reply_to=outgoing_id,
                references=[outgoing_id])])

            self.assertEqual(st2.get_case("t1").slots["nationality"], "Chinese")
            self.assertEqual(sink2[-1]["in_reply_to"], "<client-second@example.test>")


class TestImapSafety(unittest.TestCase):
    def test_fetch_unseen_raw_peeks_without_marking_seen(self):
        calls = []

        class FakeImap(object):
            def __init__(self, host, port):
                pass
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False
            def login(self, username, password):
                pass
            def select(self, mailbox):
                pass
            def search(self, charset, *criteria):
                return "OK", [b"1"]
            def fetch(self, msg_id, query):
                calls.append(("fetch", query))
                return "OK", [(b"1", b"Subject: hi\r\nMessage-ID: <m@test>\r\n\r\nbody")]

        old = real_email.imaplib.IMAP4_SSL
        try:
            real_email.imaplib.IMAP4_SSL = FakeImap
            settings = real_email.EmailSettings(
                smtp_host="smtp", smtp_port=587, imap_host="imap", imap_port=993,
                username="u", password="p", from_addr="a@test", to_addr="b@test")
            rows = real_email.fetch_unseen_raw(settings)
        finally:
            real_email.imaplib.IMAP4_SSL = old

        self.assertEqual(rows[0]["imap_id"], "1")
        self.assertEqual(calls, [("fetch", "(BODY.PEEK[])")])

    def test_mark_seen_is_explicit(self):
        calls = []

        class FakeImap(object):
            def __init__(self, host, port):
                pass
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False
            def login(self, username, password):
                pass
            def select(self, mailbox):
                pass
            def store(self, imap_id, command, flag):
                calls.append((imap_id, command, flag))
                return "OK", []

        old = real_email.imaplib.IMAP4_SSL
        try:
            real_email.imaplib.IMAP4_SSL = FakeImap
            settings = real_email.EmailSettings(
                smtp_host="smtp", smtp_port=587, imap_host="imap", imap_port=993,
                username="u", password="p", from_addr="a@test", to_addr="b@test")
            real_email.mark_seen(settings, "42")
        finally:
            real_email.imaplib.IMAP4_SSL = old

        self.assertEqual(calls, [("42", "+FLAGS", "\\Seen")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
