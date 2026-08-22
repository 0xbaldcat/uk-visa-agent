"""Ingress event parsing and schema repair.

LLMs may propose structure, but workflow owns acceptance. This module wraps a
candidate parser with schema validation, a bounded repair attempt, and a trace
record that can be persisted for audit.
"""
import json

import llm


class IngressResult(object):
    def __init__(self, event_type, candidate_json=None, accepted_json=None,
                 rejected_json=None, validation_errors=None, repair_attempts=0,
                 status="rejected", prompt_version="ingress-intake-v1",
                 schema_version="intake-slots-v1", model_name=None,
                 raw_input=None):
        self.event_type = event_type
        self.candidate_json = candidate_json or {}
        self.accepted_json = accepted_json or {}
        self.rejected_json = rejected_json or {}
        self.validation_errors = validation_errors or []
        self.repair_attempts = repair_attempts
        self.status = status
        self.prompt_version = prompt_version
        self.schema_version = schema_version
        self.model_name = model_name
        self.raw_input = raw_input

    def trace(self, raw_message_id=None):
        return {
            "event_type": self.event_type,
            "raw_message_id": raw_message_id,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "model_name": self.model_name,
            "candidate_json": self.candidate_json,
            "accepted_json": self.accepted_json,
            "rejected_json": self.rejected_json,
            "validation_errors": self.validation_errors,
            "repair_attempts": self.repair_attempts,
            "status": self.status,
        }


class IntakeIngressInterpreter(object):
    def __init__(self, candidate_parser, repair_parser=None, max_repairs=1,
                 prompt_version="ingress-intake-v1", schema_version="intake-slots-v1"):
        self.candidate_parser = candidate_parser
        self.repair_parser = repair_parser or candidate_parser
        self.max_repairs = max_repairs
        self.prompt_version = prompt_version
        self.schema_version = schema_version

    def parse(self, text, slot_specs):
        candidate = self.candidate_parser.parse_intake_candidate(text, slot_specs)
        accepted, rejected, errors = validate_candidate(candidate, slot_specs)
        attempts = 0
        if rejected and attempts < self.max_repairs and hasattr(self.repair_parser, "repair_intake_candidate"):
            attempts += 1
            repaired = self.repair_parser.repair_intake_candidate(
                text, slot_specs, candidate, errors, accepted)
            r_accepted, r_rejected, r_errors = validate_candidate(repaired, slot_specs)
            for key, value in r_accepted.items():
                if key not in accepted:
                    accepted[key] = value
            rejected = dict((k, v) for k, v in r_rejected.items() if k not in accepted)
            errors = r_errors
            candidate = repaired

        if accepted and rejected:
            status = "partially_applied"
        elif accepted:
            status = "applied"
        else:
            status = "rejected"
        return IngressResult(
            "provide_intake_facts",
            candidate_json=candidate,
            accepted_json=accepted,
            rejected_json=rejected,
            validation_errors=errors,
            repair_attempts=attempts,
            status=status,
            prompt_version=self.prompt_version,
            schema_version=self.schema_version,
            model_name=getattr(self.candidate_parser, "model_name", None),
            raw_input=text,
        )


def validate_candidate(candidate, slot_specs):
    allowed = dict((spec["id"], spec) for spec in slot_specs)
    accepted = {}
    rejected = {}
    errors = []
    if not isinstance(candidate, dict):
        return {}, {"_root": candidate}, [{
            "field": "_root", "error": "candidate must be a JSON object"}]
    for key, value in candidate.items():
        if key not in allowed:
            rejected[key] = value
            errors.append({"field": key, "error": "field not expected in current state"})
            continue
        if value in (None, ""):
            continue
        try:
            accepted[key] = llm.coerce_slot(value, allowed[key])
        except llm.ModelRefusal as exc:
            rejected[key] = value
            errors.append({"field": key, "error": str(exc), "value": value})
    return accepted, rejected, errors


class DeterministicIntakeCandidateParser(object):
    model_name = "deterministic-intake-parser"

    def parse_intake_candidate(self, text, slot_specs):
        # Reuse EmailDemoModel's local parser without creating a dependency cycle.
        import email_model
        return email_model.extract_intake_candidates(text, slot_specs)


class StaticCandidateParser(object):
    def __init__(self, candidates, model_name="static-test-parser"):
        self.candidates = list(candidates)
        self.model_name = model_name
        self.repair_calls = []

    def parse_intake_candidate(self, text, slot_specs):
        if not self.candidates:
            return {}
        return self.candidates.pop(0)

    def repair_intake_candidate(self, text, slot_specs, candidate, errors, accepted):
        self.repair_calls.append({
            "candidate": candidate,
            "errors": errors,
            "accepted": accepted,
        })
        if not self.candidates:
            return candidate
        return self.candidates.pop(0)
