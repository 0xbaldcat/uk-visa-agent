"""Checklist loading.

Requirements live in versioned YAML, not in a prompt. This module is the only
place allowed to answer "what does this application need", which is how we keep
failure mode #1 (model inventing a requirement) structurally impossible.
"""
import os
import copy
from typing import Optional, Dict, Any, List

import yaml

PLACEHOLDER = "UNVERIFIED_PLACEHOLDER"


class UnsourcedRequirement(Exception):
    """A requirement with no provenance. Fatal by design."""


class Checklist(object):
    def __init__(self, data, path=None, sources=None):
        self.data = data
        self.path = path
        registry = sources or {}
        self.sources = registry.get("sources", {})
        # Fees, processing times and the like. Research flagged these as volatile,
        # so they live as dated data and are always rendered with their as_of date
        # rather than being baked into code or prose.
        self.volatile = registry.get("volatile", {})
        self.route_id = data["route_id"]
        self.route_label = data["route_label"]
        self.config_version = data["config_version"]
        self.data_status = data.get("data_status", PLACEHOLDER)
        self.visa_type = data.get("visa_type")
        self.purposes = data.get("purposes", [])
        self.components = data.get("components", [])
        self.scope_note = data.get("scope_note")

    # --- provenance -----------------------------------------------------

    @property
    def is_verified(self):
        return self.data_status == "VERIFIED"

    def unsourced(self):
        # type: () -> List[str]
        """Every requirement whose provenance is missing or unresolvable.

        Reported in the QC report, so a pack built on scaffolding can never be
        mistaken for one built on sourced rules. A source id that does not resolve
        against the registry counts as unsourced -- a dangling reference is not
        provenance.
        """
        out = []

        def bad(source_id):
            return (source_id is None or source_id == PLACEHOLDER
                    or source_id not in self.sources)

        for ev in self.data.get("evidence", []):
            if bad(ev.get("source")):
                out.append("evidence:%s" % ev["id"])
            for chk in ev.get("checks", []):
                if "source" in chk and bad(chk["source"]):
                    out.append("evidence:%s/check:%s" % (ev["id"], chk.get("kind")))
        for rf in self.data.get("risk_factors", []):
            if bad(rf.get("source")):
                out.append("risk_factor:%s" % rf["id"])
        for limb in self.data.get("genuine_visitor_test", []):
            if bad(limb.get("source")):
                out.append("gvt:%s" % limb["id"])
        return out

    def volatile_value(self, key):
        """Never return a bare number: the as_of date travels with it."""
        entry = self.volatile.get(key)
        if entry is None:
            raise KeyError("no volatile value %r" % key)
        return entry

    def assert_verified(self):
        """Call before serving a real client. PoC deliberately does not call this."""
        missing = self.unsourced()
        if missing:
            raise UnsourcedRequirement(
                "%d requirement(s) lack a GOV.UK source: %s" % (len(missing), ", ".join(missing)))

    # --- slots ----------------------------------------------------------

    @property
    def slots(self):
        return self.data.get("intake_slots", [])

    def first_missing_slot(self, filled):
        # type: (Dict[str, Any]) -> Optional[Dict[str, Any]]
        for slot in self.slots:
            if filled.get(slot["id"]) is None:
                return slot
        return None

    def missing_slots(self, filled):
        # type: (Dict[str, Any]) -> List[Dict[str, Any]]
        return [slot for slot in self.slots if filled.get(slot["id"]) is None]

    def slot(self, slot_id):
        for s in self.slots:
            if s["id"] == slot_id:
                return s
        return None

    # --- evidence -------------------------------------------------------

    def evidence(self, evidence_id):
        for ev in self.data.get("evidence", []):
            if ev["id"] == evidence_id:
                return ev
        return None

    def _is_required(self, ev, slots):
        if ev.get("required") == "always":
            return True
        cond = ev.get("required_when")
        if not cond:
            return False
        return slots.get(cond["slot"]) == cond["equals"]

    def required_evidence(self, slots):
        # type: (Dict[str, Any]) -> List[Dict[str, Any]]
        return [ev for ev in self.data.get("evidence", []) if self._is_required(ev, slots)]

    def first_missing_evidence(self, slots, supplied):
        # type: (Dict[str, Any], Dict[str, Any]) -> Optional[Dict[str, Any]]
        for ev in self.required_evidence(slots):
            if ev["id"] not in supplied:
                return ev
        return None

    # --- ties and risk --------------------------------------------------

    def home_ties(self):
        return self.data.get("home_ties", [])

    def active_risk_flags(self, slots):
        out = []
        for rf in self.data.get("risk_flags", []):
            trig = rf.get("trigger", {})
            if slots.get(trig.get("slot")) == trig.get("equals"):
                out.append(rf)
        return out


def load(path, sources_path=None):
    # type: (str, Optional[str]) -> Checklist
    sources = None
    if sources_path and os.path.exists(sources_path):
        with open(sources_path) as fh:
            sources = yaml.safe_load(fh)
    with open(path) as fh:
        return Checklist(yaml.safe_load(fh), path=path, sources=sources)


def load_route(route_id, config_dir=None):
    config_dir = config_dir or os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
    registry_path = os.path.join(config_dir, "routes.yaml")
    if os.path.exists(registry_path):
        with open(registry_path) as fh:
            registry = yaml.safe_load(fh) or {}
        route = (registry.get("routes") or {}).get(route_id)
        if route:
            data = _compose_route(route, config_dir)
            return Checklist(data, path=registry_path,
                             sources=_load_sources(config_dir))
    return load(os.path.join(config_dir, "%s.yaml" % route_id),
                sources_path=os.path.join(config_dir, "sources.yaml"))


def _load_sources(config_dir):
    path = os.path.join(config_dir, "sources.yaml")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return yaml.safe_load(fh)


def _compose_route(route, config_dir):
    data = {
        "route_id": route["route_id"],
        "route_label": route["route_label"],
        "visa_type": route.get("visa_type"),
        "purposes": list(route.get("purposes") or []),
        "config_version": route.get("config_version", "0.0.0"),
        "data_status": route.get("data_status", PLACEHOLDER),
        "components": list(route.get("components") or []),
        "scope_note": route.get("scope_note"),
        "genuine_visitor_test": [],
        "intake_slots": [],
        "evidence": [],
        "risk_factors": [],
        "home_ties": [],
    }
    statuses = [data["data_status"]]
    for component_id in data["components"]:
        component = _load_component(config_dir, component_id)
        statuses.append(component.get("data_status", PLACEHOLDER))
        for key in ("genuine_visitor_test", "intake_slots", "evidence",
                    "risk_factors", "home_ties"):
            _merge_list_by_id(data[key], component.get(key, []))
    if any(status != "VERIFIED" for status in statuses):
        data["data_status"] = PLACEHOLDER
    return data


def _load_component(config_dir, component_id):
    path = os.path.join(config_dir, "%s.yaml" % component_id)
    if not os.path.exists(path):
        raise FileNotFoundError("route component not found: %s" % path)
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _merge_list_by_id(target, incoming):
    index = dict((item["id"], pos) for pos, item in enumerate(target))
    for item in incoming or []:
        item = copy.deepcopy(item)
        existing = index.get(item["id"])
        if existing is None:
            index[item["id"]] = len(target)
            target.append(item)
        else:
            merged = copy.deepcopy(target[existing])
            merged.update(item)
            target[existing] = merged
