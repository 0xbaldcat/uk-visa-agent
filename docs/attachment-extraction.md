# Attachment extraction interface

The email walkthrough accepts real client-style attachments. The conversion
pipeline is:

1. `email_bridge.EmailPoller` saves every email attachment to disk.
2. `email_model.EmailDemoModel.extract_fields(path, wanted_fields)` calls
   `document_extract.extract_fields_from_file(...)`.
3. `DocumentTextExtractor.extract_text(path)` returns OCR/document text.
4. `extract_fields_from_text(text, wanted_fields)` emits only requested fields.
5. `Engine.apply_document(...)` runs the existing deterministic validation rules.

## Adapter contract

Input:

- `path`: local saved attachment path
- `wanted_fields`: checklist field ids from `config/visitor_family_visit.yaml`

Output:

- `dict[str, str | bool]`, containing only fields requested by `wanted_fields`

Failure:

- raise `llm.ModelRefusal`; the engine records the document as unreadable and
  asks the client to resupply it.

## Current local support

- `.json`: developer shortcut containing pre-extracted fields.
- `.txt`, `.text`, `.md`, `.csv`: direct text parsing.
- `.docx`: reads `word/document.xml` body text.
- `.pdf`: parses visible text from simple text PDFs. Scanned or compressed PDFs
  fall back to OCR when configured.
- images and scanned PDFs: use Baidu OCR in live mode when
  `VISA_AGENT_BAIDU_OCR_API_KEY` and `VISA_AGENT_BAIDU_OCR_SECRET_KEY` are set.
  Same-path `.ocr.txt` sidecars are only for deterministic offline tests.

For example, if the saved attachment is:

```text
inbound-attachments/case-001/0-passport.jpg
```

the local extractor can use this sidecar in offline tests:

```text
inbound-attachments/case-001/0-passport.jpg.ocr.txt
```

## Fixture manifest shape

Recommended fixture manifest entry:

```yaml
- evidence_id: passport
  filename: passport-pass.pdf
  content_type: application/pdf
  expected_fields:
    holder_name: Mei Ling Chen
    passport_number: EK1234567
    expiry_date: "2029-04-30"
    prior_compliant_travel: true
  expected_blocking_failures: []
```

For negative examples:

```yaml
- evidence_id: travel_itinerary
  filename: travel-itinerary-wrong-name.pdf
  content_type: application/pdf
  expected_fields:
    outbound_date: "2026-10-05"
    return_date: "2027-01-03"
    passenger_name: M. L. Chen
  expected_blocking_failures:
    - name_matches
```

Filename should start with the configured `evidence_id` when possible, because
the email bridge maps attachments to checklist items from filename aliases.

## Checked-in realistic fixtures

`fixtures/realistic-materials/` contains synthetic pass/fail examples for all
eight items required by the demo applicant. The set mixes native-text PDFs,
scanned PDFs, DOCX files and PNG images. `manifest.yaml` is executable test data:
the unit suite checks each file's extracted fields and blocking failure kinds
against it. Scanned/image fixtures use deterministic `.ocr.txt` sidecars in
offline tests; live email uses the Baidu OCR adapter so a client only sends the
original PDF/image attachment.
