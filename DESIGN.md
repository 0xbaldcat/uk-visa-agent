# UK Visitor Visa Preparation Agent — design notes

A WhatsApp/email agent that takes a client from "I want to visit my sister in
Manchester" to a checked, assembled application pack. Product scope is UK
Standard Visitor; the current PoC's fully tested reference scenario is a
family visit with a settled UK relative.

The two questions this document answers are **how delivery is kept stable** and
**where an agent is used instead of a fixed workflow**.

---

## 1. The constraint that shapes everything: asymmetric cost

Asking the client one unnecessary question costs a moment of mild annoyance.
Missing one required document costs a refusal: the fee is not refunded, the trip is
lost, and a refusal sits on the applicant's immigration history and weighs against
future applications. Worse, a *fabricated* detail is a mandatory refusal under
Immigration Rules 9.7.1 even when innocent, and deception carries a ten-year ban.

When the downside is that lopsided, control belongs to deterministic structure, and
the model is admitted only where it is genuinely better than code. Every decision
below follows from this.

---

## 2. Agent or workflow: per stage

| Stage | Choice | Why |
|---|---|---|
| Eligibility screen | Workflow | Decision tree, must be reproducible |
| Case profiling | **Agent** | Clients answer out of order and bury four facts in one sentence |
| Risk diagnosis | Agent + rules | Model surfaces gaps; it never rules on sufficiency |
| Checklist generation | Workflow | Pure mapping from profile to requirements. A missed item is a refusal |
| Chasing | Workflow | State machine + WhatsApp template constraints |
| Document QC | Mixed | Format/dates/identity by code; cross-document sense by model |
| Cover letter | Agent + template + guard | Needs language ability, but facts must be locked |
| Submission | Not built | No third-party API exists (see §6) |

**Shape: a state machine spine with the model at the edges.**
*Model extracts and phrases; code judges and delivers.*

A pure workflow fails on real input — "my sister's in Manchester, I'll come for
about three months" carries purpose, sponsor, duration and a risk factor in one
breath, and no form captures that. A pure agent fails on the asymmetry above.

Implementation: `state.next_action()` is a pure function of persisted case state.
Same case, same next step, every time — no model call anywhere in the decision path.

### 2.1 LLM ingress layer: every email may be natural language

The client does not know our state machine. They may write:

> Hi, I am Mei Ling Chen, Chinese passport, visiting my sister in the UK from
> 2026-10-05 to 2027-01-03. What documents do I need?

or later:

> I am self-employed, paying myself, no refusals, probably around GBP 4200.

So every inbound email should first pass through an **ingress interpreter**. Its
job is to convert messy input into structured events and fields:

- `provide_intake_facts`
- `provide_documents`
- `ask_clarification`
- `change_previous_answer`
- `cannot_provide_document`

The PoC implements the first slice: while the case is in intake, every text email
is parsed for all currently missing slots, not just the next slot. If a real
OpenAI-compatible LLM key is configured, `ChatCompletionsIntakeClient` performs
that extraction. If not, `EmailDemoModel` falls back to deterministic parsing so
the demo remains reproducible offline.

This still does **not** let the LLM drive the case. The interpreter may say
"these facts were present"; it may not decide what requirements exist, whether a
document is acceptable, or whether the pack is ready. Structured output re-enters
the workflow only after schema coercion in `llm.coerce_slot()`.

When schema validation rejects part of the candidate JSON, the ingress layer may
perform one bounded repair attempt. The repair prompt receives the original
email, target schema, accepted fields and validation errors. It is allowed to fix
format or enum shape; it is not allowed to invent facts the user did not provide.
If repair still fails, valid fields are kept, invalid fields are rejected, and the
workflow asks the user for the missing or unclear information.

The trace is persisted in `ingress_events`: candidate JSON, accepted JSON,
rejected JSON, validation errors, repair attempts and final status. The system
does not store or rely on chain-of-thought; it stores reproducible inputs and
outputs.

