import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
from tools import LocalTools, finalize_plan, validate_plan, similarity  # noqa: E402


class ToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        subprocess.run([sys.executable, "-I", str(AGENT / "data" / "generate.py"),
                        "--out", str(t / "out"), "--truth", str(t / "truth")], check=True, capture_output=True)
        cls.tools = LocalTools(str(t / "out"))
        cls.truth = json.loads((t / "truth" / "truth.json").read_text())
        with (t / "truth" / "existing_accounts.csv").open() as f:
            cls.existing = [{"external_id": r["External_Id__c"], "name": r["Name"]} for r in csv.DictReader(f)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_fuzzy_finds_seeded_duplicates(self):
        res = self.tools.fuzzy_match(self.existing, 0.85)
        found = {m["acq_account_id"]: m["existing_external_id"] for m in res["matches"]}
        want = {d["acq_account_id"]: d["matches_external_id"] for d in self.truth["near_duplicates"]}
        hits = sum(1 for k, v in want.items() if found.get(k) == v)
        false_pos = [k for k in found if k not in want]
        print(f"\nfuzzy: recall {hits}/{len(want)}, false positives {len(false_pos)}")
        self.assertGreaterEqual(hits / len(want), 0.9)
        self.assertLessEqual(len(false_pos), 2)

    def test_profile_quality_matches_seeded_problems_exactly(self):
        res = self.tools.profile_quality()
        got = {(f["file"], f["kind"].split(" (")[0]): f for f in res["findings"]}
        bad = self.truth["bad_rows"]

        def seeded(section, kind):
            return {r.get("acq_account_id") or r.get("acq_contact_id") or r.get("acq_case_id")
                    for r in bad[section] if r["kind"] == kind}

        expect = [("accounts.csv", "blank company_name", seeded("accounts", "missing_name")),
                  ("accounts.csv", "duplicate acq_account_id", seeded("accounts", "duplicate_source_id")),
                  ("accounts.csv", "annual_revenue is not a positive number", seeded("accounts", "bad_revenue")),
                  ("contacts.csv", "blank email", seeded("contacts", "missing_email")),
                  ("contacts.csv", "orphaned acq_account_id", seeded("contacts", "orphaned_account")),
                  ("cases.csv", "unrecognized priority", seeded("cases", "garbage_priority")),
                  ("cases.csv", "orphaned acq_account_id", seeded("cases", "orphaned_account")),
                  ("cases.csv", "blank subject", seeded("cases", "missing_subject"))]
        for file, kind, ids in expect:
            f = got[(file, kind)]
            self.assertEqual(f["count"], len(ids), f"{file} {kind}")
            if len(ids) <= 10:  # examples are capped at 10
                self.assertEqual(set(f["example_ids"]), ids, f"{file} {kind}")
        # nothing unexpected beyond the informational blank-tier finding
        self.assertEqual({k for k in got} - {(f, k) for f, k, _ in expect}, {("accounts.csv", "blank tier")})

    def test_similarity_basics(self):
        self.assertGreater(similarity("Acme Corp", "ACME CORPORATION, INC."), 0.95)
        self.assertLess(similarity("Acme Corp", "Zenith Labs"), 0.5)
        self.assertEqual(similarity("", "Acme"), 0.0)

    def test_file_allowlist(self):
        for bad in ("../truth/truth.json", "/etc/passwd", "existing_accounts.csv", "accounts.csv/../x"):
            with self.assertRaises(ValueError):
                self.tools.read_csv_sample(bad)

    def test_sample_and_column_values(self):
        s = self.tools.read_csv_sample("cases.csv", 5)
        self.assertEqual(s["total_rows"], 800)
        self.assertEqual(len(s["sample"]), 5)
        v = self.tools.column_values("accounts.csv", "tier")
        self.assertGreater(v["distinct"], 5)
        with self.assertRaises(ValueError):
            self.tools.column_values("accounts.csv", "nope")

    def test_validate_and_finalize(self):
        good = {"mappings": [{"source": "a.b", "target": "Account.Name", "transform": "none", "confidence": 0.95, "reasoning": "r"},
                             {"source": "a.c", "target": None, "transform": "none", "confidence": 0.9, "reasoning": "no field"},
                             {"source": "a.d", "target": "Account.Industry", "transform": "none", "confidence": 0.5, "reasoning": "vague"}],
                "dedupe_matches": [{"acq_account_id": "X", "existing_external_id": "Y", "confidence": 0.6, "reasoning": "r"}],
                "proposed_new_fields": [], "data_quality_issues": [{"file": "cases.csv", "row_id": "K1", "kind": "k", "action": "needs_human"}],
                "notes": "n"}
        self.assertEqual(validate_plan(good), [])
        out = finalize_plan(good)
        reasons = sorted(n["why"] for n in out["needs_human"])
        self.assertEqual(reasons, ["k", "low confidence", "no target field", "uncertain match"])
        bad = {**good, "mappings": [{**good["mappings"][0], "confidence": 1.5}]}
        self.assertTrue(validate_plan(bad))
        self.assertTrue(validate_plan({"mappings": []}))


if __name__ == "__main__":
    unittest.main()
