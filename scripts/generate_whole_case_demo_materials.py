#!/usr/bin/env python3
"""Generate two synthetic, runnable whole-case demo material packs.

The fixtures deliberately separate document QC from whole-case analysis:

* ``no-issue`` has six individually valid, mutually consistent documents and
  produces zero deterministic follow-up questions.
* ``needs-follow-up`` has eight individually valid, mutually consistent
  documents but triggers three whole-case context questions.

All identities, reference numbers and documents are fictional.  Native-text
PDF and DOCX files keep the offline demo reproducible without OCR credentials.
"""
from pathlib import Path
import shutil
import sys
import zipfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_realistic_fixtures import write_docx, write_text_pdf  # noqa: E402


OUT = ROOT / "fixtures" / "whole-case-demo"
FIXED_ZIP_TIME = (2026, 8, 23, 0, 0, 0)
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def attachment(evidence_id, filename, content_type, expected_fields):
    return {
        "evidence_id": evidence_id,
        "filename": filename,
        "content_type": content_type,
        "expected_fields": expected_fields,
        "expected_blocking_failures": [],
    }


def write_readme(pack_dir, title, scenario, subject, body, expected):
    attachment_names = sorted(
        path.name for path in pack_dir.iterdir()
        if path.is_file() and path.name != "README.md")
    files = "\n".join("- `%s`" % name for name in attachment_names)
    content = """# {title}

> Synthetic test data only. None of the people, identifiers or documents in
> this folder are real or valid for an immigration application.

## Scenario

{scenario}

## First email

Subject: `{subject}`

```text
{body}
```

## Attachments

{files}

Send the first email text and these attachments through the normal email demo
path. Every filename starts with its checklist evidence ID, so the current
email bridge maps it automatically.

## Expected result

{expected}
""".format(title=title, scenario=scenario, subject=subject, body=body,
           files=files, expected=expected)
    (pack_dir / "README.md").write_text(content, encoding="utf-8")


def write_deterministic_zip(pack_dir, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(pack_dir.iterdir(), key=lambda item: item.name):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())


