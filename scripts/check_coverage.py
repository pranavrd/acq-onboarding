#!/usr/bin/env python3
"""Fail if org-wide Apex coverage is below a threshold.

Reads the JSON that `sf apex run test --result-format json --output-dir DIR` wrote.
Usage: python scripts/check_coverage.py test-results 85
"""
import json
import sys
from pathlib import Path


def find_summary(directory: Path):
    for f in sorted(directory.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        summary = data.get("summary") or data.get("result", {}).get("summary")
        if summary and "orgWideCoverage" in summary:
            return summary
    return None


def main() -> int:
    directory, threshold = Path(sys.argv[1]), float(sys.argv[2])
    summary = find_summary(directory)
    if summary is None:
        print(f"No orgWideCoverage found in {directory}")
        return 1
    coverage = float(str(summary["orgWideCoverage"]).rstrip("%"))
    print(f"Org-wide coverage: {coverage}% (required {threshold}%)")
    print(f"Tests: {summary.get('testsRan')} ran, {summary.get('passing')} passing, {summary.get('failing')} failing")
    if coverage < threshold or str(summary.get("outcome", "Passed")) != "Passed":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
