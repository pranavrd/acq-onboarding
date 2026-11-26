#!/usr/bin/env python3
"""Score a plan.json against the golden mappings and the seeded ground truth. Exit 1 if any gate fails.

  python agent/eval_plan.py plans/<run_id>/plan.json --truth agent/data/truth/truth.json
  python agent/eval_plan.py plans/<run_id>/plan.json --mappings-only      # no ground-truth file needed

Gates (thresholds live in evals/golden_mapping.json):
  1. mapping accuracy >= 90% over every export column (a missing column counts as wrong)
  2. zero CONFIDENT wrong mappings (wrong target with confidence >= 0.8): a wrong guess flagged for a human is
     tolerable, a wrong guess presented as certain is not
  3. dedupe precision >= 95% and recall >= 90% against the seeded near-duplicates
  4. every seeded data-quality kind reported with the exact count

Run it after changing the prompt, the model, or the tools. It is the guardrail against silent regressions.
"""
import argparse
import json
import sys
from pathlib import Path

GOLDEN_PATH = Path(__file__).resolve().parent / "evals" / "golden_mapping.json"

# seeded kind (truth.json) -> (file, keyword that must appear in the plan entry's `kind`)
DQ_KEYS = {
    ("accounts", "missing_name"): ("accounts.csv", "blank company_name"),
    ("accounts", "duplicate_source_id"): ("accounts.csv", "duplicate"),
    ("accounts", "bad_revenue"): ("accounts.csv", "annual_revenue"),
    ("contacts", "missing_email"): ("contacts.csv", "blank email"),
    ("contacts", "orphaned_account"): ("contacts.csv", "orphan"),
    ("cases", "garbage_priority"): ("cases.csv", "priority"),
    ("cases", "orphaned_account"): ("cases.csv", "orphan"),
    ("cases", "missing_subject"): ("cases.csv", "subject"),
}


def _norm(target):
    return None if target is None else str(target).strip().lower()


def load_golden(path=GOLDEN_PATH):
    return json.loads(Path(path).read_text())


def score_mappings(plan, golden):
    by_source = {m["source"]: m for m in plan.get("mappings", [])}
    threshold = golden["gates"]["confident_threshold"]
    rows, correct, confident_wrong = [], 0, []
    for g in golden["mappings"]:
        accept = {_norm(a) for a in g["accept"]}
        m = by_source.get(g["source"])
        if m is None:
            rows.append({"source": g["source"], "ok": False, "why": "missing from plan"})
            continue
        ok = _norm(m["target"]) in accept
        correct += ok
        row = {"source": g["source"], "ok": ok, "got": m["target"], "confidence": m["confidence"], "expected": g["accept"]}
        if not ok and m["confidence"] >= threshold:
            confident_wrong.append(row)
            row["why"] = "CONFIDENT wrong"
        elif not ok:
            row["why"] = "wrong but flagged for a human"
        rows.append(row)
    return {"accuracy": correct / len(golden["mappings"]), "correct": correct, "total": len(golden["mappings"]),
            "confident_wrong": confident_wrong, "rows": rows}


def score_dedupe(plan, truth):
    claimed = {d["acq_account_id"]: d["existing_external_id"] for d in plan.get("dedupe_matches", [])}
    want = {d["acq_account_id"]: d["matches_external_id"] for d in truth["near_duplicates"]}
    tp = sum(1 for k, v in want.items() if claimed.get(k) == v)
    precision = tp / len(claimed) if claimed else 1.0
    return {"precision": precision, "recall": tp / len(want), "claimed": len(claimed), "seeded": len(want),
            "missed": sorted(k for k in want if claimed.get(k) != want[k]),
            "wrong": sorted(k for k in claimed if claimed[k] != want.get(k))}


def score_data_quality(plan, truth):
    results = []
    for (section, kind), (file, keyword) in DQ_KEYS.items():
        ids = {r.get("acq_account_id") or r.get("acq_contact_id") or r.get("acq_case_id")
               for r in truth["bad_rows"][section] if r["kind"] == kind}
        expected = len(ids)
        entries = [q for q in plan.get("data_quality_issues", []) if q["file"] == file and keyword in q["kind"].lower()]
        got = entries[0].get("count") if entries else None
        results.append({"file": file, "kind": keyword, "expected": expected, "got": got, "ok": got == expected})
    return results


