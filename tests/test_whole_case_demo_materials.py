"""Contracts for the two checked-in whole-case demo material packs."""
from datetime import date
import os
import sys
import unittest
import zipfile

import yaml


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import case_analysis
import checklist
import document_extract
import email_bridge
import state
import store
import validate


FIXTURE_ROOT = os.path.join(ROOT, "fixtures", "whole-case-demo")
FIXED_ZIP_TIME = (2026, 8, 23, 0, 0, 0)


class TestWholeCaseDemoMaterials(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(FIXTURE_ROOT, "manifest.yaml"), encoding="utf-8") as fh:
            cls.manifest = yaml.safe_load(fh)
        cls.route = checklist.load_route(cls.manifest["route_id"])
        cls.application_date = date.fromisoformat(cls.manifest["application_date"])

    def test_manifest_has_one_zero_question_and_one_follow_up_pack(self):
        packs = dict((pack["id"], pack) for pack in self.manifest["packs"])
        self.assertEqual(set(packs), {"no_issue", "needs_follow_up"})
        self.assertEqual(
            packs["no_issue"]["expected_deterministic_analysis"]["follow_up_count"], 0)
        self.assertEqual(
            packs["needs_follow_up"]["expected_deterministic_analysis"]["follow_up_count"], 3)

    def test_each_pack_exactly_covers_its_required_checklist_branch(self):
        for pack in self.manifest["packs"]:
            with self.subTest(pack=pack["id"]):
                expected = [row["id"] for row in self.route.required_evidence(pack["slots"])]
                supplied = [row["evidence_id"] for row in pack["attachments"]]
                self.assertEqual(supplied, expected)

    def test_every_filename_maps_to_its_evidence_id(self):
        for pack in self.manifest["packs"]:
            for row in pack["attachments"]:
                with self.subTest(pack=pack["id"], filename=row["filename"]):
                    path = os.path.join(FIXTURE_ROOT, pack["folder"], row["filename"])
                    self.assertTrue(os.path.isfile(path))
                    self.assertEqual(
                        email_bridge.infer_evidence_id(row["filename"]), row["evidence_id"])

    def test_all_documents_extract_and_pass_blocking_qc(self):
        for pack in self.manifest["packs"]:
            fields_by_evidence = self._extract_pack(pack)
            for row in pack["attachments"]:
                evidence = self.route.evidence(row["evidence_id"])
                checks = []
                for configured in evidence.get("checks", []):
                    resolved = dict(configured)
                    if resolved.get("kind") == "cross_document_consistency":
                        resolved["_other_fields"] = fields_by_evidence.get(
                            resolved["other_evidence"], {})
                    checks.append(resolved)
                failures = validate.run_checks(
                    checks, fields_by_evidence[row["evidence_id"]], pack["slots"],
                    today=self.application_date)
                blocking = [failure.check_kind for failure in validate.blocking(failures)]
                with self.subTest(pack=pack["id"], evidence=row["evidence_id"]):
                    self.assertEqual(blocking, row["expected_blocking_failures"])

    def test_whole_case_fallback_reaches_declared_result(self):
        for pack in self.manifest["packs"]:
            fields_by_evidence = self._extract_pack(pack)
            evidence = dict((evidence_id, {"fields": fields, "failures": []})
                            for evidence_id, fields in fields_by_evidence.items())
            case = store.Case(
                "fixture-%s" % pack["id"], self.route.route_id,
                state.Stage.COLLECTING.value, dict(pack["slots"]), evidence)
            result = case_analysis.analyse(
                self.route, case, model=None, application_date=self.application_date)
            expected = pack["expected_deterministic_analysis"]
            dimensions = [row["dimension_id"] for row in result["follow_up_questions"]]
            with self.subTest(pack=pack["id"]):
                self.assertTrue(case.is_complete(self.route))
                self.assertEqual(result["candidate_source"], expected["candidate_source"])
                self.assertEqual(len(result["observations"]), expected["observation_count"])
                self.assertEqual(len(result["follow_up_questions"]), expected["follow_up_count"])
                self.assertEqual(dimensions, expected["dimensions"])
                self.assertEqual(result["rejected"], [])

    def test_zip_archives_contain_only_readme_and_declared_attachments(self):
        for pack in self.manifest["packs"]:
            zip_path = os.path.join(FIXTURE_ROOT, pack["zip"])
            self.assertTrue(os.path.isfile(zip_path))
            expected_names = sorted(
                ["README.md"] + [row["filename"] for row in pack["attachments"]])
            continuation = pack.get("human_review_continuation") or {}
            if continuation.get("attachment"):
                expected_names.append(continuation["attachment"])
                expected_names.sort()
            with zipfile.ZipFile(zip_path) as archive:
                names = sorted(archive.namelist())
                timestamps = set(info.date_time for info in archive.infolist())
            with self.subTest(pack=pack["id"]):
                self.assertEqual(names, expected_names)
                self.assertEqual(timestamps, {FIXED_ZIP_TIME})

    def test_follow_up_pack_documents_human_review_continuation(self):
        pack = next(row for row in self.manifest["packs"]
                    if row["id"] == "needs_follow_up")
        continuation = pack["human_review_continuation"]
        self.assertEqual(
            continuation["expected_handling"],
            "human_review_only_no_automatic_reanalysis")
        attachment_path = os.path.join(
            FIXTURE_ROOT, pack["folder"], continuation["attachment"])
        self.assertTrue(os.path.isfile(attachment_path))
        readme_path = os.path.join(FIXTURE_ROOT, pack["folder"], "README.md")
        with open(readme_path, encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn(continuation["adviser_question"], readme)
        self.assertIn(continuation["client_reply"], readme)
        self.assertIn(continuation["attachment"], readme)

    def _extract_pack(self, pack):
        fields_by_evidence = {}
        for row in pack["attachments"]:
            evidence = self.route.evidence(row["evidence_id"])
            path = os.path.join(FIXTURE_ROOT, pack["folder"], row["filename"])
            fields = document_extract.extract_fields_from_file(
                path, evidence.get("extract", []))
            self.assertEqual(fields, row["expected_fields"])
            fields_by_evidence[row["evidence_id"]] = fields
        return fields_by_evidence


if __name__ == "__main__":
    unittest.main()
