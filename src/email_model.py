"""Deterministic model adapter for the live email PoC.

It parses the current expected slot from a plain email reply and extracts fields
from saved real attachments through the document extraction seam. JSON field
files are still accepted as a developer shortcut, but the mailbox walkthrough can
now use PDF/DOCX/image files once OCR text is available.
"""
import json
import os
import re
import socket
import urllib.error
import urllib.request

import document_extract
import ingress
import llm

QUOTE_CUTOFFS = [
    re.compile(r"^On .+ wrote:$", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.IGNORECASE),
    re.compile(r"^From:\s.+", re.IGNORECASE),
]


class EmailDemoModel(llm.StubModel):
    def __init__(self, text_extractor=None, intake_client=None, document_client=None,
                 case_analysis_client=None):
        super(EmailDemoModel, self).__init__()
        self.text_extractor = text_extractor or document_extract.text_extractor_from_env()
        self.intake_client = intake_client
        self.document_client = document_client or document_client_from_env()
        self.case_analysis_client = case_analysis_client or case_analysis_client_from_env()
        self.last_document_trace = None

    def parse_reply(self, text, expected_slot, slot_spec):
        value = _clean(text)
        if slot_spec.get("type") == "enum":
            value = _normalise_enum(value, slot_spec.get("values", []))
        return llm.coerce_slot(value, slot_spec)

    def parse_intake_event(self, text, slot_specs):
        parsers = []
        if self.intake_client is not None:
            parsers.append(self.intake_client)
        parsers.append(ingress.DeterministicIntakeCandidateParser())
        return MergedIntakeInterpreter(parsers).parse(text, slot_specs)

    def parse_intake(self, text, slot_specs):
        return self.parse_intake_event(text, slot_specs).accepted_json

        if "applicant_name" in wanted:
            name = _extract_name(cleaned)
            if name:
                raw["applicant_name"] = name
        if "nationality" in wanted:
            nationality = _extract_nationality(cleaned)
            if nationality:
                raw["nationality"] = nationality
        if "trip_start" in wanted or "trip_end" in wanted:
            dates = _extract_dates(cleaned)
            if "trip_start" in wanted and len(dates) >= 1:
                raw["trip_start"] = dates[0]
            if "trip_end" in wanted and len(dates) >= 2:
                raw["trip_end"] = dates[1]
        if "visit_purpose" in wanted:
            purpose = _extract_visit_purpose(lower)
            if purpose:
                raw["visit_purpose"] = purpose
        if "has_uk_settled_relative" in wanted:
            relative = _extract_uk_relative(lower)
            if relative is not None:
                raw["has_uk_settled_relative"] = relative
        if "employment_status" in wanted:
            employment = _extract_employment(lower)
            if employment:
                raw["employment_status"] = employment
        if "third_party_funding" in wanted:
            funding = _extract_third_party_funding(lower)
            if funding is not None:
                raw["third_party_funding"] = funding
        if "prior_uk_refusal" in wanted:
            refusal = _extract_prior_refusal(lower)
            if refusal is not None:
                raw["prior_uk_refusal"] = refusal
        if "estimated_trip_cost_gbp" in wanted:
            cost = _extract_cost(lower)
            if cost is not None:
                raw["estimated_trip_cost_gbp"] = cost

    def extract_fields(self, document, wanted_fields):
        self.last_document_trace = None
        if os.path.exists(document) and document.endswith(".json"):
            with open(document) as fh:
                raw = json.load(fh)
            accepted, rejected, errors = document_extract.validate_document_candidate(
                raw, wanted_fields)
            self.last_document_trace = document_extract.DocumentExtractionResult(
                candidate_json=raw,
                accepted_json=accepted,
                rejected_json=rejected,
                validation_errors=errors,
                status=("applied" if accepted and not rejected else (
                    "partially_applied" if accepted else "rejected")),
                raw_text=json.dumps(raw, ensure_ascii=False),
                provider="json-developer-shortcut").trace()
            return accepted
        if os.path.exists(document):
            result = document_extract.extract_fields_from_file_with_trace(
                document, wanted_fields, text_extractor=self.text_extractor,
                field_extractor=self.document_client)
            self.last_document_trace = result.trace()
            if not result.accepted_json:
                raise llm.ModelRefusal("no requested fields found in %s" % document)
            return result.accepted_json
        return super().extract_fields(document, wanted_fields)

    def analyse_case(self, context):
        if self.case_analysis_client is None:
            raise llm.ModelRefusal("case analysis LLM is not enabled")
        return self.case_analysis_client.analyse_case(context)


