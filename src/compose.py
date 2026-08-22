"""Turning a derived Action into words.

The state machine decides *what* to say; this decides *how*. Keeping them apart is
why a model can be swapped in for phrasing without ever gaining influence over what
the client is asked for next.

Every sentence about a requirement carries the reason, because "send me X" without
"because Y" is what makes a document-collection bot feel like a form.
"""
from typing import Dict, Any, Optional


def _why(ev):
    why = ev.get("why")
    return " ".join(why.split()) if why else None


def compose(action, checklist, case, model=None):
    # type: (Any, Any, Any, Optional[Any]) -> str
    kind = action.kind

    if kind == "ask_slot":
        grouped = action.payload.get("slots") or []
        if len(grouped) > 1:
            questions = []
            for slot_id in grouped:
                spec = checklist.slot(slot_id) or {}
                if spec.get("question"):
                    questions.append("- %s" % spec["question"])
            if questions:
                return ("Thanks, I can help prepare the UK visitor visa materials. "
                        "To build the right checklist, please reply with these details:\n%s"
                        % "\n".join(questions))
        spec = checklist.slot(action.slot) or {}
        # Slots carry their own question text: a bool or enum slot cannot be
        # phrased by slotting a noun into "your ___?" without reading oddly.
        question = spec.get("question")
        if question:
            return question
        return "Could you tell me your %s?" % spec.get(
            "prompt_hint", action.slot.replace("_", " "))

    if kind == "request_evidence":
        items = _missing_evidence_items(checklist, case)
        if not items:
            items = [checklist.evidence(action.evidence_id) or {
                "id": action.evidence_id,
                "label": action.evidence_id,
            }]
        lines = [
            "Here is what I still need for your visa materials. Please send whichever files you already have; I will check them one by one.",
            "",
        ]
        for ev in items:
            lines.append("- %s" % ev.get("label", ev.get("id")))
            why = _why(ev)
            if why:
                lines.append("  Note: %s" % why)
            lines.append("")
        return "\n".join(lines).rstrip()

    if kind == "request_resupply":
        ev = checklist.evidence(action.evidence_id) or {}
        problems = action.payload.get("failures", [])
        blocking = [f for f in problems if not f.get("advisory")]
        shown = blocking or problems
        bullets = "\n".join("  - %s" % f.get("message") for f in shown)
        return ("Thanks - I've had a look at this (%s) and there's a problem I need "
                "to flag before we use it:\n%s\n\nCould you send a version that "
                "covers this?" % (ev.get("label", action.evidence_id), bullets))

    if kind == "deliver_pack":
        return ("That's everything on your list. I've put together your document "
                "pack, a draft of your form answers, a draft cover letter and a "
                "quality-check report. They're in your email. A human adviser "
                "reviews these before you rely on them.")

    if kind == "await_human":
        if action.reason == "pack_delivered":
            return ("Your pack is with an adviser for review. I'll come back to you "
                    "once they've signed it off.")
        return ("I've passed this to a human adviser to look at - I'd rather not "
                "guess on this one. They'll be in touch.")

    if kind == "escalate":
        return ("I'm not confident enough to answer that myself, so I'm passing it "
                "to a human adviser.")

    raise ValueError("no phrasing for action kind %r" % kind)


def risk_prompt(risk):
    """The one client-facing sentence a risk factor is allowed to produce."""
    return risk["ask"]


def _missing_evidence_items(checklist, case):
    supplied = getattr(case, "evidence", {}) or {}
    return [
        ev for ev in checklist.required_evidence(getattr(case, "slots", {}) or {})
        if ev["id"] not in supplied
    ]
