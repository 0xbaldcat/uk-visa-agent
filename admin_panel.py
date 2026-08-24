#!/usr/bin/env python3
"""Tiny local admin panel for the visa PoC.

This is intentionally not a production admin app. It is a demo surface for the
human-review gate: show cases waiting on an adviser, show the internal review
pack, and persist the adviser's decision.
"""
import argparse
import html
import mimetypes
import os
import shlex
import smtplib
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

import checklist  # noqa: E402
import deliver  # noqa: E402
import real_email  # noqa: E402
import store as store_mod  # noqa: E402


CSS = """
:root {
  --bg: #f7f7f4;
  --panel: #ffffff;
  --ink: #1f2523;
  --muted: #65706b;
  --line: #d8ded8;
  --accent: #235d4d;
  --warn: #8a5a00;
  --bad: #9d2f2f;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: var(--accent); text-decoration: none; }
header {
  border-bottom: 1px solid var(--line);
  background: var(--panel);
  padding: 14px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
header h1 { margin: 0; font-size: 18px; font-weight: 650; }
main { padding: 22px 24px 40px; max-width: 1280px; margin: 0 auto; }
.layout { display: grid; grid-template-columns: 330px 1fr; gap: 18px; }
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.list a {
  display: block;
  padding: 13px 14px;
  border-bottom: 1px solid var(--line);
}
.list a:last-child { border-bottom: 0; }
.list a.active { background: #eaf1ed; }
.list-section {
  border-bottom: 1px solid var(--line);
}
.list-section:last-child { border-bottom: 0; }
.list-section-title {
  padding: 11px 14px 8px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: .03em;
  text-transform: uppercase;
  background: #fbfbf9;
  border-bottom: 1px solid var(--line);
}
.list-section-empty {
  padding: 13px 14px;
  color: var(--muted);
  font-size: 12px;
}
.case-id { font-weight: 650; overflow-wrap: anywhere; }
.meta { color: var(--muted); font-size: 12px; margin-top: 4px; }
.badge-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 12px;
  font-weight: 700;
  background: #eef0ec;
  color: #4e5853;
}
.badge.todo { background: #fff2cf; color: #6d4900; }
.badge.good { background: #dcefe6; color: #164938; }
.badge.follow { background: #fde3df; color: #8f2d22; }
.badge.neutral { background: #e8ebee; color: #4c5661; }
.content { padding: 18px; }
.grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.metric { padding: 12px; border: 1px solid var(--line); border-radius: 8px; }
.metric b { display: block; font-size: 20px; margin-top: 2px; }
h2 { font-size: 16px; margin: 22px 0 10px; }
h3 { font-size: 14px; margin: 16px 0 8px; }
table { width: 100%; border-collapse: collapse; }
td, th { border-top: 1px solid var(--line); padding: 8px 6px; text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
.status { font-weight: 650; color: var(--accent); }
.status.bad { color: var(--bad); }
.status.warn { color: var(--warn); }
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: #fbfbf9;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  max-height: 680px;
  overflow: auto;
}
textarea, input[type=text] {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 9px;
  font: inherit;
}
.material-option {
  display: grid;
  grid-template-columns: 24px 1fr;
  gap: 8px;
  align-items: start;
  border-top: 1px solid var(--line);
  padding: 9px 0;
}
.material-option:first-child { border-top: 0; }
.material-option input { margin-top: 3px; }
.option-title { font-weight: 650; }
.timeline-item {
  border-top: 1px solid var(--line);
  padding: 10px 0;
}
.timeline-item:first-child { border-top: 0; }
.analysis-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 10px;
  background: #fbfbf9;
}
.analysis-card h3 { margin-top: 0; }
.analysis-card ul { margin: 8px 0 0; padding-left: 18px; }
.analysis-card li { margin: 4px 0; }
.actions { display: flex; gap: 10px; align-items: center; margin-top: 10px; }
.notice {
  border: 1px solid #bdd6ca;
  background: #e8f3ed;
  color: #164938;
  border-radius: 8px;
  padding: 10px 12px;
  margin: 14px 0;
  font-weight: 650;
}
button {
  border: 0;
  border-radius: 6px;
  background: var(--accent);
  color: white;
  padding: 9px 12px;
  font-weight: 650;
  cursor: pointer;
}
button.secondary { background: #5b6661; }
.empty { color: var(--muted); padding: 20px; }
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  .grid { grid-template-columns: 1fr; }
}
"""


