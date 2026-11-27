import copy
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
from load_rules import PlanError, read_exports, resolve_mapping, transform, write_load_files  # noqa: E402

FIX = AGENT / "evals" / "fixtures" / "baseline_plan.json"


class TransformTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        subprocess.run([sys.executable, "-I", str(AGENT / "data" / "generate.py"),
                        "--out", str(t / "out"), "--truth", str(t / "truth")], check=True, capture_output=True)
        cls.accounts, cls.contacts, cls.cases = read_exports(t / "out")
        cls.truth = json.loads((t / "truth" / "truth.json").read_text())
        cls.plan = json.loads(FIX.read_text())
        cls.result = transform(cls.plan, cls.accounts, cls.contacts, cls.cases)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rejected(self, file, reason_part):
        return [r for r in self.result["rejected"] if r["file"] == file and reason_part in r["reason"]]

    # ---- accounts
    def test_every_account_is_loaded_rejected_or_merged_exactly_once(self):
        r = self.result
        n_rej = sum(1 for x in r["rejected"] if x["file"] == "accounts.csv")
        self.assertEqual(len(r["rows"]["accounts"]) + n_rej + len(r["merged"]["accounts"]), 500)

    def test_account_rejections_match_the_seeded_problems(self):
        self.assertEqual(len(self.rejected("accounts.csv", "blank company name")), 5)
        ids = Counter(a["acq_account_id"] for a in self.accounts)
        shared_rows = sum(n for n in ids.values() if n > 1)
        self.assertEqual(len(self.rejected("accounts.csv", "shared")), shared_rows)
        self.assertEqual(len({i for i, n in ids.items() if n > 1}), 5)  # the 5 seeded shared ids

    def test_duplicates_are_merged_not_inserted(self):
        merged = self.result["merged"]["accounts"]
        rejected = {r["id"] for r in self.result["rejected"] if r["file"] == "accounts.csv"}
        claimed = {d["acq_account_id"] for d in self.plan["dedupe_matches"]}
        # rejection beats merging: a claimed duplicate that is also a bad row (here ACQ-A-00012, a shared id) is rejected
        self.assertEqual(set(merged), claimed - rejected)
        self.assertTrue(claimed & rejected, "fixture should exercise the overlap")
        loaded_ids = {a["External_Id__c"] for a in self.result["rows"]["accounts"]}
        self.assertFalse(set(merged) & loaded_ids)

    def test_names_are_cleaned_and_source_system_is_set(self):
        for a in self.result["rows"]["accounts"]:
            self.assertEqual(a["Name"], " ".join(a["Name"].split()))
            self.assertEqual(a["Source_System__c"], "AcquiredCo")

    def test_bad_revenue_loads_blank_with_a_warning(self):
        blanks = [a for a in self.result["rows"]["accounts"] if a["AnnualRevenue"] == ""]
        warns = [w for w in self.result["warnings"] if "annual_revenue" in w["warning"]]
        self.assertEqual(len(blanks), len(warns))
        self.assertGreater(len(blanks), 0)

    # ---- contacts and cases
    def test_children_are_accounted_for_and_never_point_at_a_rejected_parent(self):
        loaded_accounts = {a["External_Id__c"] for a in self.result["rows"]["accounts"]}
        core_targets = set(self.result["merged"]["accounts"].values())
        for f, rows in (("contacts", self.contacts), ("cases", self.cases)):
            n_rej = sum(1 for x in self.result["rejected"] if x["file"] == f + ".csv")
            self.assertEqual(len(self.result["rows"][f]) + n_rej, len(rows), f)
            for o in self.result["rows"][f]:
                self.assertIn(o["Account.External_Id__c"], loaded_accounts | core_targets, f)

    def test_orphans_and_parent_rejections_are_counted_correctly(self):
        account_ids = {a["acq_account_id"] for a in self.accounts}
        for f, rows in (("contacts.csv", self.contacts), ("cases.csv", self.cases)):
            want = sum(1 for r in rows if r["acq_account_id"] not in account_ids)
            self.assertEqual(len(self.rejected(f, "orphaned")), want, f)
        self.assertEqual(len(self.rejected("contacts.csv", "orphaned")), 15)
        self.assertEqual(len(self.rejected("cases.csv", "orphaned")), 8)
        rejected_parents = {r["id"] for r in self.result["rejected"] if r["file"] == "accounts.csv"}
        want = sum(1 for r in self.contacts if r["acq_account_id"] in rejected_parents)
        self.assertGreater(want, 0)
        self.assertEqual(len(self.rejected("contacts.csv", "parent rejected")), want)

    def test_children_of_merged_accounts_are_reparented_to_core(self):
        merged = self.result["merged"]["accounts"]
        rejected_parents = {r["id"] for r in self.result["rejected"] if r["file"] == "accounts.csv"}
        want = sum(1 for r in self.contacts if r["acq_account_id"] in merged and r["acq_account_id"] not in rejected_parents
                   and r["acq_account_id"] in {a["acq_account_id"] for a in self.accounts})
        self.assertEqual(self.result["merged"]["contacts_reparented"], want)
        core_parented = [o for o in self.result["rows"]["contacts"] if o["Account.External_Id__c"].startswith("CORE-")]
        self.assertEqual(len(core_parented), want)

    def test_blank_subjects_rejected_and_status_decision_applied(self):
        self.assertEqual(len(self.rejected("cases.csv", "blank subject")), 8)
        statuses = {o["Status"] for o in self.result["rows"]["cases"]}
        self.assertTrue(statuses <= {"New", "Working", "Escalated"}, statuses)
        pend = sum(1 for w in self.result["warnings"] if "pending_customer" in w["warning"])
        loaded_ids = {o["External_Id__c"] for o in self.result["rows"]["cases"]}
        self.assertEqual(pend, sum(1 for c in self.cases if c["status"] == "pending_customer" and c["acq_case_id"] in loaded_ids))

    def test_unrecognized_priorities_load_with_a_warning_for_the_trigger(self):
        warns = [w for w in self.result["warnings"] if "unrecognized priority" in w["warning"]]
        garbage = {x["acq_case_id"] for x in self.truth["bad_rows"]["cases"] if x["kind"] == "garbage_priority"}
        loaded = {o["External_Id__c"] for o in self.result["rows"]["cases"]}
        # a garbage-priority case whose parent was rejected never loads, so it gets no warning (here ACQ-K-00351)
        self.assertEqual({w["id"] for w in warns}, garbage & loaded)
        self.assertLess(len(garbage & loaded), len(garbage), "fixture should exercise the overlap")
        raw = {o["Source_Priority__c"] for o in self.result["rows"]["cases"]}
        self.assertIn("banana", raw)  # loaded raw; Priority is the trigger's job

    # ---- approved mapping only
    def test_tier_is_dropped_and_reported_when_the_plan_has_no_target_for_it(self):
        plan = copy.deepcopy(self.plan)
        next(m for m in plan["mappings"] if m["source"] == "accounts.tier")["target"] = None  # an old plan
        r = transform(plan, self.accounts, self.contacts, self.cases)
        self.assertNotIn("Customer_Tier__c", r["rows"]["accounts"][0])
        self.assertIn("accounts.tier", {n["source"] for n in r["not_loaded"]})

    def test_createddate_is_never_loaded_and_the_reason_is_reported(self):
        reasons = {n["source"]: n["reason"] for n in self.result["not_loaded"]}
        self.assertIn("Set Audit Fields", reasons["cases.created_date"])
        self.assertNotIn("CreatedDate", self.result["rows"]["cases"][0])

    def test_the_current_plan_loads_tier_raw(self):
        raw = {a["acq_account_id"]: a["tier"] for a in self.accounts}
        for o in self.result["rows"]["accounts"]:
            self.assertEqual(o["Customer_Tier__c"], raw[o["External_Id__c"]].strip())

    def test_tier_loads_raw_when_the_plan_maps_it(self):
        plan = copy.deepcopy(self.plan)
        next(m for m in plan["mappings"] if m["source"] == "accounts.tier")["target"] = "Account.Customer_Tier__c"
        r = transform(plan, self.accounts, self.contacts, self.cases)
        raw = {a["acq_account_id"]: a["tier"] for a in self.accounts}
        for o in r["rows"]["accounts"]:
            self.assertEqual(o["Customer_Tier__c"], raw[o["External_Id__c"]].strip())

    def test_plan_that_cannot_be_executed_is_refused(self):
        def with_target(src, target):
            p = copy.deepcopy(self.plan)
            next(m for m in p["mappings"] if m["source"] == src)["target"] = target
            return p
        for plan, why in ((with_target("accounts.tier", "Account.Rating"), "no rule"),
                          (with_target("contacts.title", "Account.Name"), "different object"),
                          (with_target("accounts.website", "Account.Name"), "more than one column"),
                          (with_target("accounts.company_name", None), "required")):
            with self.assertRaises(PlanError, msg=why):
                resolve_mapping(plan)

    # ---- determinism and file format
    def test_transform_is_deterministic(self):
        again = transform(self.plan, self.accounts, self.contacts, self.cases)
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.result, sort_keys=True))

    def test_load_files_use_lf_line_endings_and_relationship_headers(self):
        with tempfile.TemporaryDirectory() as d:
            paths = write_load_files(self.result, d)
            for p in paths.values():
                self.assertNotIn(b"\r", Path(p).read_bytes())
            self.assertIn("Account.External_Id__c", Path(paths["contacts"]).read_text().splitlines()[0])
            self.assertIn("Account.External_Id__c", Path(paths["cases"]).read_text().splitlines()[0])
            self.assertIn("External_Id__c", Path(paths["accounts"]).read_text().splitlines()[0])


if __name__ == "__main__":
    unittest.main()
