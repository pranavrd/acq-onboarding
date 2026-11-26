#!/usr/bin/env python3
"""Open a pull request that proposes a plan. The agent proposes; a human merges.

  python agent/open_pr.py plans/<run_id>

What this is allowed to do (each limit is checked by a test in agent/tests/test_open_pr.py):
  - add exactly two files, plans/<run_id>/plan.json and plans/<run_id>/plan.md, on a new branch plan/<run_id>
  - push that branch (never --force) and open a PR against main
  It never merges, never edits an existing file, never touches the user's checked-out branch or working tree:
  the commit is made in a throwaway `git worktree` cut from origin/main.

Merging the PR is the human approval that a later step (execute) will require.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_ID = re.compile(r"^[0-9a-f]{12}$")
FILES = ("plan.json", "plan.md")


class OpenPrError(RuntimeError):
    pass


def _run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise OpenPrError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout


def build_body(plan: dict, run_id: str, eval_summary: str | None = None) -> str:
    nh = plan.get("needs_human", [])
    meta = plan.get("meta", {})
    lines = [
        f"Proposed onboarding plan from run `{run_id}`. **Read-only: nothing has been written to any org.**",
        "",
        f"- Model: `{meta.get('model')}` | tool calls: {meta.get('tool_calls')} | tokens in/out: {meta.get('input_tokens')}/{meta.get('output_tokens')}",
        f"- Mappings: {len(plan.get('mappings', []))} | duplicates of existing accounts: {len(plan.get('dedupe_matches', []))} | "
        f"proposed new fields: {len(plan.get('proposed_new_fields', []))} | data-quality entries: {len(plan.get('data_quality_issues', []))}",
        f"- **Needs a human: {len(nh)}** (confidence below {plan.get('low_confidence_threshold')}, no target field, or flagged)",
    ]
    if eval_summary:
        lines += ["", "### Eval against ground truth", "", "```", eval_summary.strip(), "```"]
    lines += ["", "### Needs a human", ""]
    lines += [f"- **{n['kind']}** `{n['ref']}`: {n['why']}" for n in nh[:20]] or ["None."]
    if len(nh) > 20:
        lines.append(f"- ... and {len(nh) - 20} more in `plan.json`")
    lines += ["", "Full plan: `plans/%s/plan.md`." % run_id, "",
              "**Merging this PR approves the plan.** The load step refuses to run unless the plan is on `main`.",
              "", "🤖 Opened by the planning agent (`agent/open_pr.py`). It cannot merge."]
    return "\n".join(lines)


def open_plan_pr(run_dir, repo_root, base="main", run=_run, eval_summary=None) -> str:
    """Returns the PR URL. `run` is injectable so tests can fake `gh` while using real git."""
    run_dir, repo_root = Path(run_dir).resolve(), Path(repo_root).resolve()
    run_id = run_dir.name
    if not RUN_ID.match(run_id):
        raise OpenPrError(f"run id {run_id!r} must be 12 hex characters")
    if run_dir.parent != (repo_root / "plans").resolve():
        raise OpenPrError("run directory must be plans/<run_id> inside the repository")
    for f in FILES:
        if not (run_dir / f).is_file():
            raise OpenPrError(f"{run_dir / f} not found")
    plan = json.loads((run_dir / "plan.json").read_text())
    branch = f"plan/{run_id}"

    _run(["git", "fetch", "-q", "origin", base], repo_root)
    if _run(["git", "ls-remote", "--heads", "origin", branch], repo_root).strip():
        raise OpenPrError(f"branch {branch} already exists on origin; refusing to overwrite it")
    tmp = Path(tempfile.mkdtemp(prefix="plan-pr-"))
    wt = tmp / "wt"
    created = False  # only clean up what this call made; never delete a pre-existing local branch
    try:
        _run(["git", "worktree", "add", "-q", "-b", branch, str(wt), f"origin/{base}"], repo_root)
        created = True
        dest = wt / "plans" / run_id
        dest.mkdir(parents=True)
        for f in FILES:
            shutil.copy2(run_dir / f, dest / f)
        _run(["git", "add", "--", f"plans/{run_id}/plan.json", f"plans/{run_id}/plan.md"], wt)
        staged = sorted(_run(["git", "diff", "--cached", "--name-status"], wt).split("\n"))
        expected = sorted([f"A\tplans/{run_id}/plan.json", f"A\tplans/{run_id}/plan.md", ""])
        if staged != expected:  # belt and braces: nothing else may ride along
            raise OpenPrError(f"unexpected staged changes: {staged}")
        _run(["git", "-c", "user.name=planning-agent", "-c", "user.email=planning-agent@users.noreply.github.com",
              "commit", "-q", "-m", f"Plan {run_id}: proposed onboarding mapping and dedupe",
              "-m", "Proposed by the read-only planning agent. Merging approves the plan."], wt)
        _run(["git", "push", "-q", "-u", "origin", branch], wt)  # never --force
        out = run(["gh", "pr", "create", "--base", base, "--head", branch,
                   "--title", f"Plan {run_id}: acquisition onboarding mapping and dedupe",
                   "--body", build_body(plan, run_id, eval_summary)], str(repo_root))
        url = next((w for w in out.split() if w.startswith("https://")), out.strip())
        return url
    finally:
        if created:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo_root, capture_output=True)
            subprocess.run(["git", "branch", "-D", branch], cwd=repo_root, capture_output=True)  # local copy only
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python agent/open_pr.py plans/<run_id>")
    root = Path(__file__).resolve().parents[1]
    print(open_plan_pr(sys.argv[1], root))


if __name__ == "__main__":
    main()
