# Whole-case demo: no follow-up questions

> Synthetic test data only. None of the people, identifiers or documents in
> this folder are real or valid for an immigration application.

## Scenario

A 20-day, self-funded family visit with hotel accommodation, stable employment, a comfortable evidenced balance and several home-country ties. The visited cousin is not British or settled, so sponsor-status documents are not part of this checklist branch.

## First email

Subject: `Standard Visitor documents - Li Na Wang`

```text
Hello, my name is Li Na Wang and I hold a Chinese passport. I plan to visit London from 10 October 2026 to 30 October 2026 for a family visit with my cousin. My cousin is not British or settled in the UK and I will stay at a hotel. I am employed full-time and will fund the trip myself. The estimated total cost is GBP 2,500. I have not had a UK or other visa refusal. I have attached my documents.
```

## Attachments

- `accommodation_proof_no_issue_li_na_wang.docx`
- `bank_statements_no_issue_li_na_wang.pdf`
- `employment_letter_no_issue_li_na_wang.docx`
- `home_ties_evidence_no_issue_li_na_wang.pdf`
- `passport_no_issue_li_na_wang.pdf`
- `travel_itinerary_no_issue_li_na_wang.pdf`

Send the first email text and these attachments through the normal email demo
path. Every filename starts with its checklist evidence ID, so the current
email bridge maps it automatically.

## Expected result

All six required documents should pass document QC. The deterministic whole-case fallback should produce **0 observations and 0 follow-up questions**. The case still enters human review; the adviser may confirm the selected files and package them without inventing a question.

Regenerate both whole-case directories and ZIP files with:

```bash
python3 scripts/generate_whole_case_demo_materials.py
```
