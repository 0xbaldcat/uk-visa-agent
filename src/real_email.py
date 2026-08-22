"""Real SMTP/IMAP adapter for the PoC.

The normal demo uses an in-memory EmailChannel so tests stay hermetic. This module
is the opt-in bridge for a live mailbox during the final walkthrough. Credentials
come only from environment variables; nothing is stored in code or config files.
"""
import imaplib
import json
import os
import smtplib
from dataclasses import dataclass
from email import policy
from email.utils import make_msgid
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Dict, Any, List, Optional


@dataclass
class EmailSettings:
    smtp_host: str
    smtp_port: int
    imap_host: str
    imap_port: int
    username: str
    password: str
    from_addr: str
    to_addr: str
    use_tls: bool = True

    @classmethod
    def from_env(cls):
        required = [
            "VISA_AGENT_SMTP_HOST", "VISA_AGENT_SMTP_PORT",
            "VISA_AGENT_IMAP_HOST", "VISA_AGENT_IMAP_PORT",
            "VISA_AGENT_EMAIL_USER", "VISA_AGENT_EMAIL_PASSWORD",
            "VISA_AGENT_FROM_EMAIL", "VISA_AGENT_TO_EMAIL",
        ]
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError(
                "missing email environment variables: %s" % ", ".join(missing))
        return cls(
            smtp_host=os.environ["VISA_AGENT_SMTP_HOST"],
            smtp_port=int(os.environ["VISA_AGENT_SMTP_PORT"]),
            imap_host=os.environ["VISA_AGENT_IMAP_HOST"],
            imap_port=int(os.environ["VISA_AGENT_IMAP_PORT"]),
            username=os.environ["VISA_AGENT_EMAIL_USER"],
            password=os.environ["VISA_AGENT_EMAIL_PASSWORD"],
            from_addr=os.environ["VISA_AGENT_FROM_EMAIL"],
            to_addr=os.environ["VISA_AGENT_TO_EMAIL"],
            use_tls=os.environ.get("VISA_AGENT_EMAIL_TLS", "1") != "0",
        )


def build_message(settings, subject, body, attachments=None, reply_to=None,
                  message_id=None, in_reply_to=None, references=None):
    msg = EmailMessage()
    msg["From"] = settings.from_addr
    msg["To"] = settings.to_addr
    msg["Subject"] = subject
    msg["Message-ID"] = message_id or make_msgid(
        idstring="visa-agent", domain="visa-agent.local")
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = " ".join(references)
    msg.set_content(body)

    for attachment in attachments or []:
        content = attachment.get("content")
        if not isinstance(content, (str, bytes)):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        if isinstance(content, str):
            content = content.encode("utf-8")
        content_type = attachment.get("content_type", "application/octet-stream")
        maintype, subtype = content_type.split("/", 1)
        msg.add_attachment(
            content,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.get("filename", "attachment.bin"))
    return msg


class RealEmailChannel(object):
    name = "email"
    supports_attachments = True

    def __init__(self, settings):
        self.settings = settings
        self._thread_context = {}

    def set_thread_context(self, case_id, in_reply_to=None, references=None,
                           to_addr=None, subject=None):
        self._thread_context[case_id] = {
            "in_reply_to": in_reply_to,
            "references": list(references or []),
            "to_addr": to_addr,
            "subject": subject,
        }

    def send(self, case_id, body, kind="session", attachments=None):
        context = self._thread_context.pop(case_id, {})
        references = list(context.get("references") or [])
        if context.get("in_reply_to") and context["in_reply_to"] not in references:
            references.append(context["in_reply_to"])
        subject = _thread_subject(context, case_id, kind, attachments)
        settings = self.settings
        if context.get("to_addr"):
            settings = EmailSettings(
                smtp_host=self.settings.smtp_host,
                smtp_port=self.settings.smtp_port,
                imap_host=self.settings.imap_host,
                imap_port=self.settings.imap_port,
                username=self.settings.username,
                password=self.settings.password,
                from_addr=self.settings.from_addr,
                to_addr=context["to_addr"],
                use_tls=self.settings.use_tls,
            )
        msg = build_message(
            settings, subject, body, attachments=attachments,
            in_reply_to=context.get("in_reply_to"), references=references)
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30) as smtp:
            if self.settings.use_tls:
                smtp.starttls()
            smtp.login(self.settings.username, self.settings.password)
            smtp.send_message(msg)
        return {
            "channel": "email",
            "case_id": case_id,
            "body": body,
            "attachments": list(attachments or []),
            "message_id": msg.get("Message-ID"),
            "in_reply_to": msg.get("In-Reply-To"),
            "references": references,
            "subject": subject,
        }


def _subject_for(kind, attachments):
    if attachments:
        return "Document pack ready"
    if kind == "deliver_pack":
        return "Ready for adviser review"
    if kind == "request_resupply":
        return "Please replace one document"
    if kind == "request_evidence":
        return "Next document needed"
    return "Visa preparation update"


def _thread_subject(context, case_id, kind, attachments):
    if context.get("in_reply_to") and context.get("subject"):
        subject = context["subject"].strip()
        if subject.lower().startswith("re:"):
            return subject
        return "Re: %s" % subject
    return "[visa-agent:%s] %s" % (case_id, _subject_for(kind, attachments))


def fetch_unseen_raw(settings, mailbox="INBOX", from_addr=None, limit=10):
    """Fetch unseen messages for operator inspection.

    This is deliberately small: the PoC can show real inbound email, but document
    parsing still runs through the existing model seam after the operator maps an
    attachment to the expected checklist item.
    """
    messages = []
    with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:
        imap.login(settings.username, settings.password)
        imap.select(mailbox)
        criteria = ['UNSEEN']
        if from_addr:
            criteria.extend(['FROM', '"%s"' % from_addr])
        status, data = imap.search(None, *criteria)
        if status != "OK":
            raise RuntimeError("IMAP search failed: %s" % status)
        ids = data[0].split()[-limit:]
        for msg_id in ids:
            status, msg_data = imap.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK":
                continue
            raw = msg_data[0][1]
            messages.append({"imap_id": msg_id.decode("ascii"), "raw": raw})
    return messages


def mark_seen(settings, imap_id, mailbox="INBOX"):
    with imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port) as imap:
        imap.login(settings.username, settings.password)
        imap.select(mailbox)
        status, _ = imap.store(str(imap_id), "+FLAGS", "\\Seen")
        if status != "OK":
            raise RuntimeError("IMAP mark seen failed: %s" % status)


def fetch_unseen(settings, mailbox="INBOX", from_addr=None, limit=10):
    """Fetch unseen message summaries for operator inspection."""
    messages = []
    for row in fetch_unseen_raw(settings, mailbox=mailbox, from_addr=from_addr, limit=limit):
        parsed = BytesParser(policy=policy.default).parsebytes(row["raw"])
        messages.append(_summarise_message(row["imap_id"], parsed))
    return messages


def _summarise_message(msg_id, msg):
    attachments = []
    body = ""
    for part in msg.walk():
        disposition = part.get_content_disposition()
        if disposition == "attachment":
            attachments.append({
                "filename": part.get_filename(),
                "content_type": part.get_content_type(),
                "size": len(part.get_payload(decode=True) or b""),
            })
        elif part.get_content_type() == "text/plain" and not body:
            body = part.get_content()
    return {
        "id": msg_id,
        "message_id": msg.get("Message-ID"),
        "in_reply_to": msg.get("In-Reply-To"),
        "references": msg.get("References"),
        "from": msg.get("From"),
        "subject": msg.get("Subject"),
        "body": body.strip(),
        "attachments": attachments,
    }
