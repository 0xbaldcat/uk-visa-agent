"""Channel adapters.

Two channels with genuinely different physics, so the split is not cosmetic:

  WhatsApp - low friction, mobile, good for interview questions, chasing, photo
             uploads. Constrained by Meta's 24-hour customer service window: once
             it closes, only pre-approved template messages may be sent.
  Email    - structured, archivable, carries attachments. Good for the checklist,
             the QC report and the final pack.

Rule of thumb encoded here: conversation on WhatsApp, documents by email.

The window is modelled rather than mocked away, because it changes the design of
the chasing logic -- you cannot simply retry until the client answers.
"""
from datetime import datetime, timedelta
from email.utils import make_msgid
from typing import Optional, Dict, Any, List

WHATSAPP_WINDOW_HOURS = 24


class OutsideWindow(Exception):
    """Free-form send attempted after the 24h window closed."""


class Channel(object):
    name = "base"
    supports_attachments = False

    def send(self, case_id, body, kind="session", attachments=None):
        raise NotImplementedError


class EmailChannel(Channel):
    name = "email"
    supports_attachments = True

    def __init__(self, sink=None):
        self.sink = sink if sink is not None else []
        self._thread_context = {}

    def set_thread_context(self, case_id, in_reply_to=None, references=None, to_addr=None):
        self._thread_context[case_id] = {
            "in_reply_to": in_reply_to,
            "references": list(references or []),
            "to_addr": to_addr,
        }

    def send(self, case_id, body, kind="session", attachments=None):
        context = self._thread_context.pop(case_id, {})
        message_id = make_msgid(idstring="visa-agent", domain="visa-agent.local")
        references = list(context.get("references") or [])
        if context.get("in_reply_to") and context["in_reply_to"] not in references:
            references.append(context["in_reply_to"])
        msg = {"channel": "email", "case_id": case_id, "body": body,
               "attachments": list(attachments or []),
               "message_id": message_id,
               "in_reply_to": context.get("in_reply_to"),
               "references": references,
               "to_addr": context.get("to_addr")}
        self.sink.append(msg)
        return msg


class WhatsAppChannel(Channel):
    """Models the customer-service window.

    Real send is stubbed; the window arithmetic is not, because that is the part
    that constrains the product.
    """
    name = "whatsapp"
    supports_attachments = False

    def __init__(self, sink=None, approved_templates=None):
        self.sink = sink if sink is not None else []
        # Templates must be approved by Meta in advance. Sending an unapproved
        # template outside the window fails in production, so it fails here too.
        self.approved_templates = set(approved_templates or [])
        self._last_inbound = {}      # case_id -> datetime

    def note_inbound(self, case_id, at=None):
        """A client message opens (or resets) the window."""
        self._last_inbound[case_id] = at or datetime.utcnow()

    def window_open(self, case_id, now=None):
        last = self._last_inbound.get(case_id)
        if last is None:
            return False
        now = now or datetime.utcnow()
        return (now - last) < timedelta(hours=WHATSAPP_WINDOW_HOURS)

    def window_closes_at(self, case_id):
        last = self._last_inbound.get(case_id)
        return None if last is None else last + timedelta(hours=WHATSAPP_WINDOW_HOURS)

    def send(self, case_id, body, kind="session", template_name=None,
             attachments=None, now=None):
        if kind == "session":
            if not self.window_open(case_id, now=now):
                raise OutsideWindow(
                    "the 24h window for case %s has closed; only an approved "
                    "template may be sent" % case_id)
        elif kind == "template":
            if template_name not in self.approved_templates:
                raise OutsideWindow(
                    "template %r is not approved; it cannot be sent" % template_name)
        else:
            raise ValueError("unknown message kind %r" % kind)

        msg = {"channel": "whatsapp", "case_id": case_id, "body": body,
               "kind": kind, "template_name": template_name}
        self.sink.append(msg)
        return msg


class Router(object):
    """Picks the channel for an action.

    Conversation goes to WhatsApp; anything carrying a document goes to email.
    If WhatsApp's window has closed, conversational output degrades to an approved
    template rather than being silently dropped -- failure mode "no silent failure".
    """

    DOCUMENT_ACTIONS = {"deliver_pack"}

    def __init__(self, whatsapp, email, preferred_conversation_channel="whatsapp"):
        self.whatsapp = whatsapp
        self.email = email
        if preferred_conversation_channel not in ("whatsapp", "email"):
            raise ValueError("unknown conversation channel %r" % preferred_conversation_channel)
        self.preferred_conversation_channel = preferred_conversation_channel

    def route(self, action_kind):
        if action_kind in self.DOCUMENT_ACTIONS:
            return self.email
        if self.preferred_conversation_channel == "email":
            return self.email
        return self.whatsapp

    def send(self, case_id, action_kind, body, attachments=None,
             fallback_template="visa_docs_reminder", now=None):
        channel = self.route(action_kind)
        if channel is self.email:
            return self.email.send(case_id, body, attachments=attachments)
        try:
            return self.whatsapp.send(case_id, body, kind="session", now=now)
        except OutsideWindow:
            return self.whatsapp.send(
                case_id,
                "We still need a couple of documents for your application - "
                "reply here and we'll pick up where we left off.",
                kind="template", template_name=fallback_template, now=now)