def evaluate(plan, golden, truth=None):
    g = golden["gates"]
    mappings = score_mappings(plan, golden)
    failures = []
    if mappings["accuracy"] < g["mapping_accuracy_min"]:
        failures.append(f"mapping accuracy {mappings['accuracy']:.0%} < {g['mapping_accuracy_min']:.0%}")
    if len(mappings["confident_wrong"]) > g["confident_wrong_max"]:
        failures.append(f"{len(mappings['confident_wrong'])} confident wrong mapping(s): "
                        + ", ".join(r["source"] for r in mappings["confident_wrong"]))
    report = {"mappings": mappings}
    if truth is not None:
        dedupe, dq = score_dedupe(plan, truth), score_data_quality(plan, truth)
        report.update(dedupe=dedupe, data_quality=dq)
        if dedupe["precision"] < g["dedupe_precision_min"]:
            failures.append(f"dedupe precision {dedupe['precision']:.0%} < {g['dedupe_precision_min']:.0%} (wrong: {dedupe['wrong']})")
        if dedupe["recall"] < g["dedupe_recall_min"]:
            failures.append(f"dedupe recall {dedupe['recall']:.0%} < {g['dedupe_recall_min']:.0%} (missed: {dedupe['missed']})")
        if g["data_quality_counts_exact"]:
            for r in dq:
                if not r["ok"]:
                    failures.append(f"data quality {r['file']} '{r['kind']}': expected {r['expected']}, got {r['got']}")
    report["failures"] = failures
    return report


def print_report(report):
    m = report["mappings"]
    print(f"Mappings: {m['correct']}/{m['total']} correct ({m['accuracy']:.0%}); confident wrong: {len(m['confident_wrong'])}")
    for r in m["rows"]:
        if not r["ok"]:
            print(f"  - {r['source']}: {r['why']} (got {r.get('got')!r} at {r.get('confidence')}, expected one of {r.get('expected')})")
    if "dedupe" in report:
        d = report["dedupe"]
        print(f"Dedupe: precision {d['precision']:.0%}, recall {d['recall']:.0%} ({d['claimed']} claimed, {d['seeded']} seeded); missed {d['missed']}")
        bad = [r for r in report["data_quality"] if not r["ok"]]
        print(f"Data quality: {len(report['data_quality']) - len(bad)}/{len(report['data_quality'])} kinds exact")
        for r in bad:
            print(f"  - {r['file']} '{r['kind']}': expected {r['expected']}, got {r['got']}")
    print("RESULT:", "PASS" if not report["failures"] else "FAIL")
    for f in report["failures"]:
        print("  gate failed:", f)


def summarize(plan, truth_path=None):
    """(report text, passed). Without a ground-truth file only the mapping gates run, and the text says so."""
    import contextlib
    import io

    truth = json.loads(Path(truth_path).read_text()) if truth_path and Path(truth_path).exists() else None
    report = evaluate(plan, load_golden(), truth)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_report(report)
    text = buf.getvalue()
    if truth is None:
        text = "(mappings only: no ground-truth file, dedupe and data-quality gates not run)\n" + text
    return text, not report["failures"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--truth", default="agent/data/truth/truth.json")
    ap.add_argument("--mappings-only", action="store_true")
    ap.add_argument("--golden", default=str(GOLDEN_PATH))
    args = ap.parse_args()
    plan = json.loads(Path(args.plan).read_text())
    truth = None
    if not args.mappings_only:
        if not Path(args.truth).exists():
            sys.exit(f"{args.truth} not found. Run `python agent/data/generate.py` first, or pass --mappings-only.")
        truth = json.loads(Path(args.truth).read_text())
    report = evaluate(plan, load_golden(args.golden), truth)
    print_report(report)
    sys.exit(1 if report["failures"] else 0)


if __name__ == "__main__":
    main()