def default_db_path():
    return os.environ.get("VISA_AGENT_DB_PATH") or (
        "live-panwei.sqlite3" if os.path.exists(os.path.join(ROOT, "live-panwei.sqlite3"))
        else "visa-agent.sqlite3")


def load_store(db_path):
    path = db_path if os.path.isabs(db_path) else os.path.join(ROOT, db_path)
    return store_mod.Store(path)


def load_checklist():
    return checklist.load_route("visitor_family_visit", config_dir=os.path.join(ROOT, "config"))


def case_rows(st):
    return st.conn.execute(
        "SELECT c.id, c.stage, c.slots, c.evidence, c.updated_at, "
        "ar.decision AS review_decision, ar.created_at AS review_created_at, "
        "ar.note AS review_note "
        "FROM cases c "
        "LEFT JOIN adviser_reviews ar ON ar.id = ("
        "  SELECT max(id) FROM adviser_reviews WHERE case_id = c.id"
        ") "
        "ORDER BY "
        "CASE "
        "  WHEN c.stage = 'human_review' AND ar.decision IS NULL THEN 0 "
        "  WHEN ar.decision = 'needs_client_follow_up' THEN 1 "
        "  WHEN ar.decision = 'approved_for_final_report' THEN 2 "
        "  WHEN c.stage = 'human_review' THEN 3 "
        "  ELSE 4 "
        "END, c.updated_at DESC"
    ).fetchall()