class ChatCompletionsIntakeClient(object):
    """OpenAI-compatible JSON extractor for natural-language intake emails."""

    def __init__(self, api_key, model, base_url="https://api.deepseek.com", timeout=20):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.model_name = model

    def parse_intake_candidate(self, text, slot_specs):
        return self._complete_json(_intake_messages(text, slot_specs))

    def repair_intake_candidate(self, text, slot_specs, candidate, errors, accepted):
        return self._complete_json(_repair_messages(text, slot_specs, candidate, errors, accepted))

    def parse_intake(self, text, slot_specs):
        return ingress.IntakeIngressInterpreter(self).parse(text, slot_specs).accepted_json

    def _complete_json(self, messages):
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        try:
            req = urllib.request.Request(
                self.base_url + "/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": "Bearer " + self.api_key,
                    "Content-Type": "application/json",
                })
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError, KeyError) as exc:
            raise llm.ModelRefusal("LLM chat completion failed: %s" % exc)
        try:
            content = raw["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise llm.ModelRefusal("LLM intake response was not JSON: %s" % exc)
        if not isinstance(parsed, dict):
            raise llm.ModelRefusal("LLM intake response was not a JSON object")
        return parsed


class ChatCompletionsDocumentClient(ChatCompletionsIntakeClient):
    """OpenAI-compatible extractor for missing fields in OCR/document text."""

    def parse_document_candidate(self, text, wanted_fields):
        return self._complete_json(_document_messages(text, wanted_fields))


class ChatCompletionsCaseAnalysisClient(ChatCompletionsIntakeClient):
    """OpenAI-compatible candidate generator for whole-case adviser questions."""

    def analyse_case(self, context):
        parsed = self._complete_json(_case_analysis_messages(context))
        observations = parsed.get("observations", [])
        if not isinstance(observations, list):
            raise llm.ModelRefusal("LLM case analysis observations was not a list")
        return observations


class MergedIntakeInterpreter(object):
    def __init__(self, parsers):
        self.parsers = parsers

    def parse(self, text, slot_specs):
        combined = {}
        traces = []
        repair_attempts = 0
        errors = []
        model_names = []
        for parser in self.parsers:
            try:
                result = ingress.IntakeIngressInterpreter(parser).parse(text, slot_specs)
            except llm.ModelRefusal as exc:
                errors.append({"field": "_parser", "error": str(exc)})
                continue
            traces.append(result)
            repair_attempts += result.repair_attempts
            if result.model_name:
                model_names.append(result.model_name)
            for key, value in result.accepted_json.items():
                if key not in combined:
                    combined[key] = value
        candidate = {}
        rejected = {}
        for trace in traces:
            candidate.update(trace.candidate_json)
            rejected.update(trace.rejected_json)
            errors.extend(trace.validation_errors)
        status = "applied" if combined and not rejected else (
            "partially_applied" if combined else "rejected")
        return ingress.IngressResult(
            "provide_intake_facts",
            candidate_json=candidate,
            accepted_json=combined,
            rejected_json=dict((k, v) for k, v in rejected.items() if k not in combined),
            validation_errors=errors,
            repair_attempts=repair_attempts,
            status=status,
            model_name="+".join(model_names) if model_names else None,
            raw_input=text,
        )


def extract_intake_candidates(text, slot_specs):
    cleaned = _clean(text)
    raw = {}
    wanted = set(spec["id"] for spec in slot_specs)
    lower = cleaned.lower()

    if "applicant_name" in wanted:
        name = _extract_name(cleaned)
        if name:
            raw["applicant_name"] = name
    if "nationality" in wanted:
        nationality = _extract_nationality(cleaned)
        if nationality:
            raw["nationality"] = nationality
    if "trip_start" in wanted or "trip_end" in wanted:
        dates = _extract_dates(cleaned)
        if "trip_start" in wanted and len(dates) >= 1:
            raw["trip_start"] = dates[0]
        if "trip_end" in wanted and len(dates) >= 2:
            raw["trip_end"] = dates[1]
    if "visit_purpose" in wanted:
        purpose = _extract_visit_purpose(lower)
        if purpose:
            raw["visit_purpose"] = purpose
    if "has_uk_settled_relative" in wanted:
        relative = _extract_uk_relative(lower)
        if relative is not None:
            raw["has_uk_settled_relative"] = relative
    if "employment_status" in wanted:
        employment = _extract_employment(lower)
        if employment:
            raw["employment_status"] = employment
    if "third_party_funding" in wanted:
        funding = _extract_third_party_funding(lower)
        if funding is not None:
            raw["third_party_funding"] = funding
    if "prior_uk_refusal" in wanted:
        refusal = _extract_prior_refusal(lower)
        if refusal is not None:
            raw["prior_uk_refusal"] = refusal
    if "estimated_trip_cost_gbp" in wanted:
        cost = _extract_cost(lower)
        if cost is not None:
            raw["estimated_trip_cost_gbp"] = cost
    return raw


def _intake_messages(text, slot_specs):
        wanted = [{
            "id": spec["id"],
            "type": spec.get("type", "text"),
            "values": spec.get("values", []),
            "prompt_hint": spec.get("prompt_hint"),
        } for spec in slot_specs]
        return [
            {"role": "system", "content": (
                "Extract UK visitor visa intake facts from the user's email. "
                "Return only a JSON object. Include only fields that are explicitly "
                "stated or strongly implied. Do not invent missing values. "
                "Use the provided slot ids exactly.")},
            {"role": "user", "content": json.dumps({
                "slots": wanted,
                "email": text,
            }, ensure_ascii=False)},
        ]


def _repair_messages(text, slot_specs, candidate, errors, accepted):
    return [
        {"role": "system", "content": (
            "Repair a JSON object for UK visa intake extraction. Only fix fields "
            "that were present in the original email. Do not invent missing facts. "
            "Return only a JSON object using the requested slot ids.")},
        {"role": "user", "content": json.dumps({
            "slots": [{
                "id": spec["id"],
                "type": spec.get("type", "text"),
                "values": spec.get("values", []),
                "prompt_hint": spec.get("prompt_hint"),
            } for spec in slot_specs],
            "email": text,
            "candidate_json": candidate,
            "accepted_json": accepted,
            "validation_errors": errors,
        }, ensure_ascii=False)},
    ]


def _document_messages(text, wanted_fields):
    return [
        {"role": "system", "content": (
            "Extract only the requested fields from OCR/document text for a UK "
            "visa evidence file. Return only a JSON object. Use exactly the "
            "requested field ids as keys. If a field is not present, omit it. "
            "Do not infer, translate, invent, judge document sufficiency, or "
            "return any unrequested keys.")},
        {"role": "user", "content": json.dumps({
            "requested_fields": list(wanted_fields),
            "document_text": text,
        }, ensure_ascii=False)},
    ]


def _case_analysis_messages(context):
    facts = context.get("facts") or {}
    allowed_limbs = context.get("allowed_limbs") or []
    dimensions = context.get("analysis_dimensions") or []
    global_rules = context.get("global_rules") or []
    allowed_actions = context.get("allowed_question_actions") or []
    prohibited_actions = context.get("prohibited_question_actions") or []
    output_contract = context.get("output_contract") or {}
    time_basis = context.get("time_basis")
    fact_list = [{"source": key, "value": value} for key, value in sorted(facts.items())]
    return [
        {"role": "system", "content": (
            "You are preparing UK visitor visa whole-case review notes for a human adviser. "
            "Use the supplied analysis_dimensions as the review rubric; do not add unrelated "
            "dimensions. "
            "Return only a JSON object with an observations array. Each observation must have "
            "dimension_id, limb, observation_type, observation, evidence_refs, missing_context, "
            "and source_refs. question is optional: include it only when the adviser may need "
            "to ask the client for more information. "
            "Use only facts supplied by the user message. Each evidence_ref must copy source "
            "and value exactly from fact_list. Do not shorten, rename, translate, or infer fact "
            "sources. For example, use intake.trip_length_days, not trip_length_days. "
            "For bank evidence, ask for the latest available/current statement at application "
            "time or an explanation of deposits; do not ask for future-month statements or "
            "statements close to the travel date. "
            "Do not invent facts. Do not predict an "
            "outcome, score the case, or say whether the evidence is sufficient or insufficient. "
            "Prefer at most five concise observations. It is valid to return no observations "
            "or observations without questions when no follow-up is needed.")},
        {"role": "user", "content": json.dumps({
            "allowed_limbs": list(allowed_limbs),
            "time_basis": time_basis,
            "analysis_dimensions": dimensions,
            "global_rules": global_rules,
            "allowed_question_actions": allowed_actions,
            "prohibited_question_actions": prohibited_actions,
            "output_contract": output_contract,
            "fact_list": fact_list,
            "output_schema": {
                "observations": [{
                    "dimension_id": "one of analysis_dimensions[].id",
                    "limb": "one of allowed_limbs",
                    "observation_type": "one of output_contract.allowed_observation_types",
                    "observation": "evidence-backed adviser review note",
                    "evidence_refs": [{"source": "fact key", "value": "exact fact value"}],
                    "missing_context": "what context the adviser may need",
                    "question": "optional client-facing follow-up question; omit or empty if none",
                    "source_refs": ["one or more refs copied from selected dimension.source_refs"],
                }]
            },
        }, ensure_ascii=False)},
    ]


def intake_client_from_env():
    api_key = os.environ.get("VISA_AGENT_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    model = os.environ.get("VISA_AGENT_LLM_MODEL") or os.environ.get("DEEPSEEK_MODEL") or "v4flash"
    model = _normalise_model_name(model)
    base_url = os.environ.get("VISA_AGENT_LLM_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    return ChatCompletionsIntakeClient(
        api_key=api_key, model=model, base_url=base_url,
        timeout=_timeout_from_env())


def document_client_from_env():
    api_key = os.environ.get("VISA_AGENT_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    model = os.environ.get("VISA_AGENT_LLM_MODEL") or os.environ.get("DEEPSEEK_MODEL") or "v4flash"
    model = _normalise_model_name(model)
    base_url = os.environ.get("VISA_AGENT_LLM_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    return ChatCompletionsDocumentClient(
        api_key=api_key, model=model, base_url=base_url,
        timeout=_timeout_from_env())


def case_analysis_client_from_env():
    if not _env_enabled("VISA_AGENT_CASE_ANALYSIS_LLM"):
        return None
    api_key = os.environ.get("VISA_AGENT_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    model = os.environ.get("VISA_AGENT_LLM_MODEL") or os.environ.get("DEEPSEEK_MODEL") or "v4flash"
    model = _normalise_model_name(model)
    base_url = os.environ.get("VISA_AGENT_LLM_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    return ChatCompletionsCaseAnalysisClient(
        api_key=api_key, model=model, base_url=base_url,
        timeout=_timeout_from_env())


def _env_enabled(name):
    return str(os.environ.get(name, "")).strip().lower() in ("1", "true", "yes", "on")


def _timeout_from_env(default=20):
    try:
        return int(os.environ.get("VISA_AGENT_LLM_TIMEOUT", default))
    except (TypeError, ValueError):
        return default


def _normalise_model_name(model):
    aliases = {
        "v4flash": "deepseek-v4-flash",
        "v4_flash": "deepseek-v4-flash",
        "deepseek-v4flash": "deepseek-v4-flash",
        "v4pro": "deepseek-v4-pro",
        "v4_pro": "deepseek-v4-pro",
    }
    key = re.sub(r"[^a-z0-9]+", "_", str(model or "").lower()).strip("_")
    return aliases.get(key, model)


def _clean(text):
    lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if any(pattern.match(stripped) for pattern in QUOTE_CUTOFFS):
            break
        if stripped.startswith(">"):
            continue
        if stripped.lower() in ("sent from my iphone", "sent from my android"):
            continue
        lines.append(stripped)
    return " ".join(" ".join(lines).split())


def _normalise_enum(value, allowed):
    token = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    aliases = {
        "family": "family_visit",
        "family_visit": "family_visit",
        "visiting_family": "family_visit",
        "self_employed": "self_employed",
        "freelance": "self_employed",
        "freelancer": "self_employed",
        "employed": "employed",
        "student": "student",
        "retired": "retired",
        "unemployed": "unemployed",
    }
    candidate = aliases.get(token, token)
    return candidate if candidate in allowed else value


def _extract_name(text):
    patterns = [
        r"\bmy name is\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,4})",
        r"\bi am\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,4})",
        r"\bi'm\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,4})",
        r"我是\s*([\u4e00-\u9fffA-Za-z ]{2,40})",
        r"我叫\s*([\u4e00-\u9fffA-Za-z ]{2,40})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _trim_name(match.group(1))
    # Common demo shape: "Hi, Mei Ling Chen here ..."
    match = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){2})\b", text)
    return _trim_name(match.group(1)) if match else None


def _trim_name(value):
    value = re.split(r"\b(and|from|with|who|want|need|applying|apply)\b", value)[0]
    value = re.split(r"[,.，。]", value)[0]
    return " ".join(value.strip().split())


def _extract_nationality(text):
    lower = text.lower()
    if "chinese passport" in lower or "china passport" in lower or "中国护照" in text:
        return "Chinese"
    match = re.search(r"\b([A-Z][a-z]+)\s+passport\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\bnationality\s*[:：=-]?\s*([A-Za-z ]+)", text, re.IGNORECASE)
    if match:
        return re.split(r"[,.，。]", match.group(1).strip())[0]
    return None


def _extract_dates(text):
    found = []
    for match in re.finditer(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", text):
        found.append("%04d-%02d-%02d" % (
            int(match.group(1)), int(match.group(2)), int(match.group(3))))
    for match in re.finditer(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})\b", text):
        found.append("%04d-%02d-%02d" % (
            int(match.group(3)), int(match.group(2)), int(match.group(1))))
    return _dedupe(found)


def _extract_visit_purpose(lower):
    if any(word in lower for word in ["family", "sister", "brother", "mother", "father", "relative", "visit my"]):
        return "family_visit"
    if "tour" in lower or "holiday" in lower:
        return "tourism"
    if "business" in lower:
        return "business"
    if "medical" in lower:
        return "medical"
    if "study" in lower:
        return "study_short"
    return None


def _extract_uk_relative(lower):
    if any(word in lower for word in ["sister in the uk", "brother in the uk", "relative in the uk", "family in the uk", "uk relative"]):
        return True
    if "no relative" in lower or "no family in the uk" in lower:
        return False
    return None


def _extract_employment(lower):
    if "self-employed" in lower or "self employed" in lower or "freelance" in lower:
        return "self_employed"
    if "employed" in lower or "employee" in lower:
        return "employed"
    if "student" in lower:
        return "student"
    if "retired" in lower:
        return "retired"
    if "unemployed" in lower or "not working" in lower:
        return "unemployed"
    return None


def _extract_third_party_funding(lower):
    if any(phrase in lower for phrase in ["self-funded", "self funded", "pay myself", "funding it myself", "i pay"]):
        return False
    if any(phrase in lower for phrase in ["sponsor pays", "paid by", "someone else", "my sister will pay", "my family will pay"]):
        return True
    return None


def _extract_prior_refusal(lower):
    if "no refusal" in lower or "never refused" in lower or "no visa refusal" in lower:
        return False
    if "refused" in lower or "refusal" in lower:
        return True
    return None


def _extract_cost(lower):
    match = re.search(r"(?:gbp|£)\s*([0-9][0-9,]*(?:\.\d+)?)", lower)
    if not match:
        match = re.search(r"([0-9][0-9,]*(?:\.\d+)?)\s*(?:gbp|pounds)", lower)
    return match.group(1) if match else None


def _dedupe(values):
    out = []
    for value in values:
        if value not in out:
            out.append(value)
    return out
