#!/usr/bin/env python3
"""Load an approved onboarding plan into a SCRATCH org. Dry run by default; --apply is the only mode that writes.

  python agent/execute.py --plan plans/<run_id> --org dev-scratch            # dry run
  python agent/execute.py --plan plans/<run_id> --org dev-scratch --apply    # load + reconcile

Gates (any failure stops the run before anything is written):
  1. the plan is on origin/main (merged = approved) and matches the local copy
  2. the org is a scratch org: prod and the Dev Hub are refused even with --apply
  3. the approved plan maps only fields this executor has rules for, and every field it will write exists and is
     visible in the org (this is where a missing field-level-security grant shows up, before anything is loaded)

Order: accounts, then contacts, then cases, each an upsert on External_Id__c, so a rerun updates instead of
duplicating. Parents that fail stop the run before their children load. Everything goes to the audit log, and a
reconciliation report is written to reconciliation/<exec_id>/.
"""
import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import AuditLog  # noqa: E402
from load_rules import (FILES, OBJECT, SOURCE_SYSTEM, STATUS_MAP, PlanError, read_exports,  # noqa: E402
                        resolve_mapping, transform, write_load_files)


class GateError(RuntimeError):
    pass


class SfError(RuntimeError):
    def __init__(self, message, data=None):
        super().__init__(message)
        self.data = data


