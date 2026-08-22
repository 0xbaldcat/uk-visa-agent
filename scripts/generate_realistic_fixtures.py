#!/usr/bin/env python3
"""Generate synthetic client-style visa evidence fixtures.

The files contain no real personal data. Text PDFs are intentionally simple so
the local extractor can read them without OCR. Scanned PDFs and PNGs include an
`.ocr.txt` sidecar that stands in for Baidu OCR during the offline demo.
"""
from pathlib import Path
import textwrap
import zipfile

import yaml
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "realistic-materials"


def pdf_escape(value):
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_text_pdf(path, title, lines):
    commands = ["BT", "/F1 16 Tf", "72 742 Td", "(%s) Tj" % pdf_escape(title),
                "/F1 11 Tf", "0 -34 Td"]
    for line in lines:
        commands.extend(["(%s) Tj" % pdf_escape(line), "0 -20 Td"])
    commands.append("ET")
    stream = "\n".join(commands).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
         b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
    ]
    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(("%d 0 obj\n" % index).encode("ascii"))
        data.extend(obj)
        data.extend(b"\nendobj\n")
    xref = len(data)
    data.extend(("xref\n0 %d\n" % (len(objects) + 1)).encode("ascii"))
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(("%010d 00000 n \n" % offset).encode("ascii"))
    data.extend(("trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                 % (len(objects) + 1, xref)).encode("ascii"))
    path.write_bytes(data)


def xml_escape(value):
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def write_docx(path, title, paragraphs):
    body = []
    for index, paragraph in enumerate([title] + paragraphs):
        style = '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr>' if index == 0 else ""
        body.append('<w:p><w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>'
                    % (style, xml_escape(paragraph)))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>%s<w:sectPr/></w:body></w:document>' % "".join(body))
    content_types = ('<?xml version="1.0" encoding="UTF-8"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" '
                     'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)


def font(size):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def render_scan(path, title, lines, image_format):
    image = Image.new("RGB", (1240, 1754), "#f8f6ef")
    draw = ImageDraw.Draw(image)
    draw.rectangle((65, 65, 1175, 1689), outline="#7b8794", width=3)
    draw.text((105, 110), title, font=font(42), fill="#172033")
    draw.line((105, 175, 1135, 175), fill="#9aa4b2", width=2)
    y = 225
    for line in lines:
        wrapped = textwrap.wrap(line, width=58) or [""]
        for part in wrapped:
            draw.text((115, y), part, font=font(28), fill="#263241")
            y += 43
        y += 16
    draw.text((105, 1600), "SYNTHETIC TEST DOCUMENT — NOT VALID FOR TRAVEL",
              font=font(22), fill="#9a3c3c")
    if image_format == "PDF":
        image.save(path, "PDF", resolution=150.0)
    else:
        image.save(path, image_format)


def add_ocr_sidecar(path, lines):
    Path(str(path) + ".ocr.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def entry(evidence_id, relative_path, content_type, fields, failures,
          case="pass", note=None, sidecar=False, pair=None):
    result = {
        "case": case,
        "evidence_id": evidence_id,
        "filename": relative_path,
        "content_type": content_type,
        "expected_fields": fields,
        "expected_blocking_failures": failures,
    }
    if note:
        result["scenario"] = note
    if sidecar:
        result["ocr_sidecar"] = relative_path + ".ocr.txt"
    if pair:
        result["pair"] = pair
    return result


def generate():
    for folder in (OUT / "pass", OUT / "fail", OUT / "cross-document"):
        folder.mkdir(parents=True, exist_ok=True)

    fixtures = []

    # 1. Passport: scanned PDF, OCR sidecar.
    lines = ["Holder Name: Mei Ling Chen", "Passport Number: EK1234567",
             "Nationality: Chinese", "Expiry Date: 2029-04-30",
             "Prior Compliant Travel: yes"]
    path = OUT / "pass" / "passport-pass-scanned.pdf"
    render_scan(path, "PASSPORT BIOGRAPHIC PAGE", lines, "PDF")
    add_ocr_sidecar(path, lines)
    fixtures.append(entry("passport", "pass/" + path.name, "application/pdf",
                          {"holder_name": "Mei Ling Chen", "passport_number": "EK1234567",
                           "nationality": "Chinese", "expiry_date": "2029-04-30",
                           "prior_compliant_travel": True}, [], sidecar=True,
                          note="Readable scanned passport with validity beyond the trip."))
    lines = ["Holder Name: Mei Ling Chen", "Passport Number: EK7654321",
             "Nationality: Chinese", "Expiry Date: 2026-12-01",
             "Prior Compliant Travel: yes"]
    path = OUT / "fail" / "passport-fail-expired-scanned.pdf"
    render_scan(path, "PASSPORT BIOGRAPHIC PAGE", lines, "PDF")
    add_ocr_sidecar(path, lines)
    fixtures.append(entry("passport", "fail/" + path.name, "application/pdf",
                          {"holder_name": "Mei Ling Chen", "passport_number": "EK7654321",
                           "nationality": "Chinese", "expiry_date": "2026-12-01",
                           "prior_compliant_travel": True}, ["date_after"], case="fail",
                          sidecar=True, note="Passport expires before the planned return date."))

    # 2. Bank statements: native text PDF.
    lines = ["Account Holder Name: Mei Ling Chen", "Period Start: 2026-02-10",
             "Period End: 2026-08-18", "Closing Balance: 5100.00", "Currency: GBP",
             "Recent activity: client payments and ordinary living expenses"]
    path = OUT / "pass" / "bank_statements-pass.pdf"
    write_text_pdf(path, "PERSONAL CURRENT ACCOUNT STATEMENT", lines)
    fixtures.append(entry("bank_statements", "pass/" + path.name, "application/pdf",
                          {"account_holder_name": "Mei Ling Chen", "period_start": "2026-02-10",
                           "period_end": "2026-08-18", "closing_balance": "5100.00",
                           "currency": "GBP"}, [], note="Recent statement covering over 180 days."))
    lines[0] = "Account Holder Name: Wei Zhang"
    path = OUT / "fail" / "bank_statements-fail-wrong-name.pdf"
    write_text_pdf(path, "PERSONAL CURRENT ACCOUNT STATEMENT", lines)
    fixtures.append(entry("bank_statements", "fail/" + path.name, "application/pdf",
                          {"account_holder_name": "Wei Zhang", "period_start": "2026-02-10",
                           "period_end": "2026-08-18", "closing_balance": "5100.00",
                           "currency": "GBP"}, ["name_matches"], case="fail",
                          note="Statement belongs to a different person."))

    # 3. Itinerary: native text PDF.
    lines = ["Booking Reference: SYNTH-UK-2601", "Passenger Name: Mei Ling Chen",
             "Outbound Date: 2026-10-05", "Return Date: 2027-01-03",
             "Route: Shanghai - London - Shanghai"]
    path = OUT / "pass" / "travel_itinerary-pass.pdf"
    write_text_pdf(path, "RETURN FLIGHT ITINERARY", lines)
    fixtures.append(entry("travel_itinerary", "pass/" + path.name, "application/pdf",
                          {"passenger_name": "Mei Ling Chen", "outbound_date": "2026-10-05",
                           "return_date": "2027-01-03"}, [], note="Return itinerary matches stated dates."))
    lines[1] = "Passenger Name: Wei Zhang"
    path = OUT / "fail" / "travel_itinerary-fail-wrong-name.pdf"
    write_text_pdf(path, "RETURN FLIGHT ITINERARY", lines)
    fixtures.append(entry("travel_itinerary", "fail/" + path.name, "application/pdf",
                          {"passenger_name": "Wei Zhang", "outbound_date": "2026-10-05",
                           "return_date": "2027-01-03"}, ["name_matches"], case="fail",
                          note="Itinerary names a different passenger."))

    # 4. Accommodation: Word documents.
    lines = ["Address: 14 Bramhall Road, Manchester M20 3QT", "Host Name: Hui Chen",
             "Stay Start: 2026-10-05", "Stay End: 2027-01-03",
             "The guest will use the spare bedroom for the full visit."]
    path = OUT / "pass" / "accommodation_proof-pass.docx"
    write_docx(path, "Accommodation confirmation", lines)
    fixtures.append(entry("accommodation_proof", "pass/" + path.name,
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                          {"address": "14 Bramhall Road, Manchester M20 3QT", "host_name": "Hui Chen",
                           "stay_start": "2026-10-05", "stay_end": "2027-01-03"}, [],
                          note="Host confirms address and stay dates."))
    lines = ["Host Name: Hui Chen", "Stay Start: 2026-10-05", "Stay End: 2027-01-03",
             "The guest will stay at my home; address omitted."]
    path = OUT / "fail" / "accommodation_proof-fail-missing-address.docx"
    write_docx(path, "Accommodation confirmation", lines)
    fixtures.append(entry("accommodation_proof", "fail/" + path.name,
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                          {"host_name": "Hui Chen", "stay_start": "2026-10-05",
                           "stay_end": "2027-01-03"}, ["field_present"], case="fail",
                          note="The document never states the accommodation address."))

    # 5. Invitation letter: Word documents.
    lines = ["Sponsor Name: Hui Chen", "Sponsor Address: 14 Bramhall Road, Manchester M20 3QT",
             "Relationship: sister", "Stay Start: 2026-10-05", "Stay End: 2027-01-03",
             "Funding Offered: accommodation only",
             "I invite my sister Mei Ling Chen to visit me in Manchester."]
    path = OUT / "pass" / "sponsor_invitation_letter-pass.docx"
    write_docx(path, "Invitation letter", lines)
    fixtures.append(entry("sponsor_invitation_letter", "pass/" + path.name,
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                          {"sponsor_name": "Hui Chen",
                           "sponsor_address": "14 Bramhall Road, Manchester M20 3QT",
                           "relationship": "sister", "stay_start": "2026-10-05",
                           "stay_end": "2027-01-03", "funding_offered": "accommodation only"}, [],
                          note="Invitation identifies host, relationship and arrangements."))
    lines = [line for line in lines if not line.startswith("Relationship:")]
    path = OUT / "fail" / "sponsor_invitation_letter-fail-missing-relationship.docx"
    write_docx(path, "Invitation letter", lines)
    fixtures.append(entry("sponsor_invitation_letter", "fail/" + path.name,
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                          {"sponsor_name": "Hui Chen",
                           "sponsor_address": "14 Bramhall Road, Manchester M20 3QT",
                           "stay_start": "2026-10-05", "stay_end": "2027-01-03",
                           "funding_offered": "accommodation only"}, ["field_present"], case="fail",
                          note="Invitation does not state the relationship."))

    # 6. Sponsor status: photographed card/image, OCR sidecar.
    lines = ["Sponsor Name: Hui Chen", "Status Type: Indefinite Leave to Remain",
             "Document reference: SYNTH-ILR-8842"]
    path = OUT / "pass" / "sponsor_status_proof-pass.png"
    render_scan(path, "UK STATUS EVIDENCE", lines, "PNG")
    add_ocr_sidecar(path, lines)
    fixtures.append(entry("sponsor_status_proof", "pass/" + path.name, "image/png",
                          {"sponsor_name": "Hui Chen", "status_type": "Indefinite Leave to Remain"},
                          [], sidecar=True, note="Photographed status evidence with readable OCR."))
    lines = ["Sponsor Name: Hui Chen", "Document reference: SYNTH-ILR-8842"]
    path = OUT / "fail" / "sponsor_status_proof-fail-missing-status.png"
    render_scan(path, "UK STATUS EVIDENCE", lines, "PNG")
    add_ocr_sidecar(path, lines)
    fixtures.append(entry("sponsor_status_proof", "fail/" + path.name, "image/png",
                          {"sponsor_name": "Hui Chen"}, ["field_present"], case="fail",
                          sidecar=True, note="Image does not expose the sponsor's status type."))

    # 7. Self-employment evidence: native text PDF bundle summary.
    lines = ["Business Name: Chen Design Studio", "Registration ID: 91310115MA1K3",
             "Tax Year: 2025", "Declared Income: 38000",
             "Business Statement Period Start: 2026-02-10",
             "Business Statement Period End: 2026-08-18",
             "Bundle: registration extract, tax return and business account summary"]
    path = OUT / "pass" / "self_employment_evidence-pass.pdf"
    write_text_pdf(path, "SELF-EMPLOYMENT EVIDENCE BUNDLE", lines)
    fixtures.append(entry("self_employment_evidence", "pass/" + path.name, "application/pdf",
                          {"business_name": "Chen Design Studio", "registration_id": "91310115MA1K3",
                           "tax_year": "2025", "declared_income": "38000",
                           "business_statement_period_start": "2026-02-10",
                           "business_statement_period_end": "2026-08-18"}, [],
                          note="Registration, tax and business statement facts are all present."))
    lines = ["Business Name: Chen Design Studio", "Registration ID: 91310115MA1K3",
             "Declared Income: 38000", "Business Statement Period Start: 2026-02-10"]
    path = OUT / "fail" / "self_employment_evidence-fail-incomplete.pdf"
    write_text_pdf(path, "SELF-EMPLOYMENT EVIDENCE BUNDLE", lines)
    fixtures.append(entry("self_employment_evidence", "fail/" + path.name, "application/pdf",
                          {"business_name": "Chen Design Studio", "registration_id": "91310115MA1K3",
                           "declared_income": "38000", "business_statement_period_start": "2026-02-10"},
                          ["field_present", "field_present"], case="fail",
                          note="Tax year and business statement end date are missing."))

    # 8. Home ties: scanned PDF. The negative is deliberately unreadable.
    lines = ["Property account summary", "Tie Types: apartment mortgage; elderly mother as dependant",
             "Property city: Shanghai", "Mortgage status: active"]
    path = OUT / "pass" / "home_ties_evidence-pass-scanned.pdf"
    render_scan(path, "HOME TIES EVIDENCE", lines, "PDF")
    add_ocr_sidecar(path, lines)
    fixtures.append(entry("home_ties_evidence", "pass/" + path.name, "application/pdf",
                          {"tie_types": "apartment mortgage; elderly mother as dependant"}, [],
                          sidecar=True, note="Scanned property and dependant summary exposes tie types."))
    visual_lines = ["Property account summary", "Image is cropped below the customer details",
                    "Mortgage status: active"]
    path = OUT / "fail" / "home_ties_evidence-fail-cropped-scan.pdf"
    render_scan(path, "HOME TIES EVIDENCE", visual_lines, "PDF")
    add_ocr_sidecar(path, visual_lines)
    fixtures.append(entry("home_ties_evidence", "fail/" + path.name, "application/pdf", {},
                          ["unreadable"], case="fail", sidecar=True,
                          note="Cropped scan contains none of the requested home-tie fields."))

    # Extra paired fixtures exercise cross-document consistency.
    invite_lines = ["Sponsor Name: Wei Zhang",
                    "Sponsor Address: 14 Bramhall Road, Manchester M20 3QT",
                    "Relationship: family friend", "Stay Start: 2026-10-05",
                    "Stay End: 2027-01-03", "Funding Offered: accommodation only"]
    path = OUT / "cross-document" / "sponsor_invitation_letter-fail-name-mismatch.docx"
    write_docx(path, "Invitation letter", invite_lines)
    fixtures.append(entry("sponsor_invitation_letter", "cross-document/" + path.name,
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                          {"sponsor_name": "Wei Zhang",
                           "sponsor_address": "14 Bramhall Road, Manchester M20 3QT",
                           "relationship": "family friend", "stay_start": "2026-10-05",
                           "stay_end": "2027-01-03", "funding_offered": "accommodation only"},
                          ["cross_document_consistency"], case="fail",
                          note="Invitation sponsor conflicts with the paired status document.",
                          pair="sponsor-name-mismatch"))
    lines = ["Sponsor Name: Hui Chen", "Status Type: Indefinite Leave to Remain"]
    path = OUT / "cross-document" / "sponsor_status_proof-pair-name-mismatch.png"
    render_scan(path, "UK STATUS EVIDENCE", lines, "PNG")
    add_ocr_sidecar(path, lines)
    fixtures.append(entry("sponsor_status_proof", "cross-document/" + path.name, "image/png",
                          {"sponsor_name": "Hui Chen", "status_type": "Indefinite Leave to Remain"},
                          [], sidecar=True, note="Paired status document for mismatch test.",
                          pair="sponsor-name-mismatch"))

    manifest = {
        "fixture_version": "1.0.0",
        "synthetic_data_only": True,
        "applicant": {"applicant_name": "Mei Ling Chen", "trip_start": "2026-10-05",
                      "trip_end": "2027-01-03"},
        "fixtures": fixtures,
    }
    (OUT / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")


if __name__ == "__main__":
    generate()
