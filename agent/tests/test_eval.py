import copy
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
from eval_plan import evaluate, load_golden  # noqa: E402

FIX = AGENT / "evals" / "fixtures"


class EvalGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        subprocess.run([sys.executable, "-I", str(AGENT / "data" / "generate.py"),
                        "--out", str(t / "out"), "--truth", str(t / "truth")], check=True, capture_output=True)
        cls.out, cls.truth_path = t / "out", t / "truth" / "truth.json"
        cls.truth = json.loads(cls.truth_path.read_text())
        cls.golden = load_golden()
        cls.baseline = json.loads((FIX / "baseline_plan.json").read_text())
        cls.weak = json.loads((FIX / "weak_plan.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def mutate(self):
        return copy.deepcopy(self.baseline)

    def run_eval(self, plan):
        return evaluate(plan, self.golden, self.truth)

    # --- the golden file itself
    def test_golden_covers_every_export_column_exactly(self):
        want = set()
        for f in ("accounts", "contacts", "cases"):
            with (self.out / f"{f}.csv").open() as fh:
                want |= {f"{f}.{c}" for c in next(csv.reader(fh))}
        self.assertEqual({g["source"] for g in self.golden["mappings"]}, want)

    # --- real plans
    def test_baseline_plan_passes(self):
        r = self.run_eval(self.baseline)
        self.assertEqual(r["failures"], [])
        self.assertEqual(r["mappings"]["correct"], r["mappings"]["total"])

    def test_weak_first_run_fails_on_data_quality_and_on_the_dropped_tier(self):
        r = self.run_eval(self.weak)
        dq = [f for f in r["failures"] if f.startswith("data quality")]
        self.assertEqual(len(dq), 8)
        # it predates Customer_Tier__c, so it maps tier to no field: now a confident wrong mapping that silently drops data
        other = [f for f in r["failures"] if not f.startswith("data quality")]
        self.assertEqual(len(other), 1)
        self.assertIn("accounts.tier", other[0])

    # --- mapping gates
    def test_confident_wrong_mapping_fails_even_when_accuracy_is_high(self):
        p = self.mutate()
        m = next(x for x in p["mappings"] if x["source"] == "cases.priority")
        m["target"], m["confidence"] = "Case.Priority", 0.95
        r = self.run_eval(p)
        self.assertGreaterEqual(r["mappings"]["accuracy"], 0.9)  # 22/23
        self.assertTrue(any("confident wrong" in f for f in r["failures"]))

    def test_wrong_but_flagged_mapping_is_tolerated(self):
        p = self.mutate()
        m = next(x for x in p["mappings"] if x["source"] == "cases.priority")
        m["target"], m["confidence"] = "Case.Priority", 0.5
        self.assertEqual(self.run_eval(p)["failures"], [])

    def test_accuracy_gate_trips_below_ninety_percent(self):
        p = self.mutate()
        for src in ("accounts.website", "contacts.phone", "contacts.title"):
            m = next(x for x in p["mappings"] if x["source"] == src)
            m["target"], m["confidence"] = "Account.Description", 0.3  # wrong, flagged
        r = self.run_eval(p)  # 20/23 = 87%
        self.assertTrue(any("mapping accuracy" in f for f in r["failures"]))

    def test_missing_mapping_counts_as_wrong(self):
        p = self.mutate()
        p["mappings"] = [m for m in p["mappings"] if m["source"] != "contacts.email"]
        r = self.run_eval(p)
        self.assertEqual(r["mappings"]["correct"], r["mappings"]["total"] - 1)

    def test_mapping_tier_to_an_existing_field_is_wrong(self):
        p = self.mutate()
        m = next(x for x in p["mappings"] if x["source"] == "accounts.tier")
        m["target"], m["confidence"] = "Account.Rating", 0.9
        self.assertTrue(any("accounts.tier" in f for f in self.run_eval(p)["failures"]))

    # --- dedupe gates
    def test_bogus_duplicate_claims_trip_precision(self):
        p = self.mutate()
        for i in range(5):
            p["dedupe_matches"].append({"acq_account_id": f"ACQ-A-0049{i}", "existing_external_id": "CORE-00001",
                                        "confidence": 0.9, "reasoning": "x"})
        self.assertTrue(any("dedupe precision" in f for f in self.run_eval(p)["failures"]))

    def test_dropping_duplicates_trips_recall(self):
        p = self.mutate()
        p["dedupe_matches"] = p["dedupe_matches"][:19]  # 19/25 = 76%
        self.assertTrue(any("dedupe recall" in f for f in self.run_eval(p)["failures"]))

    # --- data quality gate
    def test_off_by_one_count_fails(self):
        p = self.mutate()
        q = next(x for x in p["data_quality_issues"] if x["file"] == "contacts.csv" and "blank email" in x["kind"])
        q["count"] += 1
        r = self.run_eval(p)
        self.assertEqual(len(r["failures"]), 1)
        self.assertIn("blank email", r["failures"][0])

    # --- CLI
    def cli(self, *args):
        return subprocess.run([sys.executable, "-I", str(AGENT / "eval_plan.py"), *args], capture_output=True, text=True)

    def test_cli_exit_codes(self):
        ok = self.cli(str(FIX / "baseline_plan.json"), "--truth", str(self.truth_path))
        self.assertEqual(ok.returncode, 0, ok.stdout)
        self.assertIn("RESULT: PASS", ok.stdout)
        bad = self.cli(str(FIX / "weak_plan.json"), "--truth", str(self.truth_path))
        self.assertEqual(bad.returncode, 1)
        self.assertIn("RESULT: FAIL", bad.stdout)
        # without a truth file it refuses instead of silently skipping the dedupe and data-quality gates
        missing = self.cli(str(FIX / "baseline_plan.json"), "--truth", "/nonexistent/truth.json")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("generate.py", missing.stderr)
        self.assertEqual(self.cli(str(FIX / "baseline_plan.json"), "--mappings-only").returncode, 0)
        mo = self.cli(str(FIX / "weak_plan.json"), "--mappings-only")
        self.assertEqual(mo.returncode, 1)  # its tier mapping predates Customer_Tier__c
        self.assertIn("accounts.tier", mo.stdout)


if __name__ == "__main__":
    unittest.main()
