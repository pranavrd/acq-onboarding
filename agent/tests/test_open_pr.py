import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
from open_pr import OpenPrError, build_body, open_plan_pr  # noqa: E402

RUN_ID = "0123456789ab"
PLAN = {"meta": {"model": "m", "tool_calls": 3, "input_tokens": 1, "output_tokens": 2}, "mappings": [{}], "dedupe_matches": [],
        "proposed_new_fields": [], "data_quality_issues": [], "low_confidence_threshold": 0.8,
        "needs_human": [{"kind": "mapping", "ref": f"r{i}", "why": "low confidence"} for i in range(25)]}


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


class OpenPrTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.origin, self.work = t / "origin.git", t / "work"
        git(t, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(t, "clone", "-q", str(self.origin), str(self.work))
        for k, v in (("user.name", "dev"), ("user.email", "dev@example.com")):
            git(self.work, "config", k, v)
        git(self.work, "checkout", "-q", "-b", "main")
        (self.work / "README.md").write_text("hi\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "init")
        git(self.work, "push", "-q", "-u", "origin", "main")
        # the developer is mid-work on another branch with an uncommitted file
        git(self.work, "checkout", "-q", "-b", "feature")
        (self.work / "wip.txt").write_text("uncommitted\n")
        self.run_dir = self.work / "plans" / RUN_ID
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "plan.json").write_text(json.dumps(PLAN))
        (self.run_dir / "plan.md").write_text("# plan\n")
        (self.run_dir / "notes.txt").write_text("must not be pushed\n")
        self.gh_calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def fake_gh(self, cmd, cwd):
        self.gh_calls.append(cmd)
        return "https://github.com/o/r/pull/9\n"

    def open(self, run_dir=None):
        return open_plan_pr(run_dir or self.run_dir, self.work, run=self.fake_gh)

    def remote_files(self):
        return git(self.origin, "diff", "--name-status", "main", f"plan/{RUN_ID}").splitlines()

    def test_happy_path_adds_exactly_two_files_and_opens_a_pr(self):
        url = self.open()
        self.assertEqual(url, "https://github.com/o/r/pull/9")
        self.assertEqual(sorted(self.remote_files()), [f"A\tplans/{RUN_ID}/plan.json", f"A\tplans/{RUN_ID}/plan.md"])
        self.assertEqual(git(self.origin, "log", "-1", "--format=%an", f"plan/{RUN_ID}"), "planning-agent")
        (cmd,) = self.gh_calls
        self.assertEqual(cmd[:3], ["gh", "pr", "create"])
        self.assertEqual(cmd[cmd.index("--base") + 1], "main")
        self.assertEqual(cmd[cmd.index("--head") + 1], f"plan/{RUN_ID}")
        self.assertFalse(any("merge" in part for part in cmd if part not in cmd[cmd.index("--body"):]))

    def test_developers_checkout_is_untouched(self):
        before = (git(self.work, "branch", "--show-current"), git(self.work, "status", "--porcelain"), git(self.work, "rev-parse", "HEAD"))
        self.open()
        after = (git(self.work, "branch", "--show-current"), git(self.work, "status", "--porcelain"), git(self.work, "rev-parse", "HEAD"))
        self.assertEqual(before, after)
        self.assertEqual(before[0], "feature")
        self.assertTrue((self.work / "wip.txt").exists())
        # no leftovers: no local plan branch, only the main worktree
        self.assertNotIn(f"plan/{RUN_ID}", git(self.work, "branch", "--list"))
        self.assertEqual(len(git(self.work, "worktree", "list").splitlines()), 1)

    def test_bad_run_ids_and_paths_are_rejected(self):
        bad = self.work / "plans" / "not-a-run-id"
        bad.mkdir()
        for name in ("plan.json", "plan.md"):
            (bad / name).write_text("x")
        with self.assertRaises(OpenPrError):
            self.open(bad)
        outside = Path(self.tmp.name) / "elsewhere" / RUN_ID
        outside.mkdir(parents=True)
        for name in ("plan.json", "plan.md"):
            (outside / name).write_text("{}")
        with self.assertRaises(OpenPrError):
            self.open(outside)
        self.assertEqual(self.gh_calls, [])

    def test_missing_plan_file_is_rejected(self):
        (self.run_dir / "plan.md").unlink()
        with self.assertRaises(OpenPrError):
            self.open()
        self.assertEqual(self.gh_calls, [])

    def test_existing_remote_branch_is_never_overwritten(self):
        self.open()
        sha = git(self.origin, "rev-parse", f"plan/{RUN_ID}")
        with self.assertRaisesRegex(OpenPrError, "already exists"):
            self.open()
        self.assertEqual(git(self.origin, "rev-parse", f"plan/{RUN_ID}"), sha)
        self.assertEqual(len(self.gh_calls), 1)

    def test_preexisting_local_branch_survives_a_failed_attempt(self):
        git(self.work, "branch", f"plan/{RUN_ID}", "main")
        with self.assertRaises(OpenPrError):
            self.open()
        self.assertIn(f"plan/{RUN_ID}", git(self.work, "branch", "--list"))
        self.assertEqual(self.gh_calls, [])

    def test_body_says_the_agent_cannot_merge_and_truncates_the_list(self):
        body = build_body(PLAN, RUN_ID, "RESULT: PASS")
        self.assertIn("cannot merge", body)
        self.assertIn("Merging this PR approves the plan", body)
        self.assertIn("Needs a human: 25", body)
        self.assertIn("and 5 more", body)
        self.assertIn("RESULT: PASS", body)


class ProposeTest(unittest.TestCase):
    """plan.propose: a plan that fails the eval is not put in front of a human."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        subprocess.run([sys.executable, "-I", str(AGENT / "data" / "generate.py"),
                        "--out", str(t / "out"), "--truth", str(t / "truth")], check=True, capture_output=True)
        cls.truth = str(t / "truth" / "truth.json")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        from audit import AuditLog
        self.t = tempfile.TemporaryDirectory()
        self.audit = AuditLog(str(Path(self.t.name) / "a.sqlite"), run_id="r")
        self.calls = []

    def tearDown(self):
        self.audit.close()
        self.t.cleanup()

    def opener(self, run_dir, root, eval_summary=None):
        self.calls.append(eval_summary)
        return "https://github.com/o/r/pull/1"

    def run_dir(self, fixture):
        d = Path(self.t.name) / "plans" / RUN_ID
        d.mkdir(parents=True)
        (d / "plan.json").write_text((AGENT / "evals" / "fixtures" / fixture).read_text())
        return d

    def decisions(self):
        return [r[7] for r in self.audit.rows("r")]

    def test_failing_plan_is_not_opened(self):
        from plan import propose
        status, detail = propose(self.run_dir("weak_plan.json"), self.audit, self.truth, ".", opener=self.opener)
        self.assertEqual(status, "blocked")
        self.assertIn("RESULT: FAIL", detail)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.decisions(), ["not opened: eval failed"])

    def test_failing_plan_can_be_opened_on_request(self):
        from plan import propose
        status, _ = propose(self.run_dir("weak_plan.json"), self.audit, self.truth, ".", allow_failed_eval=True, opener=self.opener)
        self.assertEqual(status, "opened")
        self.assertIn("RESULT: FAIL", self.calls[0])  # the reviewer still sees that it failed

    def test_passing_plan_is_opened_with_the_eval_in_the_body(self):
        from plan import propose
        status, url = propose(self.run_dir("baseline_plan.json"), self.audit, self.truth, ".", opener=self.opener)
        self.assertEqual((status, url), ("opened", "https://github.com/o/r/pull/1"))
        self.assertIn("RESULT: PASS", self.calls[0])
        self.assertEqual(self.decisions(), ["pr opened"])

    def test_opener_errors_are_audited_not_raised(self):
        from plan import propose

        def boom(*a, **k):
            raise OpenPrError("branch already exists")

        status, detail = propose(self.run_dir("baseline_plan.json"), self.audit, self.truth, ".", opener=boom)
        self.assertEqual((status, detail), ("error", "branch already exists"))
        self.assertEqual(self.decisions(), ["error"])

    def test_without_ground_truth_the_summary_says_only_mappings_were_checked(self):
        from plan import propose
        propose(self.run_dir("baseline_plan.json"), self.audit, "/nonexistent.json", ".", opener=self.opener)
        self.assertIn("mappings only", self.calls[0])


if __name__ == "__main__":
    unittest.main()
