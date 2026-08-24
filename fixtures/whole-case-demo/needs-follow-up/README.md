# Whole-case demo: adviser follow-up required

> Synthetic test data only. None of the people, identifiers or documents in
> this folder are real or valid for an immigration application.

## Scenario

A 90-day visit to a settled sister, self-employment, and an evidenced GBP 5,100 balance against a declared GBP 4,200 trip cost. Every individual document is readable and consistent; the questions arise only when the case is considered as a whole.

## First email

Subject: `Standard Visitor documents - Mei Ling Chen`

```text
Hello, my name is Mei Ling Chen and I hold a Chinese passport. I plan to visit my settled sister Hui Chen in Manchester from 5 October 2026 to 3 January 2027. I am self-employed and will pay my own travel and living costs; my sister will provide free accommodation. I estimate the total trip cost at GBP 4,200. I have not had a UK or other visa refusal. I have attached my documents.
```

## Attachments

- `accommodation_proof_needs_follow_up_mei_ling_chen.docx`
- `bank_statements_needs_follow_up_mei_ling_chen.pdf`
- `home_ties_evidence_needs_follow_up_mei_ling_chen.pdf`
- `passport_needs_follow_up_mei_ling_chen.pdf`
- `self_employment_evidence_needs_follow_up_mei_ling_chen.pdf`
- `sponsor_invitation_letter_needs_follow_up_mei_ling_chen.docx`
- `sponsor_status_proof_needs_follow_up_hui_chen.pdf`
- `travel_itinerary_needs_follow_up_mei_ling_chen.pdf`

Send the first email text and these attachments through the normal email demo
path. Every filename starts with its checklist evidence ID, so the current
email bridge maps it automatically.

## Expected result

All eight required documents should pass document QC. The deterministic whole-case fallback should produce **3 adviser follow-up questions**, in the duration, financial-resources and home-country-commitments dimensions. They enter human review and are not automatically emailed to the client.


## Human-review continuation

The PDF below is **not part of the initial eight-document submission**. Use it
only after the case has entered human review.

### Adviser follow-up question

```text
Please explain why your visit needs to last from 5 October 2026 to 3 January 2027, and how Chen Design Studio and your mother's care will be managed while you are away. Please provide any documents already available that support these arrangements.
```

### Client reply

```text
Dear Adviser,

Please find attached my Visit and Home Arrangements Statement. It explains the purpose and timing of my 90-day visit, how Chen Design Studio will continue operating in Shanghai, how my mother will be cared for, and when I will resume in-person work after returning to China.

Kind regards,
Mei Ling Chen
```

Attach `visit_and_home_arrangements_mei_ling_chen.pdf` to that reply. The reply text should appear under Human
Review Client Replies and the PDF under Human Review Files. Whole-case analysis
must not run again automatically; the adviser decides whether to ask again or
include the new file in the final package.
