"""Deliverable assembly.

All four deliverables are rendered by code from validated case fields. The model
never writes a deliverable; at most it supplies prose for narrative paragraphs of
the cover letter, and even that is passed through the fabrication guard first.

This is failure mode 3 from the design notes: if the model writes the pack, the
completeness statement becomes an opinion. Here it is a computation.
"""
from datetime import date
from typing import Dict, Any, List, Optional

import diagnose
import facts


def _src(checklist, source_id):
    """Human-readable provenance for a source id."""
    reg = (checklist.sources or {}).get(source_id or "", {})
    title = reg.get("title") or source_id or "unsourced"
    url = reg.get("url")
    kind = reg.get("kind")
    label = title if not kind else "%s [%s]" % (title, kind)
    return "%s - %s" % (label, url) if url else label


# --- 1. personalised document checklist ---------------------------------

def document_checklist(checklist, case):
    # type: (Any, Any) -> Dict[str, Any]
    """Every required item with why it is needed, its state, and its provenance."""
    items = []
    for ev in checklist.required_evidence(case.slots):
        rec = case.evidence.get(ev["id"])
        if rec is None:
            status = "outstanding"
            problems = []
        else:
            raw = rec.get("failures") or []
            blocking = [f for f in raw if not f.get("advisory")]
            advis = [f for f in raw if f.get("advisory")]
            if blocking:
                status = "needs_replacement"
            elif advis:
                status = "accepted_with_note"
            else:
                status = "accepted"
            problems = raw
        items.append({
            "id": ev["id"],
            "label": ev["label"],
            "category": ev.get("category"),
            "why": ev.get("why"),
            "source": _src(checklist, ev.get("source")),
            "status": status,
            "problems": problems,
        })
    return {"route": checklist.route_label, "items": items}


# --- 2. application form answer draft -----------------------------------

# Only slots we actually collected map to form answers. We do not invent answers
# for questions the client never addressed.
FORM_FIELDS = [
    ("Full name (as in passport)", "applicant_name", None),
    ("Nationality", "nationality", None),
    ("Purpose of visit", "visit_purpose", None),
    ("Date of arrival in the UK", "trip_start", None),
    ("Date of departure from the UK", "trip_end", None),
    ("Who is paying for your trip", "third_party_funding",
     {True: "Third party or sponsor contributes", False: "Applicant self-funded"}),
    ("Employment status", "employment_status", None),
    ("Have you ever been refused a visa", "prior_uk_refusal", None),
]


def _render_answer(value, mapping=None):
    """Draft answers are read by the applicant, so render them as words."""
    if value is None:
        return None
    if mapping is not None and value in mapping:
        return mapping[value]
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        return value.replace("_", " ")
    return value


def form_answers(checklist, case):
    rows = []
    for label, slot, mapping in FORM_FIELDS:
        value = case.slots.get(slot)
        rows.append({
            "question": label,
            "answer": _render_answer(value, mapping=mapping),
            "raw": value,
            "state": "answered" if value is not None else "not_yet_collected",
        })
    return {
        "rows": rows,
        "note": ("Draft answers for the applicant to enter themselves on the GOV.UK "
                 "service. This tool does not submit applications."),
    }


# --- 3. cover letter ----------------------------------------------------

