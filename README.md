# UK Visitor Visa Preparation Agent — PoC

A WhatsApp/email agent that walks a client through preparing a UK Standard Visitor
application, the way a human consultant would: it interviews, spots the weak points
in the case, chases the right documents, checks what arrives, and assembles a pack.

It does **not** submit the application — the Home Office has no third-party
submission API, so that step is permanently the applicant's own.

## Run it

```bash
python3 -m pip install -r requirements.txt
python3 demo.py                              # WhatsApp + email walkthrough
python3 demo.py --email-only                 # fallback demo if WhatsApp is unavailable
python3 -m unittest discover -s tests        # full regression suite
```

The model seam is stubbed, so the demo is offline and deterministic.

The live email path can optionally use an OpenAI-compatible LLM for natural
language intake parsing. LLM output is treated as candidate structure: schema
validation accepts valid fields, rejects invalid fields, and may perform one
bounded repair attempt. The trace is recorded in `ingress_events`.

The review pack also includes a whole-case analysis layer: code prepares a fact
context, an optional model may propose evidence-backed observations, and code
rejects observations with missing references or outcome/sufficiency claims.

Routes are composed from versioned rule packs rather than copied per scenario.
`config/routes.yaml` maps a route to shared Standard Visitor core rules, one or
more purpose packs, and reusable applicant profile packs. The current verified
reference scenario is `visitor_family_visit`; tourism and business are scaffold
routes that show the extension shape but remain unverified until their sources,
fixtures and adviser SOP are added.

## Documentation

- [Engineering explainer](docs/engineering-explainer.html)
- [Research and design](docs/visa-agent-poc-research.md)
- [Materials checklist](docs/materials-checklist.md)
- [Material validation rules](docs/material-validation-rules.md)
- [Attachment extraction interface](docs/attachment-extraction.md)
- [Synthetic PDF/DOCX/image test materials](fixtures/realistic-materials/README.md)

## Live Email Smoke

For a real mailbox walkthrough, use a dedicated test mailbox with SMTP, IMAP and
an app-specific password. Do not use a personal primary mailbox.

```bash
export VISA_AGENT_SMTP_HOST=smtp.gmail.com
export VISA_AGENT_SMTP_PORT=587
export VISA_AGENT_IMAP_HOST=imap.gmail.com
export VISA_AGENT_IMAP_PORT=993
export VISA_AGENT_EMAIL_USER=visa-agent-demo@example.com
export VISA_AGENT_EMAIL_PASSWORD='app-specific-password'
export VISA_AGENT_FROM_EMAIL=visa-agent-demo@example.com
export VISA_AGENT_TO_EMAIL=client-test@example.com

python3 real_email_demo.py
```

Set `VISA_AGENT_FETCH_UNSEEN=1` to also list unread replies from the test client
mailbox after sending. The workflow assembles a human-readable adviser review
pack, but the customer-facing completion email does not attach it before human
review.

For a real multi-turn mailbox path, run one poll cycle after sending an email to
the agent mailbox:

```bash
export VISA_AGENT_CASE_ID=case-001
export VISA_AGENT_DB_PATH=visa-agent.sqlite3
export VISA_AGENT_ATTACHMENT_DIR=inbound-attachments
python3 email_poll_once.py
```

`email_poll_once.py` fetches unseen raw messages, de-duplicates by RFC
`Message-ID`, resolves the case from `[visa-agent:<case-id>]` or reply headers,
and creates a new case when a first-contact email has no known thread mapping.
Then it saves attachment bytes, routes the body and attachments through `Engine`,
and sends the next agent response by SMTP. `VISA_AGENT_CASE_ID` is optional; set
it only when you deliberately want all unmatched mail to go into one demo case.
For the local no-LLM demo, attach real files whose names start with the checklist
evidence id, such as `passport.pdf`, `bank_statements.docx`, or
`home_ties_evidence.jpg`.

Live email limitations for the two-day PoC: the deterministic email model parses
plain-text replies for the current expected slot and extracts fields from text
PDFs, DOCX/text files, JSON developer shortcuts, and OCR sidecars. Scanned PDFs
and images need a real OCR adapter; the adapter seam is
`document_extract.DocumentTextExtractor`.

## Local Adviser Admin Panel

Completed cases enter `human_review`. The local admin panel shows those cases,
the collected facts and materials, the internal adviser review pack, and a simple
review decision form.

```bash
python3 admin_panel.py --db live-panwei.sqlite3 --port 8765
```

Open <http://127.0.0.1:8765>. Decisions are written to `adviser_reviews` and the
case audit trail. This is a PoC review surface, not an authenticated production
admin app.

## The demo case

Deliberately a hard one: a self-employed applicant, funds only just adequate, a
sister settled in Manchester, asking for a 90-day stay. The agent spots the cluster
(settled UK relative + irregular income + long stay + thin evidenced ties) and asks
for strengthening evidence instead of just filing what it is handed. It also
catches an air ticket in the wrong name and sends it back.

The walkthrough shows: intake, webhook de-duplication, risk diagnosis, a blocking
validation failure, the WhatsApp 24-hour window forcing a template fallback,
remediation, the computed QC report, the fabrication guard blocking invented
figures, the review pack, and the human review gate.

## Design

See **[DESIGN.md](DESIGN.md)** — covers the agent-vs-workflow split per stage and
the six delivery-stability failure modes with the control and test for each.

Two sentences of it:

> Missing a document costs a refusal, a non-refundable fee and a mark on the
> applicant's history; asking one extra question costs nothing. When cost is that
> asymmetric, deterministic structure holds control and the model works at the edges.

> **Model extracts and phrases; code judges and delivers.**

## What the agent will not do

It reports whether evidence is **present and well-formed**. It never rules on
whether a case is **strong enough** — the genuine visitor test has no hard standard,
so a sufficiency verdict would be unverifiable and, if wrong, expensive for the
client. Strength is the human reviewer's call. There is no scoring field in the
schema, and a test asserts none appears.

## Provenance

Every requirement carries a source id resolving to a GOV.UK URL in
`config/sources.yaml`. Anything unsourced is listed in the QC report. Practitioner
practice (e.g. "six months of statements") is marked `advisory` and never enforced
as law — it is reported with different wording and does not block delivery.

Volatile values — fees, processing times — are dated data, never hardcoded.
