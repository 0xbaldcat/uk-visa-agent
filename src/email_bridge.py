"""Email poller that connects real messages to the Engine.

This is the missing bridge between SMTP/IMAP connectivity and the state machine:
raw RFC822 email is parsed, de-duplicated by RFC Message-ID, routed to a case, and
applied as either a text reply or document upload. The Engine then sends the next
reply through the configured email channel.
"""
import os
import re
from dataclasses import dataclass
from email import policy
from email.utils import parseaddr
from email.parser import BytesParser
from typing import List, Optional, Dict, Any

import state


CASE_TOKEN_RE = re.compile(r"\[visa-agent:([^\]\s]+)\]")
EVIDENCE_IDS = {
    "passport",
    "bank_statements",
    "travel_itinerary",
    "accommodation_proof",
    "sponsor_invitation_letter",
    "sponsor_status_proof",
    "sponsor_financial_evidence",
    "employment_letter",
    "self_employment_evidence",
    "home_ties_evidence",
}


@dataclass
class ParsedAttachment:
    filename: str
    content_type: str
    content: bytes
    evidence_id: Optional[str]


@dataclass
class ParsedEmail:
    from_addr: str
    message_id: str
    subject: str
    body: str
    in_reply_to: Optional[str]
    references: List[str]
    case_id: Optional[str]
    attachments: List[ParsedAttachment]


def parse_email(raw):
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    body = ""
    attachments = []
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            filename = part.get_filename() or "attachment.bin"
            attachments.append(ParsedAttachment(
                filename=filename,
                content_type=part.get_content_type(),
                content=part.get_payload(decode=True) or b"",
                evidence_id=infer_evidence_id(filename)))
        elif part.get_content_type() == "text/plain" and not body:
            body = part.get_content()
    subject = msg.get("Subject", "")
    references = _message_ids(msg.get("References"))
    in_reply_to = _first_message_id(msg.get("In-Reply-To"))
    case_id = case_from_subject(subject)
    return ParsedEmail(
        from_addr=parseaddr(msg.get("From", ""))[1],
        message_id=_first_message_id(msg.get("Message-ID")) or _fallback_id(subject, body),
        subject=subject,
        body=body.strip(),
        in_reply_to=in_reply_to,
        references=references,
        case_id=case_id,
        attachments=attachments,
    )


def case_from_subject(subject):
    match = CASE_TOKEN_RE.search(subject or "")
    return match.group(1) if match else None


def infer_evidence_id(filename):
    stem = os.path.splitext(os.path.basename(filename or ""))[0].lower()
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    for evidence_id in sorted(EVIDENCE_IDS, key=len, reverse=True):
        if stem == evidence_id or stem.startswith(evidence_id + "_"):
            return evidence_id
    aliases = {
        "passport": "passport",
        "itinerary": "travel_itinerary",
        "flight": "travel_itinerary",
        "accommodation": "accommodation_proof",
        "invitation": "sponsor_invitation_letter",
        "sponsor_status": "sponsor_status_proof",
        "brp": "sponsor_status_proof",
        "business": "self_employment_evidence",
        "home_ties": "home_ties_evidence",
        "bank": "bank_statements",
        "statements": "bank_statements",
    }
    for prefix, evidence_id in aliases.items():
        if stem == prefix or stem.startswith(prefix + "_"):
            return evidence_id
    return None


def _message_ids(header):
    return re.findall(r"<[^>]+>", header or "")


def _first_message_id(header):
    ids = _message_ids(header)
    return ids[0] if ids else None


def _fallback_id(subject, body):
    return "fallback:%s:%s" % (subject or "", body[:80])


class EmailPoller(object):
    def __init__(self, engine, store, default_case_id=None, message_to_case=None,
                 attachment_dir=None):
        self.engine = engine
        self.store = store
        self.default_case_id = default_case_id
        self.message_to_case = message_to_case if message_to_case is not None else {}
        self.attachment_dir = attachment_dir

    def poll_raw(self, raw_messages):
        results = []
        for raw in raw_messages:
            results.extend(self.apply_email(parse_email(raw)))
        return results

    def apply_email(self, parsed):
        case_id = self.resolve_case(parsed)
        if case_id is None:
            return []

        self._set_thread_context(case_id, parsed)
        refs = list(parsed.references)
        if parsed.message_id not in refs:
            refs.append(parsed.message_id)
        results = []
        mapped_attachments = [a for a in parsed.attachments if a.evidence_id is not None]
        if parsed.body and not mapped_attachments:
            result = self.engine.handle_reply(
                case_id, parsed.body, parsed.message_id, channel="email")
            if result:
                results.append(result)

        applied_attachment = False
        for index, attachment in enumerate(parsed.attachments):
            if attachment.evidence_id is None:
                self.store.log(case_id, "email_attachment_unmapped", {
                    "message_id": parsed.message_id,
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "size": len(attachment.content),
                })
                continue
            document_ref = self._document_ref(case_id, parsed, attachment, index)
            self._set_thread_context(case_id, parsed)
            applied = self.engine.apply_document(
                case_id, attachment.evidence_id, document_ref,
                "%s:%s:%d" % (parsed.message_id, attachment.filename, index),
                channel="email")
            if applied is not None:
                applied_attachment = True

        if mapped_attachments and applied_attachment:
            self._set_thread_context(case_id, parsed)
            case = self.store.get_case(case_id)
            results.append(self.engine._respond(
                case, state.next_action(case, self.engine.checklist)))

        if results:
            sent = results[-1].get("sent") or {}
            if sent.get("message_id"):
                self.message_to_case[sent["message_id"]] = case_id
                if hasattr(self.store, "remember_email_message"):
                    self.store.remember_email_message(sent["message_id"], case_id)
        return results

    def resolve_case(self, parsed):
        if parsed.case_id:
            return parsed.case_id
        for message_id in [parsed.in_reply_to] + list(parsed.references):
            if message_id and message_id in self.message_to_case:
                return self.message_to_case[message_id]
            if hasattr(self.store, "case_for_email_message"):
                case_id = self.store.case_for_email_message(message_id)
                if case_id:
                    return case_id
        return self.default_case_id

    def _set_thread_context(self, case_id, parsed):
        channel = getattr(getattr(self.engine, "router", None), "email", None)
        if hasattr(channel, "set_thread_context"):
            references = list(parsed.references)
            if parsed.message_id not in references:
                references.append(parsed.message_id)
            channel.set_thread_context(
                case_id, in_reply_to=parsed.message_id, references=references,
                to_addr=parsed.from_addr or None)

    def _document_ref(self, case_id, parsed, attachment, index):
        if not self.attachment_dir:
            return attachment.filename
        safe_case = re.sub(r"[^A-Za-z0-9_.-]+", "_", case_id)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", attachment.filename)
        directory = os.path.join(self.attachment_dir, safe_case)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "%s-%s" % (index, safe_name))
        with open(path, "wb") as fh:
            fh.write(attachment.content)
        return path