def cover_letter(checklist, case, narrative=None):
    # type: (Any, Any, Optional[Dict[str, str]]) -> Dict[str, Any]
    """Structured cover letter.

    Fixed skeleton, code-rendered facts. `narrative` optionally carries
    model-written prose per risk factor; each paragraph is fact-checked against
    what the client supplied before it is allowed into the letter.
    """
    ledger = facts.FactLedger()
    ledger.add_case(case)

    risks = diagnose.active_risks(checklist, case)
    ties = diagnose.home_tie_coverage(checklist, case)

    sections = []
    sections.append({
        "heading": "Purpose and duration of the visit",
        "body": _purpose_para(case),
        "generated": False,
    })

    undrafted = []
    for risk in risks:
        para = (narrative or {}).get(risk["id"])
        if not para:
            # No drafted paragraph: omit the section.
            #
            # The obvious fallback -- reuse the risk `observation` -- would be a bad
            # bug. Observations are written for the adviser and phrased in the
            # caseworker's framing ("raises doubt about the intention to leave").
            # This letter is read by the decision-maker, so emitting that text would
            # have the applicant arguing against themselves. Silence is safe; a
            # weakness argued in Home Office language is not.
            undrafted.append(risk["id"])
            continue
        # Model prose: admitted only if every factual token traces to the client.
        facts.enforce(para, ledger, where="cover letter (%s)" % risk["id"])
        sections.append({
            "heading": risk["label"],
            "body": para,
            "generated": True,
            "source": _src(checklist, risk.get("source")),
        })

    if ties["covered"]:
        sections.append({
            "heading": "Ties to the home country",
            "body": "Evidence has been provided covering: %s."
                    % ", ".join(t["label"].lower() for t in ties["covered"]),
            "generated": False,
        })

    return {
        "applicant": case.slots.get("applicant_name"),
        "sections": sections,
        # Surfaced so the reviewer sees which risks the letter is currently silent
        # on, rather than silently shipping a letter that ignores them.
        "risks_not_yet_addressed": undrafted,
        "disclaimer": ("Prepared as a draft for review. It states the applicant's "
                       "circumstances and does not assert that they meet the "
                       "requirements - that assessment is for the caseworker."),
    }


def _purpose_para(case):
    purpose = (case.slots.get("visit_purpose") or "visit").replace("_", " ")
    start, end = case.slots.get("trip_start"), case.slots.get("trip_end")
    days = diagnose.stay_length_days(case.slots)
    if start and end and days is not None:
        return ("The applicant intends to travel to the UK for a %s, arriving %s and "
                "departing %s, a stay of %d days." % (purpose, start, end, days))
    return "The applicant intends to travel to the UK for a %s." % purpose


# --- 4. QC report -------------------------------------------------------

def qc_report(checklist, case, today=None):
    # type: (Any, Any, Optional[date]) -> Dict[str, Any]
    """The completeness statement. Computed, never asserted by a model."""
    satisfied, missing, failing = case.outstanding(checklist)
    advisories = []
    for ev_id, rec in (case.evidence or {}).items():
        for f in (rec.get("failures") or []):
            if f.get("advisory"):
                advisories.append({"evidence": ev_id, "message": f.get("message"),
                                   "note": f.get("note")})

    risks = diagnose.active_risks(checklist, case)
    ties = diagnose.home_tie_coverage(checklist, case)
    unsourced = checklist.unsourced()

    return {
        "generated_on": str(today or date.today()),
        "route": checklist.route_label,
        "config_version": checklist.config_version,
        "pack_complete": not missing and not failing,
        "accepted": satisfied,
        "outstanding": missing,
        "needs_replacement": failing,
        "advisories": advisories,
        "risk_observations": risks,
        "home_tie_coverage": ties,
        "funds_picture": diagnose.funds_picture(case),
        "unsourced_rules": unsourced,
        "risk_observations_needing_narrative": [r["id"] for r in risks],
        "limits": [
            "Completeness means every configured item is present and passed its "
            "checks. It is not a prediction of the outcome.",
            "No sufficiency judgement is made on the genuine visitor test; that is "
            "for the reviewing adviser and ultimately the caseworker.",
            "This tool does not submit the application.",
        ],
    }


def build_pack(checklist, case, narrative=None, today=None):
    """All four deliverables."""
    return {
        "document_checklist": document_checklist(checklist, case),
        "form_answers": form_answers(checklist, case),
        "cover_letter": cover_letter(checklist, case, narrative=narrative),
        "qc_report": qc_report(checklist, case, today=today),
    }