def sh(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


class Sf:
    def __init__(self, run=sh):
        self.run = run

    def json(self, *args, cwd=None):
        rc, out, err = self.run(["sf", *args, "--json"], cwd)
        try:
            data = json.loads(out)
        except ValueError:
            raise SfError(f"sf {' '.join(args[:3])} gave no JSON: {(err or out).strip()[:300]}")
        if data.get("status", 0) != 0:
            raise SfError(data.get("message") or f"sf {' '.join(args[:3])} failed", data)
        return data.get("result", data)


# ------------------------------------------------------------------ gates
def gate_approved(plan_dir, repo_root, run=sh):
    """The plan must be on origin/main (merged = approved) and equal the local copy. Returns the merged plan."""
    run_id = Path(plan_dir).name
    rel = f"plans/{run_id}/plan.json"
    run(["git", "fetch", "-q", "origin", "main"], str(repo_root))
    rc, out, _ = run(["git", "show", f"origin/main:{rel}"], str(repo_root))
    if rc != 0:
        raise GateError(f"{rel} is not on origin/main. Merge the plan PR first; that merge is the approval.")
    merged = json.loads(out)
    local = Path(plan_dir) / "plan.json"
    if local.exists() and json.loads(local.read_text()) != merged:
        raise GateError(f"the local {rel} differs from the merged one; refusing to guess which is approved")
    return merged


def gate_scratch(org, sf):
    result = sf.json("org", "list")
    names = set()
    for o in result.get("scratchOrgs", []):
        names |= {o.get("alias"), o.get("username")}
    if org not in names:
        raise GateError(f"{org!r} is not a scratch org. This executor loads into scratch orgs only.")


def gate_schema(org, sf, loadable):
    """Every field we will write must exist and be visible; External_Id__c must be an external ID."""
    problems = []
    for file in FILES:
        obj = OBJECT[file]
        needed = {t.split(".", 1)[1] for t in loadable.values() if t.startswith(obj + ".")} | {"Source_System__c"}
        needed -= {"AccountId"}  # standard lookup, set through Account.External_Id__c
        fields = {f["name"]: f for f in sf.json("sobject", "describe", "--sobject", obj, "--target-org", org)["fields"]}
        missing = sorted(needed - set(fields))
        if missing:
            problems.append(f"{obj}: missing or not visible: {missing} (if the field was just deployed, assign the "
                            f"Acquisition_Onboarding permission set: sf org assign permset --name Acquisition_Onboarding)")
            continue
        if not fields["External_Id__c"].get("externalId"):
            problems.append(f"{obj}.External_Id__c is not an external ID")
        ss = {p["value"] for p in fields["Source_System__c"].get("picklistValues", []) if p.get("active", True)}
        if SOURCE_SYSTEM not in ss:
            problems.append(f"{obj}.Source_System__c has no {SOURCE_SYSTEM!r} value")
        if obj == "Case":
            status = {p["value"] for p in fields["Status"].get("picklistValues", []) if p.get("active", True)}
            absent = sorted(set(STATUS_MAP.values()) - status)
            if absent:
                problems.append(f"Case.Status has no value(s) {absent} that the status decision maps to")
    if problems:
        raise GateError("; ".join(problems))


# ------------------------------------------------------------------ load
def _find_job_id(result):
    for path in (("jobInfo", "id"), ("id",), ("jobId",)):
        v = result
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
        if isinstance(v, str) and v.startswith("750"):
            return v
    m = re.search(r"\b750[A-Za-z0-9]{12,15}\b", json.dumps(result))
    if m:
        return m.group(0)
    raise SfError(f"could not find the Bulk job id in sf output: {json.dumps(result)[:300]}")


def _read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def load_object(sf, org, sobject, file, workdir):
    """Upsert one object; returns {job_id, processed, created, updated, failed, failures:[{id,error}]}."""
    try:
        res = sf.json("data", "upsert", "bulk", "--sobject", sobject, "--file", str(file),
                      "--external-id", "External_Id__c", "--target-org", org, "--wait", "10")
    except SfError as e:  # a job with failed rows can surface as an error; the job still exists
        if not e.data:
            raise
        res = e.data
    job = _find_job_id(res)
    results = sf.json("data", "bulk", "results", "--job-id", job, "--target-org", org, cwd=str(workdir))
    created = updated = 0
    if results.get("successFilePath"):
        for r in _read_csv(Path(workdir) / results["successFilePath"]):
            created += str(r.get("sf__Created", "")).lower() == "true"
            updated += str(r.get("sf__Created", "")).lower() != "true"
    failures = []
    if results.get("failedFilePath"):
        failures = [{"id": r.get("External_Id__c", ""), "error": r.get("sf__Error", "")}
                    for r in _read_csv(Path(workdir) / results["failedFilePath"])]
    return {"job_id": job, "processed": results.get("processedRecords"), "created": created, "updated": updated,
            "failed": len(failures) or results.get("failedRecords") or 0, "failures": failures[:20],
            "failed_ids": [x["id"] for x in failures],
            "error_counts": dict(Counter(x["error"].split(":")[0] for x in failures))}


def _count(sf, org, soql):
    recs = sf.json("data", "query", "--query", soql, "--target-org", org)["records"]
    row = recs[0]
    return next(v for k, v in row.items() if k != "attributes")


def verify(sf, org, result, failed_ids=None):
    """Evidence from the org itself, not from the job results.

    Expected counts are the rows we sent minus the rows the org itself rejected (duplicate rules, validation),
    so an org-side rejection is reported, not mistaken for a loss.
    """
    failed_ids = failed_ids or {}
    rows, merged = result["rows"], result["merged"]
    ok_rows = {f: [o for o in rows[f] if o["External_Id__c"] not in set(failed_ids.get(f, []))] for f in FILES}
    core = set(merged["accounts"].values())
    reparented = {f: sum(1 for o in ok_rows[f] if o["Account.External_Id__c"] in core) for f in ("contacts", "cases")}
    q = [("accounts in org tagged AcquiredCo", len(ok_rows["accounts"]), "SELECT COUNT(Id) n FROM Account WHERE Source_System__c = 'AcquiredCo'"),
         ("contacts in org tagged AcquiredCo", len(ok_rows["contacts"]), "SELECT COUNT(Id) n FROM Contact WHERE Source_System__c = 'AcquiredCo'"),
         ("cases in org tagged AcquiredCo", len(ok_rows["cases"]), "SELECT COUNT(Id) n FROM Case WHERE Source_System__c = 'AcquiredCo'"),
         ("contacts re-parented to Core accounts", reparented["contacts"],
          "SELECT COUNT(Id) n FROM Contact WHERE Source_System__c = 'AcquiredCo' AND Account.Source_System__c = 'Core'"),
         ("cases re-parented to Core accounts", reparented["cases"],
          "SELECT COUNT(Id) n FROM Case WHERE Source_System__c = 'AcquiredCo' AND Account.Source_System__c = 'Core'"),
         ("AcquiredCo cases without a Priority (trigger fired on all)", 0,
          "SELECT COUNT(Id) n FROM Case WHERE Source_System__c = 'AcquiredCo' AND Priority = null")]
    return [{"check": name, "expected": exp, "got": (got := _count(sf, org, soql)), "ok": got == exp} for name, exp, soql in q]


# ------------------------------------------------------------------ report
def _by_reason(rejected):
    groups = {}
    for r in rejected:
        groups.setdefault((r["file"], r["reason"]), []).append(r["id"])
    return groups


def render_report(exec_id, plan_id, org, mode, result, loads, checks, stopped=None):
    L = [f"# Reconciliation `{exec_id}`", "", f"- Plan: `{plan_id}` (merged on main)", f"- Org: `{org}` (scratch)",
         f"- Mode: **{mode}**", ""]
    if stopped:
        L += [f"**STOPPED: {stopped}**", ""]
    L += ["## Per object", "", "| Object | Rows in | Loaded | Created | Updated | Merged into Core | Rejected | Failed in org |", "|---|---|---|---|---|---|---|---|"]
    for f in FILES:
        rej = sum(1 for r in result["rejected"] if r["file"] == f"{f}.csv")
        ld = loads.get(f)
        merged = len(result["merged"]["accounts"]) if f == "accounts" else 0
        L.append(f"| {OBJECT[f]} | {result['in'][f]} | {len(result['rows'][f])} | {ld['created'] if ld else '-'} | "
                 f"{ld['updated'] if ld else '-'} | {merged} | {rej} | {ld['failed'] if ld else '-'} |")
    m = result["merged"]
    L += ["", f"Merged accounts were not inserted; {m['contacts_reparented']} contacts and {m['cases_reparented']} cases "
          f"were re-parented to the existing Core account. Core accounts were not modified.", "", "## Rejected rows (nothing partial lands silently)", ""]
    for (file, reason), ids in sorted(_by_reason(result["rejected"]).items()):
        L.append(f"- `{file}` {reason}: **{len(ids)} rows** (e.g. {', '.join(list(dict.fromkeys(ids))[:5])})")
    org_failed = {f: ld for f, ld in loads.items() if ld["failed"]}
    if org_failed:
        L += ["", "## Rejected by the org itself (its own duplicate and validation rules; not bypassed)", ""]
        for f, ld in org_failed.items():
            L.append(f"- {OBJECT[f]}: **{ld['failed']} rows**, {ld['error_counts']} (e.g. {', '.join(ld['failed_ids'][:5])})")
    L += ["", "## Loaded with a warning", ""]
    wc = Counter((w["file"], w["warning"].split(": ", 1)[-1]) for w in result["warnings"])
    L += [f"- `{f}` {w}: **{n}**" for (f, w), n in sorted(wc.items())] or ["None."]
    L += ["", "## Columns not loaded", ""]
    L += [f"- `{n['source']}`: {n['reason']}" for n in result["not_loaded"]] or ["None."]
    if loads:
        L += ["", "## Bulk jobs", ""] + [f"- {OBJECT[f]}: `{ld['job_id']}`" + (f" FAILED rows: {ld['failures'][:3]}" if ld["failed"] else "") for f, ld in loads.items()]
    if checks:
        L += ["", "## Verified in the org", "", "| Check | Expected | Got | |", "|---|---|---|---|"]
        L += [f"| {c['check']} | {c['expected']} | {c['got']} | {'ok' if c['ok'] else '**MISMATCH**'} |" for c in checks]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ main flow
def run_executor(plan_dir, org, repo_root, apply=False, input_dir="agent/data/out", load_dir="agent/data/load",
                 report_dir="reconciliation", audit_db="agent/audit.sqlite", run=sh):
    """Returns an exit code (0 ok, 1 stopped or mismatched). Raises GateError / PlanError / SfError for refusals."""
    audit = AuditLog(audit_db)
    try:
        return _execute(audit, plan_dir, org, repo_root, apply, input_dir, load_dir, report_dir, run)
    finally:
        audit.close()


def _execute(audit, plan_dir, org, repo_root, apply, input_dir, load_dir, report_dir, run):
    exec_id, plan_id = audit.run_id, Path(plan_dir).name
    sf = Sf(run)
    out_dir = Path(report_dir) / exec_id

    def step(name, summary, decision="ok"):
        audit.record(name, "execute", {"plan": plan_id, "org": org, "apply": apply}, summary, decision)

    try:
        plan = gate_approved(plan_dir, repo_root, run)
        step("gate", "plan is on origin/main", "passed")
        gate_scratch(org, sf)
        step("gate", f"{org} is a scratch org", "passed")
        loadable, _ = resolve_mapping(plan)
        step("gate", f"{len(loadable)} columns map to supported fields", "passed")
        gate_schema(org, sf, loadable)
        step("gate", "all target fields exist and are visible", "passed")
    except (GateError, PlanError, SfError) as e:
        step("gate", str(e)[:400], "refused")
        raise

    (out_dir / "bulk").mkdir(parents=True, exist_ok=True)  # only after every gate has passed
    accounts, contacts, cases = read_exports(input_dir)
    result = transform(plan, accounts, contacts, cases)
    paths = write_load_files(result, load_dir)
    step("transform", json.dumps({"in": result["in"], "loaded": {f: len(result["rows"][f]) for f in FILES},
                                  "rejected": len(result["rejected"]), "merged": len(result["merged"]["accounts"])}), "ok")

    if not apply:
        text = render_report(exec_id, plan_id, org, "DRY RUN (nothing written to any org)", result, {}, [])
        (out_dir / "reconciliation.md").write_text(text)
        step("done", f"dry run; report in {out_dir}", "dry run")
        print(text)
        return 0

    loads, stopped = {}, None
    for f in FILES:
        if f not in paths:
            continue
        ld = load_object(sf, org, OBJECT[f], paths[f], out_dir / "bulk")
        loads[f] = ld
        step("load", f"{OBJECT[f]} job {ld['job_id']}: created {ld['created']}, updated {ld['updated']}, failed {ld['failed']}",
             "ok" if not ld["failed"] else "failed rows")
        # Only accounts have children (contacts and cases point at them). A rejected contact or case blocks nothing
        # else, so it is reported and the run continues; a rejected account stops the run before its children.
        if ld["failed"] and f == "accounts":
            stopped = f"{OBJECT[f]} had {ld['failed']} failed row(s); children were not loaded"
            break
    failed_ids = {f: ld["failed_ids"] for f, ld in loads.items()}
    checks = [] if stopped else verify(sf, org, result, failed_ids)
    # Rows the org itself rejected are accounted for in the report, so they do not make the run fail to reconcile.
    ok = not stopped and all(c["ok"] for c in checks)
    text = render_report(exec_id, plan_id, org, "APPLIED", result, loads, checks, stopped)
    (out_dir / "reconciliation.md").write_text(text)
    (out_dir / "reconciliation.json").write_text(json.dumps({"exec_id": exec_id, "plan": plan_id, "org": org, "loads": loads,
                                                              "checks": checks, "stopped": stopped}, indent=2))
    n_org = sum(ld["failed"] for ld in loads.values())
    step("done", ("reconciles" if ok else "DOES NOT RECONCILE") + (f"; {n_org} rows rejected by the org" if n_org else ""),
         "ok" if ok else "mismatch")
    print(text)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="plans/<run_id>")
    ap.add_argument("--org", required=True, help="scratch org alias")
    ap.add_argument("--apply", action="store_true", help="actually write to the org (default is a dry run)")
    ap.add_argument("--input", default="agent/data/out")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        sys.exit(run_executor(args.plan, args.org, root, args.apply, args.input))
    except (GateError, PlanError, SfError) as e:
        sys.exit(f"REFUSED: {e}")


if __name__ == "__main__":
    main()
