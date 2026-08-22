"""Document text extraction seam for inbound evidence files.

The PoC keeps visa rules deterministic, but real clients send files rather than
pre-structured JSON. This module converts a saved attachment path into text, then
extracts only the fields requested by the checklist. OCR providers plug in here;
the engine and validators still see a plain field dict.
"""
import os
import re
import zipfile
from html import unescape

import llm


class DocumentTextExtractor(object):
    def extract_text(self, path):
        raise NotImplementedError


class LocalDocumentTextExtractor(DocumentTextExtractor):
    """Offline extractor used by tests and demos.

    It supports text PDFs whose text is visible in the PDF content stream, DOCX
    body text, plain text files, and OCR sidecars for scanned images/PDFs. A real
    Baidu OCR adapter can replace or wrap this class without changing callers.
    """

    TEXT_EXTENSIONS = {".txt", ".text", ".md", ".csv"}
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}

    def extract_text(self, path):
        if not os.path.exists(path):
            raise llm.ModelRefusal("document file does not exist: %s" % path)
        sidecar = self._sidecar(path)
        if sidecar:
            return _read_text(sidecar)

        ext = os.path.splitext(path)[1].lower()
        if ext in self.TEXT_EXTENSIONS:
            return _read_text(path)
        if ext == ".docx":
            return _docx_text(path)
        if ext == ".pdf":
            return _pdf_visible_text(path)
        if ext in self.IMAGE_EXTENSIONS:
            raise llm.ModelRefusal("image OCR adapter not configured for %s" % path)
        raise llm.ModelRefusal("unsupported document type: %s" % ext)

    def _sidecar(self, path):
        candidates = [
            path + ".ocr.txt",
            os.path.splitext(path)[0] + ".ocr.txt",
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None


FIELD_ALIASES = {
    "holder_name": ["holder name", "passport holder", "name"],
    "passport_number": ["passport number", "passport no", "document number"],
    "expiry_date": ["expiry date", "date of expiry", "valid until"],
    "nationality": ["nationality"],
    "prior_compliant_travel": ["prior compliant travel", "travel history compliant"],
    "account_holder_name": ["account holder name", "account holder", "customer name"],
    "period_start": ["period start", "statement start", "from date"],
    "period_end": ["period end", "statement end", "to date"],
    "closing_balance": ["closing balance", "balance"],
    "currency": ["currency"],
    "outbound_date": ["outbound date", "departure date", "flight out"],
    "return_date": ["return date", "flight back"],
    "passenger_name": ["passenger name", "traveller name", "traveler name"],
    "address": ["address", "stay address", "accommodation address"],
    "host_name": ["host name"],
    "stay_start": ["stay start", "check in", "check-in"],
    "stay_end": ["stay end", "check out", "check-out"],
    "sponsor_name": ["sponsor name", "host sponsor name", "host name"],
    "sponsor_address": ["sponsor address", "host address"],
    "relationship": ["relationship"],
    "funding_offered": ["funding offered", "financial support"],
    "status_type": ["status type", "immigration status"],
    "employer_name": ["employer name", "company name"],
    "job_title": ["job title", "position"],
    "leave_start": ["leave start"],
    "leave_end": ["leave end"],
    "annual_salary": ["annual salary", "salary"],
    "business_name": ["business name", "company name"],
    "registration_id": ["registration id", "registration number", "company number"],
    "tax_year": ["tax year"],
    "declared_income": ["declared income", "taxable income"],
    "business_statement_period_start": ["business statement period start"],
    "business_statement_period_end": ["business statement period end"],
    "tie_types": ["tie types", "ties", "home ties"],
}


def extract_fields_from_file(path, wanted_fields, text_extractor=None):
    extractor = text_extractor or LocalDocumentTextExtractor()
    text = extractor.extract_text(path)
    fields = extract_fields_from_text(text, wanted_fields)
    if not fields:
        raise llm.ModelRefusal("no requested fields found in %s" % path)
    return fields


def extract_fields_from_text(text, wanted_fields):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    fields = {}
    for wanted in wanted_fields:
        value = _field_value(lines, wanted)
        if value is not None:
            fields[wanted] = _normalise_value(value)
    return fields


def _field_value(lines, field):
    labels = [field.replace("_", " ")] + FIELD_ALIASES.get(field, [])
    for line in lines:
        for label in labels:
            pattern = r"^\s*%s\s*[:：=-]\s*(.+?)\s*$" % re.escape(label)
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                return match.group(1)
    return None


def _normalise_value(value):
    value = " ".join(str(value).strip().split())
    lowered = value.lower()
    if lowered in ("true", "yes", "y"):
        return True
    if lowered in ("false", "no", "n"):
        return False
    return value


def _read_text(path):
    with open(path, "rb") as fh:
        data = fh.read()
    return data.decode("utf-8", "ignore")


def _docx_text(path):
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "ignore")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise llm.ModelRefusal("could not read DOCX text: %s" % exc)
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return unescape(xml)


def _pdf_visible_text(path):
    with open(path, "rb") as fh:
        data = fh.read()
    raw = data.decode("latin-1", "ignore")
    strings = []
    for match in re.finditer(r"\((.*?)\)\s*Tj", raw, re.DOTALL):
        strings.append(_pdf_unescape(match.group(1)))
    for match in re.finditer(r"\[(.*?)\]\s*TJ", raw, re.DOTALL):
        strings.extend(_pdf_unescape(s) for s in re.findall(r"\((.*?)\)", match.group(1), re.DOTALL))
    if strings:
        return "\n".join(strings)

    # Some simple PDFs leave text visible outside Tj operators. This fallback is
    # deliberately conservative; scanned or compressed PDFs should go to OCR.
    visible = re.sub(r"[^\x09\x0a\x0d\x20-\x7e]+", "\n", raw)
    lines = [line.strip() for line in visible.splitlines() if ":" in line]
    if lines:
        return "\n".join(lines)
    raise llm.ModelRefusal("no visible PDF text; OCR required")


def _pdf_unescape(value):
    value = value.replace(r"\\n", "\n").replace(r"\\r", "\n").replace(r"\\t", "\t")
    value = value.replace(r"\(", "(").replace(r"\)", ")")
    value = value.replace(r"\n", "\n").replace(r"\r", "\n").replace(r"\t", "\t")
    value = value.replace(r"\\", "\\")
    return value
