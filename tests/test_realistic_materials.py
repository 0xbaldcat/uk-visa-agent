"""Contract tests for the checked-in PDF/DOCX/image fixture suite."""
from datetime import date
import os
import sys
import unittest

import yaml


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import checklist
import document_extract
import llm
import validate


FIXTURE_ROOT = os.path.join(ROOT, "fixtures", "realistic-materials")
TODAY = date(2026, 8, 22)
SLOTS = {
    "applicant_name": "Mei Ling Chen",
    "nationality": "Chinese",
    "trip_start": "2026-10-05",
    "trip_end": "2027-01-03",
    "visit_purpose": "family_visit",
    "has_uk_settled_relative": True,
    "employment_status": "self_employed",
    "third_party_funding": False,
    "prior_uk_refusal": False,
    "estimated_trip_cost_gbp": 4200.0,
}


class TestRealisticMaterialFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURE_ROOT, "manifest.yaml"), encoding="utf-8") as fh:
            cls.manifest = yaml.safe_load(fh)
        cls.route = checklist.load_route("visitor_family_visit")

    def test_suite_has_pass_and_fail_for_all_eight_required_items(self):
        required = set(ev["id"] for ev in self.route.required_evidence(SLOTS))
        normal = [row for row in self.manifest["fixtures"] if not row.get("pair")]
        passing = set(row["evidence_id"] for row in normal if row["case"] == "pass")
        failing = set(row["evidence_id"] for row in normal if row["case"] == "fail")
        self.assertEqual(len(required), 8)
        self.assertEqual(passing, required)
        self.assertEqual(failing, required)

    def test_every_fixture_extracts_and_validates_as_manifested(self):
        pair_fields = {}
        for row in self.manifest["fixtures"]:
            if row.get("pair"):
                pair_fields[(row["pair"], row["evidence_id"])] = row["expected_fields"]

        for row in self.manifest["fixtures"]:
            with self.subTest(filename=row["filename"]):
                path = os.path.join(FIXTURE_ROOT, row["filename"])
                self.assertTrue(os.path.isfile(path))
                evidence = self.route.evidence(row["evidence_id"])
                try:
                    fields = document_extract.extract_fields_from_file(
                        path, evidence.get("extract", []))
                except llm.ModelRefusal:
                    self.assertEqual(row["expected_fields"], {})
                    self.assertEqual(row["expected_blocking_failures"], ["unreadable"])
                    continue

                self.assertEqual(fields, row["expected_fields"])
                checks = []
                for configured in evidence.get("checks", []):
                    resolved = dict(configured)
                    if resolved.get("kind") == "cross_document_consistency" and row.get("pair"):
                        resolved["_other_fields"] = pair_fields.get(
                            (row["pair"], resolved["other_evidence"]), {})
                    checks.append(resolved)
                failures = validate.run_checks(checks, fields, SLOTS, today=TODAY)
                blocking = [failure.check_kind for failure in validate.blocking(failures)]
                self.assertEqual(blocking, row["expected_blocking_failures"])

    def test_ocr_fixtures_have_sidecars(self):
        for row in self.manifest["fixtures"]:
            sidecar = row.get("ocr_sidecar")
            if sidecar:
                self.assertTrue(os.path.isfile(os.path.join(FIXTURE_ROOT, sidecar)))


if __name__ == "__main__":
    unittest.main()
