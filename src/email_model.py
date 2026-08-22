"""Deterministic model adapter for the live email PoC.

This is intentionally modest. It parses the current expected slot from a plain
email reply and reads JSON attachments whose filename maps to a checklist item.
It does not claim to understand PDFs or images.
"""
import json
import os
import re

import llm

QUOTE_CUTOFFS = [
    re.compile(r"^On .+ wrote:$", re.IGNORECASE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.IGNORECASE),
    re.compile(r"^From:\s.+", re.IGNORECASE),
]


class EmailDemoModel(llm.StubModel):
    def parse_reply(self, text, expected_slot, slot_spec):
        value = _clean(text)
        if slot_spec.get("type") == "enum":
            value = _normalise_enum(value, slot_spec.get("values", []))
        return llm.coerce_slot(value, slot_spec)

    def extract_fields(self, document, wanted_fields):
        if os.path.exists(document) and document.endswith(".json"):
            with open(document) as fh:
                raw = json.load(fh)
            return dict((k, v) for k, v in raw.items() if k in wanted_fields)
        return super().extract_fields(document, wanted_fields)


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
