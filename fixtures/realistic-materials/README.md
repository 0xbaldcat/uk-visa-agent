# Realistic synthetic material fixtures

This directory contains client-style attachments for the email/OCR walkthrough.
All identities, document numbers, balances, addresses and references are
fictional. The documents are test artifacts and must not be used as real
immigration evidence.

The suite covers the eight evidence items required for the demo applicant. Each
item has a passing example and a deliberately failing example. Formats include
native-text PDF, scanned-image PDF, DOCX and PNG. `cross-document/` adds a paired
sponsor-name mismatch test.

Scanned PDFs and images have same-path `.ocr.txt` files. These are deterministic
stand-ins for Baidu OCR in the offline demo; once the Baidu adapter is configured,
the original binary files can be sent to it and the sidecars ignored.

`manifest.yaml` records expected extracted fields and expected blocking failure
kinds. Regenerate the suite with:

```bash
python3 scripts/generate_realistic_fixtures.py
```

Verify all binaries against the current extraction and validation pipeline with:

```bash
python3 -m unittest discover -s tests
```