Case identity follows the same rule. A first-contact email with no subject token
and no reply-header mapping creates a new case automatically. Replies are mapped
back through RFC `Message-ID` / `In-Reply-To` / `References`; a fixed
`VISA_AGENT_CASE_ID` is only a demo override, not the product model.

---

## 3. Delivery stability: six failure modes and the control for each

Prompt instructions are not controls; they are requests. Each of these is enforced
structurally, and each has a test.

### 3.1 Inventing requirements
A model asked "how much money does a visitor need?" will confidently produce a
figure. **There is no statutory minimum for visitors** — the test is whether income
after existing commitments reasonably covers the trip.

*Control:* requirements live in versioned YAML with a source id resolving to a URL.
Nothing else may state a requirement. `Checklist.unsourced()` treats a dangling
source id as unsourced, and the QC report prints the list — so a pack built on
scaffolding cannot pass as one built on sourced rules.

`funds_picture()` therefore returns the trip cost and the evidenced balance side by
side, with an explicit note that no minimum exists. It deliberately reaches no
conclusion.

### 3.2 Presenting practice as law
"Six months of bank statements" is practitioner habit, not a rule. Telling a client
it is required is itself a false statement.

*Control:* checks carry `advisory: true`. Advisory failures are reported but never
block delivery, and their wording shifts from "are required" to "are commonly
expected".

### 3.3 State drift across a long conversation
A case runs for days over dozens of messages. If conversation history is state, it
rots.

*Control:* SQLite is the only source of truth. Every turn reloads the case and
re-derives the next action. The model receives a rendered state summary, never a
raw transcript.

### 3.4 The model writing the deliverable
If the model writes the pack, "your application is complete" becomes an opinion.

*Control:* the review pack is code-rendered from validated fields. Internally it
has four sections: checklist, form-answer draft, optional cover note draft and QC
report. `qc_report()` computes completeness. The model contributes narrative
paragraphs only, and each is tagged `generated: true` so a reviewer can see
exactly what it touched.

### 3.5 Fabrication — the dangerous one
A model trying to make a case look better may inflate a balance or upgrade a job
title. Given 9.7.1, this is the failure mode with catastrophic client cost.

*Control:* `facts.FactLedger` holds every value the client actually supplied. Any
generated prose is scanned for factual tokens — dates, money, significant numbers —
and blocked unless each traces to client-supplied data. Small bare integers are
excluded deliberately: a guard noisy enough to be switched off is not a guard.

```
"balance of 25000.00 since 2024-01-01"
  -> BLOCKED: 4 factual values not supplied by the client
```

### 3.6 Duplicate or lost delivery
WhatsApp webhooks retry; email threads fork.

*Control:* inbound dedupe key (a retry is dropped, never re-asked) and an outbox
written before send and marked after.

**Plus, across all of them: never fail silently.** An unimplemented check raises
`UnknownCheck` rather than reading as a pass. A model refusal escalates. A closed
WhatsApp window degrades to an approved template rather than dropping the message.

---

## 4. The line the agent does not cross

The genuine visitor test (Appendix V, V 4.2) turns on whether the applicant will
leave at the end of the visit. There is no hard standard for this — the caseworker
weighs ties, history and circumstances.

That makes it exactly the wrong thing for a model to adjudicate. "Your evidence of
intent to return is sufficient" is unverifiable, and if wrong it costs the client a
refusal.

So the agent:

- **does not** predict outcomes, estimate odds, score, or say "this is enough";
- **does** check each evidence item for present / absent / well-formed;
- **does** surface risk factors as observations with a remediation question;
- **leaves** strength assessment to the human reviewer.

Risk factors are config entries with an `observation` and one permitted client-facing
`ask`. There is no scoring field anywhere in the schema, and a test asserts none
appears.

This is also why the human review gate exists. It is not decoration: nothing in the
system is competent to judge sufficiency, so something outside it must.

### 4.1 A trap worth naming: audience confusion in the cover letter

Risk factors carry an `observation` written for the adviser, in the caseworker's
framing — *"raises doubt about the intention to leave"*.

