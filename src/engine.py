"""Turn handler.

One entry point per inbound message. Every turn:

  1. de-duplicate the inbound message
  2. reload case state from the DB (never from conversation history)
  3. apply exactly one piece of new information
  4. re-derive the next action from state
  5. compose, enqueue, send

Step 4 is a pure function, so the same case always produces the same next step.
That is what makes the behaviour reproducible across a multi-day conversation in
which the client answers out of order or repeats themselves.
"""
from datetime import date
from typing import Dict, Any, Optional, List

import compose
import deliver
import diagnose
import facts
import ingress
import llm as llm_mod
import state
import validate
from state import Stage


class Engine(object):
    def __init__(self, store, checklist, model, router=None, today=None):
        self.store = store
        self.checklist = checklist
        self.model = model
        self.router = router
        self.today = today or date.today()

    # --- inbound --------------------------------------------------------

    def handle_reply(self, case_id, text, dedupe_key, channel="whatsapp", now=None):
        """Client sent a text message."""
        if not self.store.record_inbound(dedupe_key, case_id, channel, text):
            return None                      # already processed; do not re-ask
        case = self.store.get_case(case_id)
        action = state.next_action(case, self.checklist)

        if action.kind == "ask_slot":
            spec = self.checklist.slot(action.slot)
            try:
                event = self._parse_intake(text, action)
                parsed = event.accepted_json if event else {}
                if event and hasattr(self.store, "record_ingress_event"):
                    self.store.record_ingress_event(case_id, event.trace(raw_message_id=dedupe_key))
                if parsed:
                    for slot_id, value in parsed.items():
                        case.slots[slot_id] = value
                        self.store.log(case_id, "slot_filled", {
                            "slot": slot_id, "value": value, "source": "intake_parse"})
                    self._advance_stage(case)
                    self.store.save_case(case)
                else:
                    value = self.model.parse_reply(text, action.slot, spec)
                    case.slots[action.slot] = value
                    self.store.log(case_id, "slot_filled", {"slot": action.slot, "value": value})
                    self._advance_stage(case)
                    self.store.save_case(case)
            except llm_mod.ModelRefusal as exc:
                # The model could not read the answer. Ask again once; never invent.
                self.store.log(case_id, "slot_parse_refused",
                               {"slot": action.slot, "reason": str(exc)})
                return self._respond(case, action, now=now)

        case = self.store.get_case(case_id)
        return self._respond(case, state.next_action(case, self.checklist), now=now)

    def handle_document(self, case_id, evidence_id, document_ref, dedupe_key,
                        channel="whatsapp", now=None):
        """Client uploaded a document against a checklist item."""
        applied = self.apply_document(case_id, evidence_id, document_ref, dedupe_key, channel=channel)
        if applied is None:
            return None
        if isinstance(applied, state.Action):
            case = self.store.get_case(case_id)
            return self._respond(case, applied, now=now)
        case = self.store.get_case(case_id)
        return self._respond(case, state.next_action(case, self.checklist), now=now)

    def apply_document(self, case_id, evidence_id, document_ref, dedupe_key,
                       channel="whatsapp"):
        """Apply an uploaded document without sending the next response.

        Email can bundle several attachments in one inbound message. The poller
        uses this method to update state for all of them, then emits exactly one
        final response for the resulting case state.
        """
        if not self.store.record_inbound(dedupe_key, case_id, channel, document_ref):
            return None
        case = self.store.get_case(case_id)
        ev = self.checklist.evidence(evidence_id)
        if ev is None:
            case.stage = state.transition(case.stage, Stage.ESCALATED)
            case.escalation_reason = "unknown evidence id %s" % evidence_id
            self.store.save_case(case)
            return state.Action("escalate")

        try:
            fields = self.model.extract_fields(document_ref, ev.get("extract", []))
        except llm_mod.ModelRefusal as exc:
            self._record_document_trace(case_id, evidence_id, document_ref, {
                "provider": getattr(self.model, "model_name", None) or "document-extractor",
                "candidate_json": {},
                "accepted_json": {},
                "rejected_json": {},
                "validation_errors": [{"field": "_document", "error": str(exc)}],
                "status": "rejected",
            })
            # Could not read the document. That is a resupply request, not a pass.
            case.evidence[evidence_id] = {
                "fields": {}, "document_ref": document_ref,
                "failures": [validate.Failure(
                    "unreadable", None,
                    "I couldn't read this document clearly enough to check it").to_dict()]}
            self.store.log(case_id, "extract_refused",
                           {"evidence": evidence_id, "reason": str(exc)})
            self.store.save_case(case)
            return True
        self._record_document_trace(
            case_id, evidence_id, document_ref,
            getattr(self.model, "last_document_trace", None))

        checks = self._resolve_checks(ev, case)
        failures = validate.run_checks(checks, fields, case.slots, today=self.today)
        self._record_validation_trace(case_id, evidence_id, checks, failures)
        case.evidence[evidence_id] = {
            "fields": fields,
            "document_ref": document_ref,
            "failures": [f.to_dict() for f in failures],
        }
        self._revalidate_all(case)
        self.store.log(case_id, "evidence_recorded", {
            "evidence": evidence_id,
            "blocking": len([f for f in case.evidence[evidence_id]["failures"] if not f.get("advisory")]),
            "advisory": len([f for f in case.evidence[evidence_id]["failures"] if f.get("advisory")])})
        self._advance_stage(case)
        self.store.save_case(case)
        return True

    # --- internals ------------------------------------------------------

    def _parse_intake(self, text, action):
        if not (hasattr(self.model, "parse_intake_event")
                or hasattr(self.model, "parse_intake")):
            return {}
        slots = []
        for slot_id in action.payload.get("slots") or [action.slot]:
            spec = self.checklist.slot(slot_id)
            if spec:
                slots.append(spec)
        if hasattr(self.model, "parse_intake_event"):
            return self.model.parse_intake_event(text, slots)
        if hasattr(self.model, "parse_intake"):
            parsed = self.model.parse_intake(text, slots)
            return ingress.IngressResult(
                "provide_intake_facts",
                candidate_json=parsed or {},
                accepted_json=parsed or {},
                status=("applied" if parsed else "rejected"),
                model_name=getattr(self.model, "model_name", None),
                raw_input=text)
        return ingress.IngressResult("provide_intake_facts", raw_input=text)

    def _resolve_checks(self, ev, case):
        """Inject sibling-document values for cross-document checks."""
        resolved = []
        for chk in ev.get("checks", []):
            chk = dict(chk)
            if chk.get("kind") == "cross_document_consistency":
                other = case.evidence.get(chk.get("other_evidence")) or {}
                chk["_other_fields"] = other.get("fields") or {}
            resolved.append(chk)
        return resolved

    def _revalidate_all(self, case):
        """Re-run checks whose inputs may have changed after a later upload.

        Cross-document checks are order-sensitive if we only validate the document
        being uploaded. Revalidating every supplied required item keeps the case
        row consistent no matter which order the client sends files in.
        """
        for ev in self.checklist.required_evidence(case.slots):
            rec = case.evidence.get(ev["id"])
            if not rec:
                continue
            checks = self._resolve_checks(ev, case)
            failures = validate.run_checks(
                checks, rec.get("fields") or {}, case.slots, today=self.today)
            rec["failures"] = [f.to_dict() for f in failures]

    def _record_document_trace(self, case_id, evidence_id, document_ref, trace):
        if trace and hasattr(self.store, "record_document_extraction_event"):
            self.store.record_document_extraction_event(
                case_id, evidence_id, document_ref, trace)

    def _record_validation_trace(self, case_id, evidence_id, checks, failures):
        if not hasattr(self.store, "record_validation_event"):
            return
        failed = {}
        for failure in failures:
            failed.setdefault(failure.check_kind, []).append(failure.to_dict())
        for chk in checks:
            kind = chk.get("kind")
            details = failed.get(kind, [])
            self.store.record_validation_event(
                case_id, evidence_id, kind,
                "failed" if details else "passed",
                {"failures": details})

    def _advance_stage(self, case):
        """Move the stage to match reality. Transitions are validated, not assumed."""
        if self.checklist.first_missing_slot(case.slots) is not None:
            return
        if case.stage == Stage.INTAKE:
            case.stage = state.transition(case.stage, Stage.CHECKLIST_READY)
        if case.stage == Stage.CHECKLIST_READY:
            case.stage = state.transition(case.stage, Stage.COLLECTING)

        has_failing = case.first_failed_evidence(self.checklist) is not None
        if has_failing and case.stage == Stage.COLLECTING:
            case.stage = state.transition(case.stage, Stage.REMEDIATION)
        elif not has_failing and case.stage == Stage.REMEDIATION:
            case.stage = state.transition(case.stage, Stage.COLLECTING)

    def _respond(self, case, action, now=None):
        body = compose.compose(action, self.checklist, case, model=self.model)
        attachments = None
        if action.kind == "deliver_pack":
            attachments = self._delivery_attachments(case)
        outbox_id = self.store.enqueue(case.id, "auto", body, action_kind=action.kind)
        sent = None
        if self.router is not None:
            sent = self.router.send(case.id, action.kind, body, attachments=attachments, now=now)
            self.store.mark_sent(outbox_id)
        if action.kind == "deliver_pack":
            case.stage = state.transition(case.stage, Stage.ASSEMBLING)
            self.store.log(case.id, "pack_assembled", {"outbox_id": outbox_id})
            case.stage = state.transition(case.stage, Stage.HUMAN_REVIEW)
            self.store.log(case.id, "human_review_requested", {"outbox_id": outbox_id})
            self.store.save_case(case)
        return {"action": action, "body": body, "sent": sent, "outbox_id": outbox_id}

    def _delivery_attachments(self, case):
        narrative = {}
        for risk in diagnose.active_risks(self.checklist, case):
            try:
                para = self.model.draft_paragraph(risk, case.slots)
            except llm_mod.ModelRefusal as exc:
                self.store.log(case.id, "narrative_draft_refused", {
                    "risk": risk["id"], "reason": str(exc)})
                continue
            if para:
                narrative[risk["id"]] = para
        try:
            pack = deliver.build_pack(
                self.checklist, case, narrative=narrative, today=self.today)
        except facts.FabricationError as exc:
            case.stage = state.transition(case.stage, Stage.ESCALATED)
            case.escalation_reason = str(exc)
            self.store.save_case(case)
            self.store.log(case.id, "delivery_blocked", {"reason": str(exc)})
            raise
        return [
            {"filename": "personalised-checklist.json",
             "content_type": "application/json",
             "content": pack["document_checklist"]},
            {"filename": "form-answers-draft.json",
             "content_type": "application/json",
             "content": pack["form_answers"]},
            {"filename": "cover-letter-draft.json",
             "content_type": "application/json",
             "content": pack["cover_letter"]},
            {"filename": "quality-check-report.json",
             "content_type": "application/json",
             "content": pack["qc_report"]},
        ]

    # --- diagnosis surface ---------------------------------------------

    def open_questions(self, case_id):
        """Remediation questions from active risks -- what a consultant would chase.

        Only ever returns the configured `ask` sentence, so the agent cannot drift
        into offering an opinion on whether the case is strong enough.
        """
        case = self.store.get_case(case_id)
        return [{"risk": r["id"], "question": compose.risk_prompt(r)}
                for r in diagnose.active_risks(self.checklist, case)]
