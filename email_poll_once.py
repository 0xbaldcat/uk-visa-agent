#!/usr/bin/env python3
"""Fetch unseen mailbox messages once and route them through the Engine.

This is the live mailbox loop for the PoC. It uses the same `Engine` as the
offline demo; SMTP/IMAP credentials come from `VISA_AGENT_*` environment vars.

For document extraction in a no-LLM local demo, attach JSON files whose filenames
start with the evidence id, e.g. `passport.json` or `home_ties_evidence.json`.
The JSON object should contain the fields that the checklist expects.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import channels
import checklist as checklist_mod
import email_bridge
import email_model
import engine as engine_mod
import real_email
import store as store_mod


def main():
    settings = real_email.EmailSettings.from_env()
    case_id = os.environ.get("VISA_AGENT_CASE_ID", "case-001")
    db_path = os.environ.get("VISA_AGENT_DB_PATH", "visa-agent.sqlite3")
    attachment_dir = os.environ.get("VISA_AGENT_ATTACHMENT_DIR", "inbound-attachments")

    cl = checklist_mod.load_route("visitor_family_visit")
    st = store_mod.Store(db_path)
    if st.get_case(case_id) is None:
        st.create_case(case_id, cl.route_id)

    email_channel = real_email.RealEmailChannel(settings)
    router = channels.Router(
        channels.WhatsAppChannel(), email_channel,
        preferred_conversation_channel="email")
    model = email_model.EmailDemoModel()
    eng = engine_mod.Engine(st, cl, model, router=router)
    poller = email_bridge.EmailPoller(
        eng, st, default_case_id=case_id, attachment_dir=attachment_dir)

    raw_rows = real_email.fetch_unseen_raw(settings)
    results = []
    for row in raw_rows:
        produced = poller.poll_raw([row["raw"]])
        results.extend(produced)
        real_email.mark_seen(settings, row["imap_id"])
    print("processed emails:", len(raw_rows))
    print("engine results:", len(results))
    for result in results:
        sent = result.get("sent") or {}
        print("%s -> %s %s" % (
            result["action"].kind,
            sent.get("channel", "-"),
            sent.get("subject", "")))


if __name__ == "__main__":
    main()
