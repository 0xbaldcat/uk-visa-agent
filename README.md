# UK Visitor Visa Preparation Agent — PoC

An email/WhatsApp-style agent that helps a client prepare a UK Standard Visitor
application pack. It collects intake facts, requests route-specific evidence,
extracts and validates uploaded documents, prepares an adviser review pack, and
returns the adviser-approved materials as a ZIP file.

The agent does **not** submit an application, predict the visa outcome, or decide
that the evidence is sufficient. Those boundaries are enforced in code, not left
to a model prompt.

## Current scope

- Product scope: UK Standard Visitor.
- Verified PoC route: family visit with a settled UK relative
  (`visitor_family_visit`).
- Scaffold-only routes: tourism and business. They are marked unverified and must
  not be treated as live rule packs.
- Primary live channel: email over SMTP/IMAP. WhatsApp behavior is represented in
  the offline demo, including its 24-hour reply-window constraint.
- Human review is mandatory before the final client package is sent.

## Quick start

```bash
python3 -m pip install -r requirements.txt
python3 demo.py
python3 demo.py --email-only
python3 -m unittest discover -s tests
```

The demos are deterministic and run without external services. Test materials are
synthetic and live under `fixtures/`.

## How the system works

```text
Client email
  -> parse and de-duplicate
  -> resolve or create case
  -> collect structured intake facts
  -> derive required evidence from versioned rules
  -> save attachments and extract fields
  -> run deterministic document checks
  -> request missing/replacement evidence
  -> build evidence-backed whole-case adviser notes
  -> human review and file selection
  -> email one ZIP containing the final report and selected files
```

SQLite is the source of truth. The next action is derived from persisted case
state by `src/state.py`; an LLM never chooses the workflow transition. Optional
LLM adapters may interpret natural-language intake, extract missing document
fields, draft guarded narrative, and propose whole-case observations. Their
outputs are candidate data that must pass schema, reference, and policy checks.

See [TDD.md](TDD.md) for the design decisions, architecture, data flow, and
implementation details.

## Live email walkthrough

Use a dedicated test mailbox and an app-specific password. Do not use a personal
primary mailbox.

```bash
export VISA_AGENT_SMTP_HOST=smtp.gmail.com
export VISA_AGENT_SMTP_PORT=587
export VISA_AGENT_IMAP_HOST=imap.gmail.com
export VISA_AGENT_IMAP_PORT=993
export VISA_AGENT_EMAIL_USER=visa-agent-demo@example.com
export VISA_AGENT_EMAIL_PASSWORD='app-specific-password'
export VISA_AGENT_FROM_EMAIL=visa-agent-demo@example.com
export VISA_AGENT_TO_EMAIL=client-test@example.com
export VISA_AGENT_DB_PATH=visa-agent.sqlite3
export VISA_AGENT_ATTACHMENT_DIR=inbound-attachments

python3 email_poll_once.py
```

`email_poll_once.py` fetches unseen messages, routes each message to a case using
the subject case token and RFC reply headers, processes all mapped attachments,
sends at most one workflow response for the email, and marks the message seen.
An unmatched first-contact email creates a case automatically.

`VISA_AGENT_CASE_ID` is an optional demo override that sends otherwise unmatched
mail to one fixed case. Leave it unset for normal multi-case behavior.

### Optional document OCR

Text PDFs, DOCX, and text files are parsed locally. Images and scanned PDFs can
use the Baidu OCR adapter:

```bash
export VISA_AGENT_BAIDU_OCR_API_KEY='...'
export VISA_AGENT_BAIDU_OCR_SECRET_KEY='...'
```

OCR sidecar files remain available for deterministic fixtures; live clients do
not need to provide them.

### Optional OpenAI-compatible LLM

```bash
export VISA_AGENT_LLM_API_KEY='...'
export VISA_AGENT_LLM_MODEL='deepseek-v4-flash'
export VISA_AGENT_LLM_BASE_URL='https://api.deepseek.com'
```

With these variables set, the same adapter is available for natural-language
intake and missing document-field extraction. The deterministic parser remains
the fallback. To enable model-generated whole-case adviser observations, opt in
separately:

```bash
export VISA_AGENT_CASE_ANALYSIS_LLM=1
export VISA_AGENT_CASE_ANALYSIS_LLM_MODEL='deepseek-v4-pro'
```

If the model is unavailable or produces an invalid candidate, the workflow keeps
accepted facts, rejects unsupported output, and either uses deterministic
whole-case observations or asks for clarification.

## Adviser admin panel

Cases with complete, passing required evidence enter `human_review`.

```bash
python3 admin_panel.py --db visa-agent.sqlite3 --port 8765
```

Open <http://127.0.0.1:8765>. The reviewer can inspect accepted materials,
preview or download case files, send a follow-up message, or approve a selected
set of files. Approval emails exactly one archive:

```text
visa-reviewed-materials-package.zip
├── visa-final-review-report.pdf
└── materials/
    └── <reviewer-selected files>
```

This panel is a local PoC surface. It has no production authentication,
authorization, encryption-at-rest, retention policy, or malware-scanning gateway.

## Rule and evidence data

Rules are composed rather than copied per scenario:

- `config/routes.yaml` selects the route components.
- `config/standard_visitor_core.yaml` holds shared Standard Visitor rules.
- `config/purposes/` holds purpose-specific packs.
- `config/applicant_financial_home_profile.yaml` holds reusable profile rules.
- `config/sources.yaml` resolves requirement source IDs to GOV.UK URLs.
- `config/case_analysis_rubric.yaml` constrains whole-case adviser observations.

Practice such as a commonly requested statement period is marked `advisory` and
cannot block delivery as if it were law. Volatile data is dated rather than
hardcoded.

## Documentation

- [Technical design document](TDD.md) — design decisions and implementation
- [Engineering explainer](docs/engineering-explainer.html)
- [Research and product context](docs/visa-agent-poc-research.md)
- [Materials checklist](docs/materials-checklist.md)
- [Material validation rules](docs/material-validation-rules.md)
- [Whole-case analysis rubric](docs/case-analysis-rubric.md)
- [Attachment extraction interface](docs/attachment-extraction.md)
- [Synthetic material guide](fixtures/realistic-materials/README.md)

## Safety boundary

The system reports whether required evidence is present, readable, and consistent
with explicit checks. It may surface evidence-backed questions for an adviser. It
does not score the case, estimate approval odds, or claim that the genuine visitor
test is met. The final decision on case strength belongs to the human reviewer.