def selected_case_id(st, query):
    requested = query.get("case", [None])[0]
    if requested and st.get_case(requested):
        return requested
    row = st.conn.execute(
        "SELECT id FROM cases WHERE stage = 'human_review' ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    if row:
        return row["id"]
    row = st.conn.execute("SELECT id FROM cases ORDER BY updated_at DESC LIMIT 1").fetchone()
    return row["id"] if row else None


def review_pack(cl, case):
    pack = deliver.build_pack(cl, case)
    return deliver.render_pack_attachments(pack)[0]["content"]


def review_pack_data(cl, case):
    return deliver.build_pack(cl, case)


def client_email_for_case(st, case_id):
    row = st.conn.execute(
        "SELECT sender FROM email_sender_cases WHERE case_id = ? "
        "ORDER BY last_seen_at DESC, created_at DESC LIMIT 1",
        (case_id,)).fetchone()
    return row["sender"] if row else None


def load_email_settings(to_addr):
    _load_local_env_file(os.path.join(ROOT, ".local-live.env"))
    defaults = {
        "VISA_AGENT_SMTP_HOST": "smtp.gmail.com",
        "VISA_AGENT_SMTP_PORT": "587",
        "VISA_AGENT_IMAP_HOST": "imap.gmail.com",
        "VISA_AGENT_IMAP_PORT": "993",
        "VISA_AGENT_EMAIL_USER": "visa.agent.demo@gmail.com",
        "VISA_AGENT_FROM_EMAIL": "visa.agent.demo@gmail.com",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    os.environ["VISA_AGENT_TO_EMAIL"] = to_addr or os.environ.get(
        "VISA_AGENT_TO_EMAIL", "")
    return real_email.EmailSettings.from_env()


def _load_local_env_file(path):
    if not os.path.exists(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key and key not in os.environ:
                parts = shlex.split(value)
                os.environ[key] = " ".join(parts) if parts else value.strip()


def notify_client(st, cl, case_id, review_id, decision, note, selected_tokens=None):
    case = st.get_case(case_id)
    to_addr = client_email_for_case(st, case_id)
    subject, body, attachments = customer_notification(
        cl, case, decision, note, selected_tokens=selected_tokens, st=st)
    if not to_addr:
        st.record_adviser_notification(
            case_id, decision, "skipped", review_id=review_id,
            subject=subject, body=body, error="no client email found for case")
        return "skipped"
    try:
        settings = load_email_settings(to_addr)
        prior_message_id = st.latest_email_message_for_case(case_id)
        msg = real_email.build_message(
            settings, subject, body, attachments=attachments,
            in_reply_to=prior_message_id,
            references=([prior_message_id] if prior_message_id else None))
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.use_tls:
                smtp.starttls()
            smtp.login(settings.username, settings.password)
            smtp.send_message(msg)
        st.remember_email_message(msg.get("Message-ID"), case_id)
        st.record_adviser_notification(
            case_id, decision, "sent", review_id=review_id, to_addr=to_addr,
            subject=subject, body=body, message_id=msg.get("Message-ID"))
        return "sent"
    except Exception as exc:
        st.record_adviser_notification(
            case_id, decision, "failed", review_id=review_id, to_addr=to_addr,
            subject=subject, body=body, error=str(exc))
        return "failed"


def customer_notification(cl, case, decision, note, selected_tokens=None, st=None):
    if decision == "approved_for_final_report":
        subject = "[visa-agent:%s] Adviser review complete" % case.id
        body = (
            "Your materials have been reviewed by an adviser.\n\n"
            "Case ID: %s\n\n"
            "I've attached the reviewed materials package for this stage. Please "
            "read the report and tell us if any factual detail or filename is "
            "wrong before you use it.\n\n"
            "This service does not submit the visa application for you."
        ) % case.id
        selected_rows = selected_material_rows(cl, case, selected_tokens, st=st)
        html_report = render_client_final_report_html(
            cl, case, note, selected_rows=selected_rows)
        attachments = [{
            "filename": "visa-final-review-report.pdf",
            "content_type": "application/pdf",
            "content": render_client_final_report_pdf(
                cl, case, note, selected_rows=selected_rows),
        }, {
            "filename": "visa-final-review-report.html",
            "content_type": "text/html",
            "content": html_report,
        }]
        attachments.extend(material_attachments(selected_rows))
        return subject, body, attachments
    subject = "[visa-agent:%s] Adviser follow-up needed" % case.id
    follow_up = note or "The adviser needs a little more information before final review."
    body = (
        "Your materials have been reviewed by an adviser, and we need one more "
        "follow-up before we can finish the report.\n\n"
        "Case ID: %s\n\n"
        "%s\n\n"
        "Please reply to this email with the requested information or documents."
        % (case.id, follow_up))
    return subject, body, []


def render_client_final_report(cl, case, adviser_note=None, selected_rows=None):
    accepted = selected_rows if selected_rows is not None else selected_material_rows(cl, case)
    lines = [
        "# Reviewed Visa Materials Package",
        "",
        "Applicant: %s" % (case.slots.get("applicant_name") or "unknown"),
        "",
        ("This report lists the documents an adviser reviewed and the files "
         "included in this package. Check the filenames and facts before using "
         "the materials for your application."),
        "",
    ]
    if adviser_note:
        lines.extend(["## Adviser Note", adviser_note, ""])
    lines.append("## Included Accepted Materials")
    if accepted:
        for item in accepted:
            line = "- %s: %s" % (item["label"], item["filename"])
            if item.get("attached"):
                line += " (attached)"
            else:
                line += " (accepted, but file was not found for email attachment)"
            lines.append(line)
            if item.get("advisories"):
                for advisory in item["advisories"]:
                    lines.append("  - Note: %s" % advisory)
    else:
        lines.append("- No accepted material files are ready to include yet.")
    lines.extend([
        "",
        "## Limits",
        "- This report confirms the listed materials were reviewed for completeness and consistency.",
        "- It is not a visa outcome prediction.",
        "- This service does not submit the application.",
    ])
    return "\n".join(lines).rstrip() + "\n"


def render_client_final_report_html(cl, case, adviser_note=None, selected_rows=None):
    accepted = selected_rows if selected_rows is not None else selected_material_rows(cl, case)
    rows = []
    for item in accepted:
        notes = list(item.get("advisories") or [])
        if not item.get("attached"):
            notes.append("Accepted, but the source file was not found for email attachment.")
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>Included</td><td>%s</td></tr>" % (
                esc(item["label"]), esc(item["filename"]),
                esc("; ".join(notes) if notes else "Reviewed and accepted")))
    if not rows:
        rows.append("<tr><td colspan=\"4\">No accepted material files are ready to include yet.</td></tr>")
    adviser = ("<section><h2>Adviser Note</h2><p>%s</p></section>" %
               esc(adviser_note)) if adviser_note else ""
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Reviewed Visa Materials Package</title>
  <style>
    body { font-family: Arial, sans-serif; color: #1f2523; line-height: 1.45; margin: 32px; }
    h1 { font-size: 24px; margin-bottom: 8px; }
    h2 { font-size: 18px; margin-top: 28px; }
    table { border-collapse: collapse; width: 100%%; margin-top: 12px; }
    th, td { border: 1px solid #d8ded8; padding: 8px; text-align: left; vertical-align: top; }
    th { background: #eef3ef; }
    .limits { color: #515c57; }
  </style>
</head>
<body>
  <h1>Reviewed Visa Materials Package</h1>
  <p><strong>Applicant:</strong> %s</p>
  <p>This report lists the documents an adviser reviewed and the files included in this package. Check the filenames and facts before using the materials for your application.</p>
  %s
  <h2>Included Accepted Materials</h2>
  <table>
    <thead><tr><th>Material</th><th>File</th><th>Package status</th><th>Review note</th></tr></thead>
    <tbody>%s</tbody>
  </table>
  <h2>Limits</h2>
  <ul class="limits">
    <li>This report confirms the listed materials were reviewed for completeness and consistency.</li>
    <li>It is not a visa outcome prediction.</li>
    <li>This service does not submit the application.</li>
  </ul>
</body>
</html>
""" % (
        esc(case.slots.get("applicant_name") or "unknown"),
        adviser,
        "".join(rows))


def render_client_final_report_pdf(cl, case, adviser_note=None, selected_rows=None):
    markdown = render_client_final_report(
        cl, case, adviser_note=adviser_note, selected_rows=selected_rows)
    lines = []
    for line in markdown.splitlines():
        clean = line.replace("#", "").replace("*", "").strip()
        if not clean:
            lines.append("")
            continue
        while len(clean) > 88:
            cut = clean.rfind(" ", 0, 88)
            if cut <= 0:
                cut = 88
            lines.append(clean[:cut])
            clean = clean[cut:].strip()
        lines.append(clean)
    return simple_text_pdf(lines[:52])


def simple_text_pdf(lines):
    """Small dependency-free PDF for the PoC final report."""
    def pdf_escape(value):
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    text_ops = ["BT", "/F1 10 Tf", "50 760 Td", "14 TL"]
    for index, line in enumerate(lines):
        if index:
            text_ops.append("T*")
        text_ops.append("(%s) Tj" % pdf_escape(line))
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("utf-8")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(("%d 0 obj\n" % idx).encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(("xref\n0 %d\n" % (len(objects) + 1)).encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(("%010d 00000 n \n" % offset).encode("ascii"))
    pdf.extend(
        ("trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" %
         (len(objects) + 1, xref)).encode("ascii"))
    return bytes(pdf)


def accepted_material_rows(cl, case):
    satisfied, _, _ = case.outstanding(cl)
    rows = []
    for ev_id in satisfied:
        ev = cl.evidence(ev_id) or {"label": ev_id}
        rec = case.evidence.get(ev_id) or {}
        document_ref = rec.get("document_ref") or ""
        advisories = [
            f.get("message") for f in (rec.get("failures") or [])
            if f.get("advisory") and f.get("message")
        ]
        rows.append({
            "evidence_id": ev_id,
            "token": "accepted:%s" % ev_id,
            "source": "validated_checklist",
            "label": ev["label"],
            "document_ref": document_ref,
            "filename": os.path.basename(document_ref) if document_ref else "[no file recorded]",
            "attached": bool(document_ref and os.path.exists(document_ref)),
            "advisories": advisories,
        })
    return rows


def human_review_file_rows(st, case):
    if st is None or not hasattr(st, "human_review_files"):
        return []
    rows = []
    for row in st.human_review_files(case.id):
        rows.append({
            "token": "review_file:%s" % row["id"],
            "source": "human_review_upload",
            "label": row["evidence_id"] or "Human-review file",
            "document_ref": row["document_ref"],
            "filename": row["filename"],
            "attached": bool(row["document_ref"] and os.path.exists(row["document_ref"])),
            "advisories": [],
            "created_at": row["created_at"],
            "from_addr": row["from_addr"],
            "selected_by_default": bool(row["selected_by_default"]),
        })
    return rows


def material_options(cl, case, st=None):
    options = []
    for row in accepted_material_rows(cl, case):
        row = dict(row)
        row["selected_by_default"] = True
        options.append(row)
    options.extend(human_review_file_rows(st, case))
    return options


def selected_material_rows(cl, case, selected_tokens=None, st=None):
    options = material_options(cl, case, st=st)
    if selected_tokens is None:
        selected_tokens = [
            item["token"] for item in options if item.get("selected_by_default")]
    selected = set(selected_tokens)
    return [item for item in options if item["token"] in selected]


def material_attachments(rows):
    attachments = []
    for item in rows:
        if not item["attached"]:
            continue
        content_type = mimetypes.guess_type(item["document_ref"])[0] or "application/octet-stream"
        with open(item["document_ref"], "rb") as fh:
            content = fh.read()
        attachments.append({
            "filename": item["filename"],
            "content_type": content_type,
            "content": content,
        })
    return attachments


def accepted_material_attachments(cl, case):
    return material_attachments(selected_material_rows(cl, case))


def status_class(value):
    if value in ("needs_replacement", "failed", "escalated"):
        return "bad"
    if value in ("accepted_with_note", "collecting", "remediation"):
        return "warn"
    return ""


def review_badge(decision, stage):
    if decision == "approved_for_final_report":
        return "Approved", "good"
    if decision == "needs_client_follow_up":
        return "Needs follow-up", "follow"
    if stage == "human_review":
        return "Needs review", "todo"
    return "In progress", "neutral"


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def page(title, body):
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>{css}</style>
</head>
<body>
  <header>
    <h1>Visa Adviser Review</h1>
    <div class="meta">Local PoC admin panel</div>
  </header>
  <main>{body}</main>
</body>
</html>""".format(title=esc(title), css=CSS, body=body)


def render_list(rows, active_id):
    if not rows:
        return '<div class="panel empty">No cases yet.</div>'
    grouped = [
        ("Needs Review", [row for row in rows if list_group(row) == "needs_review"]),
        ("Reviewed", [row for row in rows if list_group(row) == "reviewed"]),
        ("In Progress", [row for row in rows if list_group(row) == "in_progress"]),
    ]
    sections = []
    for title, group_rows in grouped:
        items = []
        for row in group_rows:
            items.append(render_list_row(row, active_id))
        if not items:
            items.append('<div class="list-section-empty">No cases in this group.</div>')
        sections.append(
            '<section class="list-section"><div class="list-section-title">{title}</div>'
            '{items}</section>'.format(title=esc(title), items="".join(items)))
    return '<div class="panel list">%s</div>' % "".join(sections)


def list_group(row):
    if row["review_decision"]:
        return "reviewed"
    if row["stage"] == "human_review":
        return "needs_review"
    return "in_progress"


def render_list_row(row, active_id):
    case = store_mod.Case(row["id"], "visitor_family_visit", row["stage"],
                          {}, {}, None)
    active = " active" if row["id"] == active_id else ""
    label, cls = review_badge(row["review_decision"], case.stage.value)
    review_meta = row["review_created_at"] or "not reviewed"
    return (
        '<a class="{active}" href="/?case={id}">'
        '<div class="case-id">{id}</div>'
        '<div class="meta">stage: {stage} · updated {updated}</div>'
        '<div class="badge-row"><span class="badge {cls}">{label}</span>'
        '<span class="badge neutral">{review_meta}</span></div>'
        '</a>'.format(
            active=active,
            id=esc(row["id"]),
            stage=esc(case.stage.value),
            updated=esc(row["updated_at"]),
            cls=esc(cls),
            label=esc(label),
            review_meta=esc(review_meta)))


def render_case(st, cl, case_id, query=None):
    if not case_id:
        return '<div class="panel empty">No case selected.</div>'
    query = query or {}
    case = st.get_case(case_id)
    satisfied, missing, failing = case.outstanding(cl)
    review = st.latest_adviser_review(case_id)
    notification = st.latest_adviser_notification(case_id)
    review_messages = st.human_review_messages(case_id) if hasattr(st, "human_review_messages") else []
    review_files = st.human_review_files(case_id) if hasattr(st, "human_review_files") else []
    pack_data = review_pack_data(cl, case)
    pack = deliver.render_pack_attachments(pack_data)[0]["content"]
    whole_case_html = render_whole_case_analysis_section(
        pack_data.get("whole_case_analysis") or {})
    rows = []
    for ev in cl.required_evidence(case.slots):
        rec = case.evidence.get(ev["id"])
        if rec is None:
            status = "missing"
        elif [f for f in rec.get("failures", []) if not f.get("advisory")]:
            status = "needs_replacement"
        elif [f for f in rec.get("failures", []) if f.get("advisory")]:
            status = "accepted_with_note"
        else:
            status = "accepted"
        rows.append(
            "<tr><td>{label}</td><td><span class=\"status {cls}\">{status}</span></td>"
            "<td>{ref}</td></tr>".format(
                label=esc(ev["label"]),
                cls=status_class(status),
                status=esc(status.replace("_", " ")),
                ref=esc((rec or {}).get("document_ref", ""))))
    slot_rows = "".join(
        "<tr><th>{}</th><td>{}</td></tr>".format(esc(k), esc(v))
        for k, v in sorted(case.slots.items()))
    review_html = (
        '<div class="metric"><span>Latest adviser decision</span><b>%s</b>'
        '<div class="meta">%s</div><div class="meta">%s</div></div>'
        % (esc(review["decision"] if review else "none"),
           esc(review["created_at"] if review else "No review recorded yet."),
           esc(review["note"] if review and review["note"] else "")))
    notification_html = (
        '<div class="metric"><span>Customer notification</span><b>%s</b>'
        '<div class="meta">%s</div><div class="meta">%s</div></div>'
        % (esc(notification["status"] if notification else "none"),
           esc(notification["created_at"] if notification else "No customer email yet."),
           esc(notification["error"] if notification and notification["error"] else
               (notification["subject"] if notification else ""))))
    saved = (query.get("saved") or [""])[0]
    notified = (query.get("notified") or [""])[0]
    notice = ""
    if saved:
        text = "Review decision recorded: %s" % saved.replace("_", " ")
        if notified:
            text += " · customer notification: %s" % notified
        notice = '<div class="notice">%s</div>' % esc(text)
    history = st.conn.execute(
        "SELECT decision, note, reviewer, package_selection, created_at FROM adviser_reviews "
        "WHERE case_id = ? ORDER BY id DESC LIMIT 8", (case_id,)).fetchall()
    history_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            esc(row["created_at"]), esc(row["decision"]), esc(row["reviewer"]),
            esc(row["note"]))
        for row in history)
    if not history_rows:
        history_rows = '<tr><td colspan="4" class="meta">No review decisions yet.</td></tr>'
    review_message_rows = "".join(
        '<div class="timeline-item"><div class="meta">{time} · {sender}</div>'
        '<div>{body}</div></div>'.format(
            time=esc(row["created_at"]), sender=esc(row["from_addr"]),
            body=esc(row["body"]))
        for row in review_messages)
    if not review_message_rows:
        review_message_rows = '<div class="meta">No human-review client replies yet.</div>'
    review_file_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            esc(row["created_at"]), esc(row["filename"]), esc(row["evidence_id"] or ""),
            esc(row["document_ref"]))
        for row in review_files)
    if not review_file_rows:
        review_file_rows = '<tr><td colspan="4" class="meta">No human-review files yet.</td></tr>'
    material_html = render_material_selector(cl, case, st)
    return """
<div class="panel content">
  <div class="case-id">{case_id}</div>
  <div class="meta">Stage: <span class="status {stage_cls}">{stage}</span></div>
  {notice}
  <div class="grid" style="margin-top:14px">
    <div class="metric"><span>Accepted</span><b>{accepted}</b></div>
    <div class="metric"><span>Missing</span><b>{missing}</b></div>
    <div class="metric"><span>Needs replacement</span><b>{failing}</b></div>
    {review_html}
    {notification_html}
  </div>

  <h2>Adviser Decision</h2>
  <form method="post" action="/review">
    <input type="hidden" name="case_id" value="{case_id}">
    <input type="hidden" name="materials_present" value="1">
    <label>Message / adviser note</label>
    <textarea name="note" rows="5" placeholder="For follow-up, write the email to the client. For approval, record the adviser note for the final package."></textarea>
    <h3>Final Package Selection</h3>
    <div class="meta">Default checked items are machine-validated checklist files. Human-review files are opt-in.</div>
    {material_html}
    <div class="actions">
      <button name="decision" value="approved_for_final_report">Approve for final report with selected files</button>
      <button class="secondary" name="decision" value="needs_client_follow_up">Needs client follow-up / send message</button>
    </div>
  </form>

  <h2>Review History</h2>
  <table><thead><tr><th>Time</th><th>Decision</th><th>Reviewer</th><th>Note</th></tr></thead><tbody>{history_rows}</tbody></table>

  <h2>Whole-Case Analysis</h2>
  {whole_case_html}

  <h2>Intake Facts</h2>
  <table>{slot_rows}</table>

  <h2>Materials</h2>
  <table><thead><tr><th>Material</th><th>Status</th><th>File</th></tr></thead><tbody>{rows}</tbody></table>

  <h2>Human Review Client Replies</h2>
  {review_message_rows}

  <h2>Human Review Files</h2>
  <table><thead><tr><th>Time</th><th>File</th><th>Mapped checklist item</th><th>Saved path</th></tr></thead><tbody>{review_file_rows}</tbody></table>

  <h2>Internal Review Pack</h2>
  <pre>{pack}</pre>
</div>
""".format(
        case_id=esc(case.id),
        stage=esc(case.stage.value),
        stage_cls=status_class(case.stage.value),
        notice=notice,
        accepted=len(satisfied),
        missing=len(missing),
        failing=len(failing),
        review_html=review_html,
        notification_html=notification_html,
        history_rows=history_rows,
        whole_case_html=whole_case_html,
        material_html=material_html,
        slot_rows=slot_rows,
        rows="".join(rows),
        review_message_rows=review_message_rows,
        review_file_rows=review_file_rows,
        pack=esc(pack))


def render_whole_case_analysis_section(doc):
    observations = doc.get("observations") or []
    follow_ups = doc.get("follow_up_questions") or []
    meta = (
        '<div class="meta">Evidence-backed adviser notes only. '
        'Not an outcome prediction or sufficiency decision. '
        'Observations: {obs} · Follow-up questions: {questions} · Source: {source}</div>'
        .format(
            obs=len(observations),
            questions=len(follow_ups),
            source=esc(doc.get("candidate_source") or "unknown")))
    if not observations:
        return meta + '<div class="analysis-card">No whole-case observations generated.</div>'
    cards = []
    for item in observations:
        refs = "".join(
            "<li>{source} = {value}</li>".format(
                source=esc(ref.get("source", "")),
                value=esc(ref.get("value", "")))
            for ref in item.get("evidence_refs") or [])
        if not refs:
            refs = '<li class="meta">No evidence refs recorded.</li>'
        question = item.get("question") or "none"
        missing = item.get("missing_context") or "none"
        cards.append(
            '<div class="analysis-card">'
            '<h3>{limb}</h3>'
            '<div><b>Observation:</b> {observation}</div>'
            '<div><b>Missing context:</b> {missing}</div>'
            '<div><b>Suggested question:</b> {question}</div>'
            '<div class="meta">Dimension: {dimension} · Type: {kind}</div>'
            '<ul>{refs}</ul>'
            '</div>'.format(
                limb=esc((item.get("limb") or "").replace("_", " ").title()),
                observation=esc(item.get("observation", "")),
                missing=esc(missing),
                question=esc(question),
                dimension=esc(item.get("dimension_id", "")),
                kind=esc(item.get("observation_type", "")),
                refs=refs))
    return meta + "".join(cards)


def render_material_selector(cl, case, st):
    options = material_options(cl, case, st=st)
    if not options:
        return '<div class="meta">No files available for final package selection.</div>'
    items = []
    for item in options:
        checked = " checked" if item.get("selected_by_default") else ""
        source = "Checklist-validated" if item["source"] == "validated_checklist" else "Human-review upload"
        availability = "available" if item.get("attached") else "file missing"
        items.append(
            '<label class="material-option">'
            '<input type="checkbox" name="material" value="{token}"{checked}>'
            '<div><div class="option-title">{label}</div>'
            '<div class="meta">{source} · {filename} · {availability}</div>'
            '</div></label>'.format(
                token=esc(item["token"]), checked=checked, label=esc(item["label"]),
                source=esc(source), filename=esc(item["filename"]),
                availability=esc(availability)))
    return "".join(items)


def render_app(st, cl, query):
    rows = case_rows(st)
    active_id = selected_case_id(st, query)
    body = '<div class="layout">%s%s</div>' % (
        render_list(rows, active_id),
        render_case(st, cl, active_id, query=query))
    return page("Visa Adviser Review", body)


class Handler(BaseHTTPRequestHandler):
    db_path = None

    def _store(self):
        return load_store(self.db_path)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        st = self._store()
        cl = load_checklist()
        html_body = render_app(st, cl, urllib.parse.parse_qs(parsed.query))
        self._send_html(html_body)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/review":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        case_id = (data.get("case_id") or [""])[0]
        decision = (data.get("decision") or [""])[0]
        note = (data.get("note") or [""])[0].strip()
        selected_tokens = (
            data.get("material") if "materials_present" in data and
            decision == "approved_for_final_report" else None)
        if decision not in ("approved_for_final_report", "needs_client_follow_up"):
            self.send_error(400, "unknown decision")
            return
        st = self._store()
        if not st.get_case(case_id):
            self.send_error(404, "case not found")
            return
        review_id = st.record_adviser_review(
            case_id, decision, note=note, reviewer="admin_panel",
            package_selection=selected_tokens)
        cl = load_checklist()
        notification_status = notify_client(
            st, cl, case_id, review_id, decision, note,
            selected_tokens=selected_tokens)
        self.send_response(303)
        self.send_header("Location", "/?case=%s&saved=%s&notified=%s" % (
            urllib.parse.quote(case_id), urllib.parse.quote(decision),
            urllib.parse.quote(notification_status)))
        self.end_headers()

    def _send_html(self, html_body):
        payload = html_body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the local adviser admin panel.")
    parser.add_argument("--db", default=default_db_path())
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args(argv)
    Handler.db_path = args.db
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("Admin panel: http://%s:%d" % (args.host, args.port))
    print("DB: %s" % args.db)
    server.serve_forever()


if __name__ == "__main__":
    main()