The obvious implementation is to use that text as the cover letter paragraph when
the model has not drafted one. That would be a serious bug: the cover letter is read
by the **decision-maker**, so it would have the applicant stating their own weakness
in the Home Office's own language.

Undrafted risks are therefore omitted from the letter and listed under
`risks_not_yet_addressed` for the reviewer. Silence is safe; a weakness argued
against yourself is not. Pinned by a test.

---

## 5. Channel split

Different physics, not cosmetic preference:

- **WhatsApp** — low friction, mobile, photo uploads. Conversation, interviewing,
  chasing.
- **Email** — structured, archivable, attachments. Checklist, QC report, final pack.

*Conversation on WhatsApp, documents by email.* Nobody reads a twenty-item checklist
in a chat bubble, and nobody replies to email eight times a day.

The 24-hour customer-service window is modelled rather than mocked away, because it
constrains the product: once it closes, only pre-approved templates may be sent, so
chasing cannot simply retry until the client responds. `Router` degrades a
conversational send to an approved template automatically.

---

## 6. What this deliberately does not do

**It does not submit anything.** The Home Office exposes no third-party submission
API; applications go through the official GOV.UK service, with biometrics at a visa
centre. This is a permanent ceiling, not a PoC shortcut.

The deliverable ceiling is therefore: a checked pack plus a gap report, handed to
the client or their adviser to submit themselves.

---

## 7. Production TODO

Out of scope here, but real:

1. **Data protection** — passports and bank statements are high-sensitivity personal
   data. Encryption at rest, retention limits, processor agreements, transfer
   mechanism. Must be done before any real client.
2. **Rule freshness** — monitor GOV.UK; rule data needs effective dates and a review
   cadence. Fees, processing times and WhatsApp pricing change often and are already
   isolated in `sources.yaml:volatile` with `as_of` stamps rather than hardcoded.
3. **WhatsApp productionisation** — Business API number, template approval, and a
   cost model for the 2026-10-01 pricing change.
4. **Real model hardening** — the PoC has an optional OpenAI-compatible intake
   adapter; production still needs provider observability, retries, redaction,
   per-call budgets, and escalation when the model is unavailable.
5. **Audit retention** — every client-facing statement should be reconstructible
   against the rule version and reviewer at the time.
6. **Refusal feedback loop** — refusal reasons should flow back into the risk model.
   This is the only way the product gets better over time.
7. **Accountability model** — who signs off the pack, and under what authority.
   (Relevant if this ever serves real clients; not a PoC concern.)
8. **Adversarial input** — a client who supplies forged documents. Detection is hard;
   at minimum the reviewer needs to see what was checked and what was not.

---

## 8. Layout

```
config/sources.yaml               provenance registry + volatile dated values
config/routes.yaml                route registry: visa type, purposes, component list
config/standard_visitor_core.yaml shared Standard Visitor slots/evidence/checks
config/purposes/*.yaml            purpose packs such as family_visit, tourism scaffold
config/applicant_*.yaml           reusable funding, employment and home-tie profile
src/state.py                      stages, legal transitions, next_action()
src/checklist.py                  composes route packs; only module that answers "what is required"
src/validate.py                   deterministic checks; advisory vs blocking
src/diagnose.py                   risk observations, tie coverage, funds picture
src/facts.py                      fabrication guard
src/ingress.py                    structured event validation, repair, trace object
src/llm.py                        model seam: 3 jobs, schema-checked returns
src/compose.py                    action -> words
src/deliver.py                    structured deliverables and review-pack rendering
src/channels.py                   WhatsApp window, in-memory email, router
src/real_email.py                 optional SMTP/IMAP adapter for live walkthroughs
src/email_bridge.py               raw .eml / IMAP -> Engine bridge
src/email_model.py                email intake parser + optional chat-completions adapter
src/engine.py                     turn handler
demo.py                           end-to-end scripted walkthrough
real_email_demo.py                live email smoke script
email_poll_once.py                live mailbox poll-once loop
tests/test_poc.py                 tests, one per invariant
```

Run: `python3 demo.py` and `python3 -m unittest discover -s tests`