def generate_no_issue():
    pack_dir = OUT / "no-issue"
    pack_dir.mkdir(parents=True, exist_ok=True)
    subject = "Standard Visitor documents - Li Na Wang"
    body = (
        "Hello, my name is Li Na Wang and I hold a Chinese passport. I plan to "
        "visit London from 10 October 2026 to 30 October 2026 for a family visit "
        "with my cousin. My cousin is not British or settled in the UK and I will "
        "stay at a hotel. I am employed full-time and will fund the trip myself. "
        "The estimated total cost is GBP 2,500. I have not had a UK or other visa "
        "refusal. I have attached my documents."
    )
    slots = {
        "applicant_name": "Li Na Wang",
        "nationality": "Chinese",
        "trip_start": "2026-10-10",
        "trip_end": "2026-10-30",
        "visit_purpose": "family_visit",
        "has_uk_settled_relative": False,
        "employment_status": "employed",
        "third_party_funding": False,
        "prior_uk_refusal": False,
        "estimated_trip_cost_gbp": 2500.0,
    }
    rows = []

    filename = "passport_no_issue_li_na_wang.pdf"
    fields = {
        "holder_name": "Li Na Wang", "passport_number": "LN2030405",
        "expiry_date": "2031-06-30", "nationality": "Chinese",
        "prior_compliant_travel": True,
    }
    write_text_pdf(pack_dir / filename, "PASSPORT BIOGRAPHIC PAGE", [
        "Holder Name: Li Na Wang", "Passport Number: LN2030405",
        "Nationality: Chinese", "Expiry Date: 2031-06-30",
        "Prior Compliant Travel: yes", "Previous travel: France 2024; Japan 2025",
        "Document reference: SYNTH-LNW-2030405",
    ])
    rows.append(attachment("passport", filename, "application/pdf", fields))

    filename = "bank_statements_no_issue_li_na_wang.pdf"
    fields = {
        "account_holder_name": "Li Na Wang", "period_start": "2026-02-15",
        "period_end": "2026-08-20", "closing_balance": "18500.00",
        "currency": "GBP",
    }
    write_text_pdf(pack_dir / filename, "PERSONAL CURRENT ACCOUNT STATEMENT", [
        "Account Holder Name: Li Na Wang", "Period Start: 2026-02-15",
        "Period End: 2026-08-20", "Closing Balance: 18500.00", "Currency: GBP",
        "Statement number: 08/2026", "Account type: Salary Current Account",
        "2026-08-20 | CARD PAYMENT | GROCERIES | -64.20 | 18500.00",
        "2026-08-15 | SALARY | HORIZON TECHNOLOGY | +4100.00 | 18564.20",
        "2026-08-08 | MORTGAGE PAYMENT | -1280.00 | 14464.20",
    ])
    rows.append(attachment("bank_statements", filename, "application/pdf", fields))

    filename = "travel_itinerary_no_issue_li_na_wang.pdf"
    fields = {
        "outbound_date": "2026-10-10", "return_date": "2026-10-30",
        "passenger_name": "Li Na Wang",
    }
    write_text_pdf(pack_dir / filename, "RETURN FLIGHT ITINERARY", [
        "Booking Reference: SYNTH-LNW-2610", "Passenger Name: Li Na Wang",
        "Outbound Date: 2026-10-10", "Return Date: 2026-10-30",
        "OUTBOUND | ZX301 | Shanghai PVG to London LHR",
        "RETURN | ZX302 | London LHR to Shanghai PVG",
        "Cabin: Economy Flexible", "Status: Reservation for visa documentation",
    ])
    rows.append(attachment("travel_itinerary", filename, "application/pdf", fields))

    filename = "accommodation_proof_no_issue_li_na_wang.docx"
    fields = {
        "address": "22 Southwark Street, London SE1 1TU",
        "host_name": "Southwark Riverside Hotel",
        "stay_start": "2026-10-10", "stay_end": "2026-10-30",
    }
    write_docx(pack_dir / filename, "Hotel booking confirmation", [
        "Guest Name: Li Na Wang", "Booking Reference: SYNTH-HOTEL-2210",
        "Address: 22 Southwark Street, London SE1 1TU",
        "Host Name: Southwark Riverside Hotel", "Stay Start: 2026-10-10",
        "Stay End: 2026-10-30", "Room type: Single room, prepaid flexible rate",
        "This synthetic booking confirms accommodation for the full planned visit.",
    ])
    rows.append(attachment("accommodation_proof", filename, DOCX_TYPE, fields))

    filename = "employment_letter_no_issue_li_na_wang.docx"
    fields = {
        "employer_name": "Shanghai Horizon Technology Co., Ltd.",
        "job_title": "Senior Product Manager", "leave_start": "2026-10-09",
        "leave_end": "2026-10-31", "annual_salary": "CNY 420000",
    }
    write_docx(pack_dir / filename, "Employment and approved leave confirmation", [
        "To: Entry Clearance Officer", "Employee Name: Li Na Wang",
        "Employer Name: Shanghai Horizon Technology Co., Ltd.",
        "Job Title: Senior Product Manager", "Leave Start: 2026-10-09",
        "Leave End: 2026-10-31", "Annual Salary: CNY 420000",
        "Li Na Wang has worked with us full-time since 2020-05-18.",
        "Her paid leave is approved and she will resume work on 2026-11-02.",
    ])
    rows.append(attachment("employment_letter", filename, DOCX_TYPE, fields))

    filename = "home_ties_evidence_no_issue_li_na_wang.pdf"
    fields = {
        "tie_types": "full-time employment; apartment mortgage; spouse and child in Shanghai",
    }
    write_text_pdf(pack_dir / filename, "HOME-COUNTRY TIES SUMMARY", [
        "Applicant Name: Li Na Wang",
        "Tie Types: full-time employment; apartment mortgage; spouse and child in Shanghai",
        "Property city: Shanghai", "Mortgage status: active",
        "Household members remaining in China: spouse and school-age child",
        "Supporting references: SYNTH-MTG-8821; SYNTH-HH-1930",
    ])
    rows.append(attachment("home_ties_evidence", filename, "application/pdf", fields))

    write_readme(
        pack_dir,
        "Whole-case demo: no follow-up questions",
        "A 20-day, self-funded family visit with hotel accommodation, stable "
        "employment, a comfortable evidenced balance and several home-country ties. "
        "The visited cousin is not British or settled, so sponsor-status documents "
        "are not part of this checklist branch.",
        subject,
        body,
        "All six required documents should pass document QC. The deterministic "
        "whole-case fallback should produce **0 observations and 0 follow-up "
        "questions**. The case still enters human review; the adviser may confirm "
        "the selected files and package them without inventing a question.",
    )
    return {
        "id": "no_issue", "label": "No material follow-up identified",
        "folder": "no-issue", "zip": "no-issue.zip",
        "intake_email": {"subject": subject, "body": body},
        "slots": slots, "attachments": rows,
        "expected_document_qc": "all_required_documents_pass",
        "expected_deterministic_analysis": {
            "candidate_source": "deterministic_fallback",
            "observation_count": 0, "follow_up_count": 0, "dimensions": [],
        },
    }


