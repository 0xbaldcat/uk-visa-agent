#!/usr/bin/env python3
"""Tiny local admin panel for the visa PoC.

This is intentionally not a production admin app. It is a demo surface for the
human-review gate: show cases waiting on an adviser, show the internal review
pack, and persist the adviser's decision.
"""
import argparse
import html
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

import checklist  # noqa: E402
import deliver  # noqa: E402
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
    links = []
    for row in rows:
        case = store_mod.Case(row["id"], "visitor_family_visit", row["stage"],
                              {}, {}, None)
        active = " active" if row["id"] == active_id else ""
        label, cls = review_badge(row["review_decision"], case.stage.value)
        review_meta = row["review_created_at"] or "not reviewed"
        links.append(
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
    return '<div class="panel list">%s</div>' % "".join(links)


def render_case(st, cl, case_id, query=None):
    if not case_id:
        return '<div class="panel empty">No case selected.</div>'
    query = query or {}
    case = st.get_case(case_id)
    satisfied, missing, failing = case.outstanding(cl)
    review = st.latest_adviser_review(case_id)
    pack = review_pack(cl, case)
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
    saved = (query.get("saved") or [""])[0]
    notice = ""
    if saved:
        notice = ('<div class="notice">Review decision recorded: %s</div>'
                  % esc(saved.replace("_", " ")))
    history = st.conn.execute(
        "SELECT decision, note, reviewer, created_at FROM adviser_reviews "
        "WHERE case_id = ? ORDER BY id DESC LIMIT 8", (case_id,)).fetchall()
    history_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            esc(row["created_at"]), esc(row["decision"]), esc(row["reviewer"]),
            esc(row["note"]))
        for row in history)
    if not history_rows:
        history_rows = '<tr><td colspan="4" class="meta">No review decisions yet.</td></tr>'
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
  </div>

  <h2>Adviser Decision</h2>
  <form method="post" action="/review">
    <input type="hidden" name="case_id" value="{case_id}">
    <label>Review note</label>
    <textarea name="note" rows="3" placeholder="What did you decide or what should the client provide next?"></textarea>
    <div class="actions">
      <button name="decision" value="approved_for_final_report">Approve for final report</button>
      <button class="secondary" name="decision" value="needs_client_follow_up">Needs client follow-up</button>
    </div>
  </form>

  <h2>Review History</h2>
  <table><thead><tr><th>Time</th><th>Decision</th><th>Reviewer</th><th>Note</th></tr></thead><tbody>{history_rows}</tbody></table>

  <h2>Intake Facts</h2>
  <table>{slot_rows}</table>

  <h2>Materials</h2>
  <table><thead><tr><th>Material</th><th>Status</th><th>File</th></tr></thead><tbody>{rows}</tbody></table>

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
        history_rows=history_rows,
        slot_rows=slot_rows,
        rows="".join(rows),
        pack=esc(pack))


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
        if decision not in ("approved_for_final_report", "needs_client_follow_up"):
            self.send_error(400, "unknown decision")
            return
        st = self._store()
        if not st.get_case(case_id):
            self.send_error(404, "case not found")
            return
        st.record_adviser_review(case_id, decision, note=note, reviewer="admin_panel")
        self.send_response(303)
        self.send_header("Location", "/?case=%s&saved=%s" % (
            urllib.parse.quote(case_id), urllib.parse.quote(decision)))
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
