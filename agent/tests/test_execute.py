import contextlib
import copy
import csv
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
from audit import AuditLog  # noqa: E402
from execute import GateError, run_executor  # noqa: E402
from load_rules import PlanError  # noqa: E402

PLAN_ID = "0123456789ab"
ALL_FIELDS = {
    "Account": ["External_Id__c", "Source_System__c", "Name", "Industry", "Customer_Tier__c", "Website", "BillingCity",
                "BillingCountry", "AnnualRevenue", "NumberOfEmployees"],
    "Contact": ["External_Id__c", "Source_System__c", "FirstName", "LastName", "Email", "Phone", "Title"],
    "Case": ["External_Id__c", "Source_System__c", "Subject", "Source_Priority__c", "Status"],
}


class FakeOrg:
    """A stateful stand-in for git + the sf CLI: creates on first upsert, updates on the second, runs a fake trigger."""

    def __init__(self, plan_on_main, core_ids, scratch=("dev-scratch",), hide=(), fail_ids=(), break_trigger=False):
        self.plan_on_main, self.scratch, self.hide = plan_on_main, set(scratch), set(hide)
        self.fail_ids, self.break_trigger = set(fail_ids), break_trigger
        self.state = {"Account": {c: {"Source_System__c": "Core"} for c in core_ids}, "Contact": {}, "Case": {}}
        self.calls, self.jobs, self.n = [], {}, 0

    def upserts(self):
        return [c[c.index("--sobject") + 1] for c in self.calls if c[:4] == ["sf", "data", "upsert", "bulk"]]

    def run(self, cmd, cwd=None):
        self.calls.append(cmd)
        if cmd[0] == "git":
            if cmd[1] == "show":
                return (0, self.plan_on_main, "") if self.plan_on_main is not None else (128, "", "not found")
            return 0, "", ""
        key = tuple(cmd[1:3])
        if key == ("org", "list"):
            return self.ok({"scratchOrgs": [{"alias": a, "username": a + "@x.com"} for a in self.scratch],
                            "nonScratchOrgs": [{"alias": "prod-jwt"}]})
        if key == ("sobject", "describe"):
            obj = cmd[cmd.index("--sobject") + 1]
            fields = []
            for name in ALL_FIELDS[obj]:
                if name in self.hide:
                    continue
                f = {"name": name, "externalId": name == "External_Id__c", "picklistValues": []}
                if name == "Source_System__c":
                    f["picklistValues"] = [{"value": "Core", "active": True}, {"value": "AcquiredCo", "active": True}]
                if name == "Status":
                    f["picklistValues"] = [{"value": v, "active": True} for v in ("New", "Working", "Escalated", "Closed")]
                fields.append(f)
            return self.ok({"fields": fields})
        if key == ("data", "upsert"):
            return self.upsert(cmd)
        if key == ("data", "bulk"):
            return self.results(cmd, cwd)
        if key == ("data", "query"):
            return self.query(cmd[cmd.index("--query") + 1])
        raise AssertionError(f"unexpected command {cmd}")

    @staticmethod
    def ok(result):
        return 0, json.dumps({"status": 0, "result": result}), ""

    def upsert(self, cmd):
        obj, path = cmd[cmd.index("--sobject") + 1], cmd[cmd.index("--file") + 1]
        with open(path, newline="") as fh:
            assert b"\r" not in Path(path).read_bytes(), "Bulk API needs LF"
            rows = list(csv.DictReader(fh))
        self.n += 1
        job = "750" + str(self.n).zfill(12)
        ok, failed = [], []
        for r in rows:
            ext = r["External_Id__c"]
            parent = r.get("Account.External_Id__c")
            if ext in self.fail_ids:
                failed.append({"External_Id__c": ext, "sf__Error": "FIELD_INTEGRITY_EXCEPTION"})
            elif parent is not None and parent not in self.state["Account"]:
                failed.append({"External_Id__c": ext, "sf__Error": "no parent " + parent})
            else:
                created = ext not in self.state[obj]
                rec = dict(r)
                if obj == "Case":
                    rec["Priority"] = None if self.break_trigger else "Medium"
                self.state[obj][ext] = rec
                ok.append({"sf__Id": "x", "sf__Created": "true" if created else "false", "External_Id__c": ext})
        self.jobs[job] = (ok, failed)
        return self.ok({"jobInfo": {"id": job, "numberRecordsProcessed": len(rows)}})

    def results(self, cmd, cwd):
        job = cmd[cmd.index("--job-id") + 1]
        ok, failed = self.jobs[job]
        res = {"processedRecords": len(ok) + len(failed), "failedRecords": len(failed), "successFilePath": f"{job}-success-records.csv"}
        for name, rows in ((res["successFilePath"], ok), (f"{job}-failed-records.csv", failed)):
            if rows:
                with open(Path(cwd) / name, "w", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                    w.writeheader()
                    w.writerows(rows)
        if failed:
            res["failedFilePath"] = f"{job}-failed-records.csv"
        return self.ok(res)

    def query(self, q):
        s = self.state
        acq = lambda obj: [r for r in s[obj].values() if r.get("Source_System__c") == "AcquiredCo"]  # noqa: E731
        m = re.search(r"FROM (Account|Contact|Case) WHERE Source_System__c = 'AcquiredCo'$", q)
        if m:
            n = len(acq(m.group(1)))
        elif "Account.Source_System__c = 'Core'" in q:
            obj = "Contact" if "FROM Contact" in q else "Case"
            n = sum(1 for r in acq(obj) if s["Account"][r["Account.External_Id__c"]]["Source_System__c"] == "Core")
        elif "Priority = null" in q:
            n = sum(1 for r in acq("Case") if r.get("Priority") is None)
        else:
            raise AssertionError(q)
        return self.ok({"records": [{"attributes": {}, "n": n}]})


class ExecuteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        subprocess.run([sys.executable, "-I", str(AGENT / "data" / "generate.py"),
                        "--out", str(t / "out"), "--truth", str(t / "truth")], check=True, capture_output=True)
        cls.out = str(t / "out")
        with (t / "truth" / "existing_accounts.csv").open() as f:
            cls.core_ids = [r["External_Id__c"] for r in csv.DictReader(f)]
        plan = json.loads((AGENT / "evals" / "fixtures" / "baseline_plan.json").read_text())
        next(m for m in plan["mappings"] if m["source"] == "accounts.tier")["target"] = "Account.Customer_Tier__c"
        cls.plan = plan

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.root = Path(self.t.name)
        d = self.root / "plans" / PLAN_ID
        d.mkdir(parents=True)
        (d / "plan.json").write_text(json.dumps(self.plan))
        self.plan_dir = d
        self.rec, self.load, self.audit_db = self.root / "rec", self.root / "load", str(self.root / "a.sqlite")

    def tearDown(self):
        self.t.cleanup()

    def org(self, **kw):
        kw.setdefault("plan_on_main", json.dumps(self.plan))
        return FakeOrg(core_ids=self.core_ids, **kw)

    def go(self, org, apply=False, target="dev-scratch", plan_dir=None):
        with contextlib.redirect_stdout(io.StringIO()):  # the executor echoes its report
            return run_executor(plan_dir or self.plan_dir, target, self.root, apply, self.out, str(self.load),
                                str(self.rec), self.audit_db, run=org.run)

    def last_report(self):
        d = sorted(self.rec.iterdir(), key=lambda p: p.stat().st_mtime)[-1]
        return d, (d / "reconciliation.md").read_text(), json.loads((d / "reconciliation.json").read_text()) if (d / "reconciliation.json").exists() else None

    def decisions(self):
        a = AuditLog(self.audit_db)
        try:
            return [r[7] for r in a.rows()]
        finally:
            a.close()

    # ---- dry run
    def test_dry_run_writes_nothing_to_the_org(self):
        org = self.org()
        self.assertEqual(self.go(org), 0)
        self.assertEqual(org.upserts(), [])
        _, md, _ = self.last_report()
        self.assertIn("DRY RUN", md)
        self.assertTrue((self.load / "accounts.csv").exists())
        self.assertFalse(any(org.state[o] for o in ("Contact", "Case")))

    # ---- apply
    def test_apply_loads_parents_first_and_reconciles_against_the_org(self):
        org = self.org()
        self.assertEqual(self.go(org, apply=True), 0)
        self.assertEqual(org.upserts(), ["Account", "Contact", "Case"])
        _, md, js = self.last_report()
        self.assertTrue(all(c["ok"] for c in js["checks"]), js["checks"])
        self.assertEqual(js["loads"]["accounts"]["created"], len([r for r in org.state["Account"].values() if r["Source_System__c"] == "AcquiredCo"]))
        self.assertIn("Verified in the org", md)
        self.assertIn("Rejected rows", md)

    def test_second_run_creates_nothing_and_updates_everything(self):
        org = self.org()
        self.go(org, apply=True)
        first = self.last_report()[2]["loads"]
        self.assertEqual(self.go(org, apply=True), 0)
        second = self.last_report()[2]["loads"]
        for f in ("accounts", "contacts", "cases"):
            self.assertEqual(second[f]["created"], 0, f)
            self.assertEqual(second[f]["updated"], first[f]["created"], f)

    # ---- gates: every refusal happens before any write
    def assertRefused(self, exc, org, **kw):
        with self.assertRaises(exc):
            self.go(org, apply=True, **kw)
        self.assertEqual(org.upserts(), [])
        self.assertIn("refused", self.decisions())

    def test_plan_not_merged_to_main_is_refused(self):
        self.assertRefused(GateError, self.org(plan_on_main=None))

    def test_local_plan_that_differs_from_the_merged_one_is_refused(self):
        other = copy.deepcopy(self.plan)
        other["notes"] = "edited after approval"
        self.assertRefused(GateError, self.org(plan_on_main=json.dumps(other)))

    def test_non_scratch_org_is_refused_even_with_apply(self):
        self.assertRefused(GateError, self.org(), target="prod-jwt")

    def test_field_without_visibility_is_refused_with_the_permission_set_hint(self):
        org = self.org(hide=["Customer_Tier__c"])
        with self.assertRaisesRegex(GateError, "Acquisition_Onboarding"):
            self.go(org, apply=True)
        self.assertEqual(org.upserts(), [])

    def test_plan_with_an_unsupported_target_is_refused(self):
        bad = copy.deepcopy(self.plan)
        next(m for m in bad["mappings"] if m["source"] == "accounts.website")["target"] = "Account.Rating"
        (self.plan_dir / "plan.json").write_text(json.dumps(bad))
        self.assertRefused(PlanError, self.org(plan_on_main=json.dumps(bad)))

    # ---- failure handling
    def test_failed_parent_rows_stop_the_run_before_children_load(self):
        from load_rules import read_exports, transform
        a, c, k = read_exports(self.out)
        first = transform(self.plan, a, c, k)["rows"]["accounts"][0]["External_Id__c"]
        org = self.org(fail_ids=[first])
        self.assertEqual(self.go(org, apply=True), 1)
        self.assertEqual(org.upserts(), ["Account"])
        _, md, js = self.last_report()
        self.assertIn("STOPPED", md)
        self.assertIn("children were not loaded", js["stopped"])

    # ---- rows the org itself rejects (duplicate rules, validation): reported, never bypassed, never fatal
    def loaded_rows(self):
        from load_rules import read_exports, transform
        a, c, k = read_exports(self.out)
        return transform(self.plan, a, c, k)["rows"]

    def test_org_rejected_contacts_do_not_stop_cases_and_still_reconcile(self):
        rows = self.loaded_rows()
        victims = [rows["contacts"][0]["External_Id__c"], rows["contacts"][5]["External_Id__c"]]
        org = self.org(fail_ids=victims)
        self.assertEqual(self.go(org, apply=True), 0)
        self.assertEqual(org.upserts(), ["Account", "Contact", "Case"])  # cases still loaded
        _, md, js = self.last_report()
        self.assertIn("Rejected by the org itself", md)
        self.assertEqual(sorted(js["loads"]["contacts"]["failed_ids"]), sorted(victims))
        self.assertTrue(all(c["ok"] for c in js["checks"]), js["checks"])
        got = {c["check"]: c["expected"] for c in js["checks"]}
        self.assertEqual(got["contacts in org tagged AcquiredCo"], len(rows["contacts"]) - 2)

    def test_a_rejected_reparented_contact_lowers_the_expected_reparent_count(self):
        rows = self.loaded_rows()
        victim = next(o["External_Id__c"] for o in rows["contacts"] if o["Account.External_Id__c"].startswith("CORE-"))
        org = self.org(fail_ids=[victim])
        self.assertEqual(self.go(org, apply=True), 0)
        _, _, js = self.last_report()
        by = {c["check"]: c for c in js["checks"]}
        full = sum(1 for o in rows["contacts"] if o["Account.External_Id__c"].startswith("CORE-"))
        self.assertEqual(by["contacts re-parented to Core accounts"]["expected"], full - 1)
        self.assertTrue(by["contacts re-parented to Core accounts"]["ok"])

    def test_second_run_repeats_the_org_rejections_and_still_creates_nothing(self):
        rows = self.loaded_rows()
        org = self.org(fail_ids=[rows["contacts"][0]["External_Id__c"]])
        self.go(org, apply=True)
        self.assertEqual(self.go(org, apply=True), 0)
        second = self.last_report()[2]["loads"]
        self.assertEqual(second["contacts"]["created"], 0)
        self.assertEqual(second["contacts"]["failed"], 1)
        self.assertEqual(second["contacts"]["updated"], len(rows["contacts"]) - 1)

    def test_verification_catches_a_trigger_that_did_not_fire(self):
        org = self.org(break_trigger=True)
        self.assertEqual(self.go(org, apply=True), 1)
        _, md, _ = self.last_report()
        self.assertIn("MISMATCH", md)
        self.assertEqual(self.decisions()[-1], "mismatch")


if __name__ == "__main__":
    unittest.main()
