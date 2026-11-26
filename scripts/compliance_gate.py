#!/usr/bin/env python3
"""Compliance gate: block risky permissions and public read/write sharing.

Usage:
  python scripts/compliance_gate.py --diff origin/main...HEAD   # changed files only
  python scripts/compliance_gate.py --all                       # every file under force-app

Fails (exit 1) and prints file:line for each violation:
  - userPermissions enabled for ModifyAllData, ViewAllData, AuthorApex, ManageUsers
    in *.permissionset-meta.xml and *.profile-meta.xml
  - sharingModel set to ReadWrite on Account, Contact or Case in *.object-meta.xml
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

RISKY_PERMS = {"ModifyAllData", "ViewAllData", "AuthorApex", "ManageUsers"}
SHARING_OBJECTS = {"Account", "Contact", "Case"}

PERM_BLOCK = re.compile(r"<userPermissions>(.*?)</userPermissions>", re.DOTALL)
SHARING = re.compile(r"<(sharingModel|externalSharingModel)>\s*ReadWrite\s*</\1>")


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def check_permissions(path: Path, text: str):
    for m in PERM_BLOCK.finditer(text):
        block = m.group(1)
        name = re.search(r"<name>\s*(\w+)\s*</name>", block)
        enabled = re.search(r"<enabled>\s*true\s*</enabled>", block)
        if name and enabled and name.group(1) in RISKY_PERMS:
            yield f"{path}:{line_of(text, m.start())}: risky permission {name.group(1)} enabled"


def check_sharing(path: Path, text: str):
    # Account.object-meta.xml -> object name is the first part of the file name
    obj = path.name.split(".")[0]
    if obj not in SHARING_OBJECTS:
        return
    for m in SHARING.finditer(text):
        yield f"{path}:{line_of(text, m.start())}: {m.group(1)} set to ReadWrite on {obj}"


def changed_files(diff_range: str):
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", diff_range],
        check=True, capture_output=True, text=True,
    ).stdout
    return [Path(p) for p in out.splitlines() if p.strip()]


def all_files():
    return [p for p in Path("force-app").rglob("*") if p.is_file()]


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--diff", help="git range, e.g. origin/main...HEAD")
    g.add_argument("--all", action="store_true")
    args = ap.parse_args()

    files = all_files() if args.all else changed_files(args.diff)
    violations = []
    for p in files:
        if not p.exists():
            continue
        name = p.name
        if name.endswith((".permissionset-meta.xml", ".profile-meta.xml")):
            violations += list(check_permissions(p, p.read_text()))
        elif name.endswith(".object-meta.xml"):
            violations += list(check_sharing(p, p.read_text()))

    if violations:
        print("COMPLIANCE GATE FAILED")
        for v in violations:
            print("  " + v)
        return 1
    print(f"Compliance gate passed ({len(files)} file(s) checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