def generate_needs_follow_up():
    pack_dir = OUT / "needs-follow-up"
    pack_dir.mkdir(parents=True, exist_ok=True)
    subject = "Standard Visitor documents - Mei Ling Chen"
    body = (
        "Hello, my name is Mei Ling Chen and I hold a Chinese passport. I plan to "
        "visit my settled sister Hui Chen in Manchester from 5 October 2026 to "
        "3 January 2027. I am self-employed and will pay my own travel and living "
        "costs; my sister will provide free accommodation. I estimate the total "
        "trip cost at GBP 4,200. I have not had a UK or other visa refusal. I have "
        "attached my documents."
    )
    slots = {
        "applicant_name": "Mei Ling Chen", "nationality": "Chinese",
        "trip_start": "2026-10-05", "trip_end": "2027-01-03",
        "visit_purpose": "family_visit", "has_uk_settled_relative": True,
        "employment_status": "self_employed", "third_party_funding": False,
        "prior_uk_refusal": False, "estimated_trip_cost_gbp": 4200.0,
    }
    rows = []

    filename = "passport_needs_follow_up_mei_ling_chen.pdf"
    fields = {
        "holder_name": "Mei Ling Chen", "passport_number": "EK1234567",
        "expiry_date": "2029-04-30", "nationality": "Chinese",
        "prior_compliant_travel": True,
    }
    write_text_pdf(pack_dir / filename, "PASSPORT BIOGRAPHIC PAGE", [
        "Holder Name: Mei Ling Chen", "Passport Number: EK1234567",
        "Nationality: Chinese", "Expiry Date: 2029-04-30",
        "Prior Compliant Travel: yes", "Previous travel: France 2024; Japan 2025",
        "Document reference: SYNTH-MLC-1234567",
    ])
    rows.append(attachment("passport", filename, "application/pdf", fields))

    filename = "bank_statements_needs_follow_up_mei_ling_chen.pdf"
    fields = {
        "account_holder_name": "Mei Ling Chen", "period_start": "2026-02-10",
        "period_end": "2026-08-18", "closing_balance": "5100.00", "currency": "GBP",
    }
    write_text_pdf(pack_dir / filename, "PERSONAL CURRENT ACCOUNT STATEMENT", [
        "Account Holder Name: Mei Ling Chen", "Period Start: 2026-02-10",
        "Period End: 2026-08-18", "Closing Balance: 5100.00", "Currency: GBP",
        "Statement number: 08/2026", "Account type: Everyday Current Account",
        "2026-08-18 | CARD PAYMENT | GROCERIES | -82.40 | 5100.00",
        "2026-08-15 | CLIENT PAYMENT | DESIGN RETAINER | +1850.00 | 5182.40",
        "2026-08-09 | BANK TRANSFER | RENT | -920.00 | 3332.40",
    ])
    rows.append(attachment("bank_statements", filename, "application/pdf", fields))

    filename = "travel_itinerary_needs_follow_up_mei_ling_chen.pdf"
    fields = {
        "outbound_date": "2026-10-05", "return_date": "2027-01-03",
        "passenger_name": "Mei Ling Chen",
    }
    write_text_pdf(pack_dir / filename, "RETURN FLIGHT ITINERARY", [
        "Booking Reference: SYNTH-MLC-2601", "Passenger Name: Mei Ling Chen",
        "Outbound Date: 2026-10-05", "Return Date: 2027-01-03",
        "OUTBOUND | ZX201 | Shanghai PVG to London LHR",
        "RETURN | ZX202 | London LHR to Shanghai PVG",
        "Cabin: Economy Flexible", "Status: Reservation for visa documentation",
    ])
    rows.append(attachment("travel_itinerary", filename, "application/pdf", fields))

    filename = "accommodation_proof_needs_follow_up_mei_ling_chen.docx"
    fields = {
        "address": "14 Bramhall Road, Manchester M20 3QT", "host_name": "Hui Chen",
        "stay_start": "2026-10-05", "stay_end": "2027-01-03",
    }
    write_docx(pack_dir / filename, "Accommodation confirmation", [
        "To: Entry Clearance Officer", "Guest Name: Mei Ling Chen",
        "Address: 14 Bramhall Road, Manchester M20 3QT", "Host Name: Hui Chen",
        "Stay Start: 2026-10-05", "Stay End: 2027-01-03",
        "Hui Chen confirms that Mei Ling Chen may use the spare bedroom throughout the visit.",
        "No accommodation charge will be made.",
    ])
    rows.append(attachment("accommodation_proof", filename, DOCX_TYPE, fields))

    filename = "sponsor_invitation_letter_needs_follow_up_mei_ling_chen.docx"
    fields = {
        "sponsor_name": "Hui Chen",
        "sponsor_address": "14 Bramhall Road, Manchester M20 3QT",
        "relationship": "sister", "stay_start": "2026-10-05",
        "stay_end": "2027-01-03", "funding_offered": "accommodation only",
    }
    write_docx(pack_dir / filename, "Invitation letter", [
        "To: Entry Clearance Officer", "Applicant: Mei Ling Chen",
        "Sponsor Name: Hui Chen",
        "Sponsor Address: 14 Bramhall Road, Manchester M20 3QT",
        "Relationship: sister", "Stay Start: 2026-10-05", "Stay End: 2027-01-03",
        "Funding Offered: accommodation only",
        "I invite my sister Mei Ling Chen to visit me in Manchester.",
        "She will pay her travel and living costs; I will provide accommodation.",
    ])
    rows.append(attachment("sponsor_invitation_letter", filename, DOCX_TYPE, fields))

    filename = "sponsor_status_proof_needs_follow_up_hui_chen.pdf"
    fields = {"sponsor_name": "Hui Chen", "status_type": "Indefinite Leave to Remain"}
    write_text_pdf(pack_dir / filename, "UK IMMIGRATION STATUS EVIDENCE", [
        "Sponsor Name: Hui Chen", "Status Type: Indefinite Leave to Remain",
        "Document reference: SYNTH-ILR-8842", "Record status: current",
    ])
    rows.append(attachment("sponsor_status_proof", filename, "application/pdf", fields))

    filename = "self_employment_evidence_needs_follow_up_mei_ling_chen.pdf"
    fields = {
        "business_name": "Chen Design Studio", "registration_id": "91310115MA1K3",
        "tax_year": "2025", "declared_income": "38000",
        "business_statement_period_start": "2026-02-10",
        "business_statement_period_end": "2026-08-18",
    }
    write_text_pdf(pack_dir / filename, "SELF-EMPLOYMENT EVIDENCE BUNDLE", [
        "Business Name: Chen Design Studio", "Registration ID: 91310115MA1K3",
        "Tax Year: 2025", "Declared Income: 38000",
        "Business Statement Period Start: 2026-02-10",
        "Business Statement Period End: 2026-08-18",
        "Business status: active", "Registered activity: product and graphic design",
        "Supporting pages: registration, tax filing, business account summary",
    ])
    rows.append(attachment("self_employment_evidence", filename, "application/pdf", fields))

    filename = "home_ties_evidence_needs_follow_up_mei_ling_chen.pdf"
    fields = {"tie_types": "apartment mortgage; elderly mother as dependant"}
    write_text_pdf(pack_dir / filename, "HOME-COUNTRY TIES SUMMARY", [
        "Applicant Name: Mei Ling Chen",
        "Tie Types: apartment mortgage; elderly mother as dependant",
        "Property city: Shanghai", "Mortgage status: active",
        "Dependant relationship: mother", "Supporting references: SYNTH-MTG-7712; SYNTH-DEP-4481",
    ])
    rows.append(attachment("home_ties_evidence", filename, "application/pdf", fields))

    write_readme(
        pack_dir,
        "Whole-case demo: adviser follow-up required",
        "A 90-day visit to a settled sister, self-employment, and an evidenced "
        "GBP 5,100 balance against a declared GBP 4,200 trip cost. Every individual "
        "document is readable and consistent; the questions arise only when the "
        "case is considered as a whole.",
        subject,
        body,
        "All eight required documents should pass document QC. The deterministic "
        "whole-case fallback should produce **3 adviser follow-up questions**, in "
        "the duration, financial-resources and home-country-commitments dimensions. "
        "They enter human review and are not automatically emailed to the client.",
    )

    follow_up_filename = "visit_and_home_arrangements_mei_ling_chen.pdf"
    adviser_question = (
        "Please explain why your visit needs to last from 5 October 2026 to "
        "3 January 2027, and how Chen Design Studio and your mother's care will "
        "be managed while you are away. Please provide any documents already "
        "available that support these arrangements."
    )
    client_reply = (
        "Dear Adviser,\n\n"
        "Please find attached my Visit and Home Arrangements Statement. It "
        "explains the purpose and timing of my 90-day visit, how Chen Design "
        "Studio will continue operating in Shanghai, how my mother will be cared "
        "for, and when I will resume in-person work after returning to China.\n\n"
        "Kind regards,\nMei Ling Chen"
    )
    readme_path = pack_dir / "README.md"
    readme_path.write_text(
        readme_path.read_text(encoding="utf-8") + """

## Human-review continuation

The PDF below is **not part of the initial eight-document submission**. Use it
only after the case has entered human review.

### Adviser follow-up question

```text
{question}
```

### Client reply

```text
{reply}
```

Attach `{filename}` to that reply. The reply text should appear under Human
Review Client Replies and the PDF under Human Review Files. Whole-case analysis
must not run again automatically; the adviser decides whether to ask again or
include the new file in the final package.
""".format(question=adviser_question, reply=client_reply,
           filename=follow_up_filename),
        encoding="utf-8",
    )
    write_text_pdf(
        pack_dir / follow_up_filename,
        "VISIT AND HOME ARRANGEMENTS STATEMENT",
        [
            "Applicant Name: Mei Ling Chen",
            "Document Date: 2026-08-20",
            "Trip Dates: 2026-10-05 to 2027-01-03",
            "Visit Purpose: Extended family visit with sister Hui Chen",
            "Planned Duration: 90 days",
            "Reason: First extended visit with my sister in three years",
            "October: Family time and local visits in Manchester",
            "November: Family time and day trips in North West England",
            "December: Christmas and New Year with my sister",
            "Business: Chen Design Studio will remain active in Shanghai",
            "Operations: Project coordinator Lin Zhao will manage daily client enquiries",
            "UK Activity: I will not work or provide services while in the UK",
            "Mother Care: Cousin Wei Chen will provide daily support in Shanghai",
            "Return Plan: Resume in-person client work in Shanghai on 2027-01-06",
        ],
    )
    return {
        "id": "needs_follow_up", "label": "Adviser follow-up required",
        "folder": "needs-follow-up", "zip": "needs-follow-up.zip",
        "intake_email": {"subject": subject, "body": body},
        "slots": slots, "attachments": rows,
        "expected_document_qc": "all_required_documents_pass",
        "expected_deterministic_analysis": {
            "candidate_source": "deterministic_fallback",
            "observation_count": 3, "follow_up_count": 3,
            "dimensions": [
                "purpose_duration_and_activities",
                "financial_resources_and_trip_cost",
                "personal_circumstances_and_will_leave",
            ],
        },
        "human_review_continuation": {
            "adviser_question": adviser_question,
            "client_reply": client_reply,
            "attachment": follow_up_filename,
            "expected_handling": "human_review_only_no_automatic_reanalysis",
        },
    }


def generate():
    if OUT.exists():
        for name in ("no-issue", "needs-follow-up"):
            path = OUT / name
            if path.exists():
                shutil.rmtree(path)
        for name in ("no-issue.zip", "needs-follow-up.zip"):
            path = OUT / name
            if path.exists():
                path.unlink()
    OUT.mkdir(parents=True, exist_ok=True)

    packs = [generate_no_issue(), generate_needs_follow_up()]
    manifest = {
        "fixture_version": "1.0.0",
        "synthetic_data_only": True,
        "route_id": "visitor_family_visit",
        "application_date": "2026-08-23",
        "purpose": (
            "Two end-to-end fixture packs that isolate document QC from whole-case "
            "analysis. They are deterministic offline fixtures, not visa advice."
        ),
        "packs": packs,
    }
    (OUT / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    for pack in packs:
        write_deterministic_zip(OUT / pack["folder"], OUT / pack["zip"])


if __name__ == "__main__":
    generate()
