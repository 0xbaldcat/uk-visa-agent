"""Case state machine.

The spine of the system. The next action is *derived* from persisted case state,
never decided by a model. Given the same case row, `next_action` always returns
the same thing -- that determinism is what makes delivery auditable.
"""
from enum import Enum
from typing import Optional, List, Dict, Any


class Stage(str, Enum):
    INTAKE = "intake"                 # collecting the facts needed to pick a checklist
    CHECKLIST_READY = "checklist"     # checklist instantiated, nothing collected yet
    COLLECTING = "collecting"         # gathering / re-gathering evidence
    REMEDIATION = "remediation"       # something failed validation, client must resupply
    ASSEMBLING = "assembling"         # all checks pass, building the pack
    HUMAN_REVIEW = "human_review"     # pack built, waiting on a human consultant
    ESCALATED = "escalated"           # agent refused to proceed, human must intervene


# Allowed transitions. Anything not listed is a bug, not a judgement call.
TRANSITIONS = {
    Stage.INTAKE:          {Stage.CHECKLIST_READY, Stage.ESCALATED},
    Stage.CHECKLIST_READY: {Stage.COLLECTING, Stage.ESCALATED},
    Stage.COLLECTING:      {Stage.REMEDIATION, Stage.ASSEMBLING, Stage.ESCALATED},
    Stage.REMEDIATION:     {Stage.COLLECTING, Stage.ASSEMBLING, Stage.ESCALATED},
    Stage.ASSEMBLING:      {Stage.HUMAN_REVIEW, Stage.ESCALATED},
    Stage.HUMAN_REVIEW:    {Stage.COLLECTING, Stage.ESCALATED},
    Stage.ESCALATED:       {Stage.COLLECTING},
}


class IllegalTransition(Exception):
    pass


def transition(current: Stage, target: Stage) -> Stage:
    if target not in TRANSITIONS[current]:
        raise IllegalTransition("%s -> %s is not an allowed transition" % (current, target))
    return target


class Action(object):
    """A derived instruction for the composer. Carries no prose."""

    def __init__(self, kind, slot=None, evidence_id=None, reason=None, payload=None):
        # type: (str, Optional[str], Optional[str], Optional[str], Optional[Dict[str, Any]]) -> None
        self.kind = kind                # ask_slot | request_evidence | request_resupply
                                        # | deliver_pack | escalate | await_human
        self.slot = slot
        self.evidence_id = evidence_id
        self.reason = reason
        self.payload = payload or {}

    def __repr__(self):
        return "Action(%s, slot=%s, evidence=%s, reason=%s)" % (
            self.kind, self.slot, self.evidence_id, self.reason)

    def __eq__(self, other):
        if not isinstance(other, Action):
            return NotImplemented
        return ((self.kind, self.slot, self.evidence_id)
                == (other.kind, other.slot, other.evidence_id))


def next_action(case, checklist):
    # type: (Any, Any) -> Action
    """Derive the single next action from persisted state.

    Pure function of (case, checklist). No model call, no randomness.
    """
    if case.stage == Stage.ESCALATED:
        return Action("await_human", reason=case.escalation_reason)

    # 1. Intake slots first, in declared order, so the conversation has a stable shape.
    missing_slot = checklist.first_missing_slot(case.slots)
    if missing_slot is not None:
        return Action("ask_slot", slot=missing_slot["id"],
                      payload={"slots": _intake_prompt_group(
                          missing_slot["id"], checklist.missing_slots(case.slots))})

    # 2. Anything that failed validation outranks anything not yet supplied:
    #    fixing a wrong document is cheaper for the client than opening a new front.
    failed = case.first_failed_evidence(checklist)
    if failed is not None:
        ev_id, failures = failed
        return Action("request_resupply", evidence_id=ev_id,
                      reason="failed_validation", payload={"failures": failures})

    # 3. Then evidence that is required-and-absent.
    missing_ev = checklist.first_missing_evidence(case.slots, case.evidence)
    if missing_ev is not None:
        return Action("request_evidence", evidence_id=missing_ev["id"])

    # 4. Everything required is present and passing.
    if case.stage == Stage.HUMAN_REVIEW:
        return Action("await_human", reason="pack_delivered")
    return Action("deliver_pack")


INTAKE_GROUPS = [
    ["applicant_name", "nationality", "trip_start", "trip_end", "visit_purpose",
     "has_uk_settled_relative"],
    ["employment_status", "third_party_funding", "prior_uk_refusal",
     "estimated_trip_cost_gbp"],
]


def _intake_prompt_group(first_missing_id, missing_slots):
    missing_ids = [slot["id"] for slot in missing_slots]
    for group in INTAKE_GROUPS:
        if first_missing_id in group:
            return [slot_id for slot_id in group if slot_id in missing_ids]
    return [first_missing_id]
