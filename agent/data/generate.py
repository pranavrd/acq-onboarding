#!/usr/bin/env python3
"""Synthetic "AcquiredCo" exports with seeded problems. Standard library only; deterministic.

Writes to --out (what the planning agent may read):
  accounts.csv  contacts.csv  cases.csv
Writes to --truth (what the agent must NOT read; used for evals and reconciliation checks):
  existing_accounts.csv  (the ~50 "Core" customers to load into the scratch org first)
  truth.json             (every seeded problem, by id)

Seeded problems:
  - 5% of accounts are near-duplicates of existing Core accounts (name variants)
  - 3% bad rows per file (missing name / duplicate source id / bad revenue; missing email /
    orphaned account; garbage priority / orphaned account / missing subject)
  - `tier` values that do not map cleanly, and a column (`legacy_health_score`) the org has no field for
"""
import argparse
import csv
import json
import random
import re
from pathlib import Path

ADJ = ["Acme", "Apex", "Blue", "Bright", "Cedar", "Crimson", "Delta", "Eagle", "Falcon", "Granite",
       "Harbor", "Iron", "Juniper", "Keystone", "Lumen", "Maple", "Nimbus", "Orion", "Pioneer", "Quartz",
       "Redwood", "Summit", "Titan", "Umbra", "Vertex", "Willow", "Xenon", "Yonder", "Zenith", "Northwind"]
NOUN = ["Systems", "Logistics", "Dynamics", "Robotics", "Analytics", "Foods", "Energy", "Health", "Media",
        "Networks", "Labs", "Capital", "Freight", "Software", "Materials", "Devices", "Partners", "Works",
        "Biotech", "Aerospace", "Retail", "Marine", "Mining", "Textiles", "Security"]
SUFFIX = ["Inc", "LLC", "Corp", "Ltd", "Group", "Holdings"]
INDUSTRY = ["Technology", "Healthcare", "Finance", "Manufacturing", "Retail", "Energy", "Transportation", "Media"]
CITY = [("Austin", "US"), ("Seattle", "US"), ("Boston", "US"), ("Toronto", "CA"), ("London", "GB"),
        ("Berlin", "DE"), ("Pune", "IN"), ("Singapore", "SG"), ("Sydney", "AU"), ("Denver", "US")]
FIRST = ["Alex", "Priya", "Jordan", "Maya", "Chen", "Sam", "Elena", "Ravi", "Noah", "Zoe", "Omar", "Lena",
         "Kai", "Nina", "Arjun", "Tess", "Luca", "Mira", "Dev", "Iris"]
LAST = ["Nguyen", "Patel", "Garcia", "Kim", "Singh", "Rossi", "Walker", "Cohen", "Ivanov", "Okafor",
        "Silva", "Brown", "Tanaka", "Haddad", "Larsen", "Moreno", "Shah", "Reyes", "Dubois", "Frost"]
# Deliberately messy: some map cleanly to a (not yet existing) Tier field, most do not.
TIERS = ["Gold", "Silver", "Bronze", "Platinum", "Tier 1", "T2", "VIP", "Enterprise", "SMB", "strategic", ""]
# Raw priorities from the acquired company's system.
PRIORITIES = ["P0", "P1", "P2", "P3", "Urgent", "sev1", "sev2", "sev3", "Critical", "Normal", "Low"]
GARBAGE_PRIORITIES = ["???", "banana", "ASAP!!", "tbd"]
STATUSES = ["open", "in_progress", "pending_customer", "escalated"]
SUBJECTS = ["Cannot log in to portal", "Invoice total looks wrong", "Data export failing", "Slow dashboard",
            "Need to add a user", "API returns 500", "Password reset loop", "Feature request: bulk edit",
            "Integration stopped syncing", "Billing address change"]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())[:20] or "example"


def variant(name: str, rng: random.Random) -> str:
    """A near-duplicate spelling of an existing account name."""
    base, _, suffix = name.rpartition(" ")
    choice = rng.randrange(4)
    if choice == 0:
        return f"{base.upper()} {'CORPORATION' if suffix == 'Corp' else suffix.upper()}, INC."
    if choice == 1:
        return f"The {base} {suffix}."
    if choice == 2:
        return f"{base}  {suffix}"  # double space
    i = rng.randrange(1, max(2, len(base) - 1))
    return base[:i] + base[i + 1] + base[i] + base[i + 2:] + f" {suffix}"  # swapped letters


def write_csv(path: Path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")  # Bulk API expects LF
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="agent/data/out")
    ap.add_argument("--truth", default="agent/data/truth")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--accounts", type=int, default=500)
    ap.add_argument("--contacts", type=int, default=1000)
    ap.add_argument("--cases", type=int, default=800)
    ap.add_argument("--existing", type=int, default=50)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    out, truth_dir = Path(args.out), Path(args.truth)

    bases = [f"{a} {n}" for a in ADJ for n in NOUN]
    rng.shuffle(bases)
    n_dup = round(args.accounts * 0.05)
    n_bad_acct = round(args.accounts * 0.03)

    # ---- existing Core customers (loaded into the scratch org first so dedupe has targets)
    existing = []
    for i in range(args.existing):
        name = f"{bases[i]} {rng.choice(SUFFIX)}"
        city, country = rng.choice(CITY)
        existing.append({"Name": name, "External_Id__c": f"CORE-{i+1:05d}", "Source_System__c": "Core",
                         "Industry": rng.choice(INDUSTRY), "BillingCity": city, "BillingCountry": country})

    # ---- acquired accounts
    accounts, truth = [], {"near_duplicates": [], "bad_rows": {"accounts": [], "contacts": [], "cases": []}}
    dup_targets = rng.sample(existing, n_dup)
    new_bases = iter(bases[args.existing:])
    for i in range(args.accounts):
        acq_id = f"ACQ-A-{i+1:05d}"
        if i < n_dup:
            target = dup_targets[i]
            name = variant(target["Name"], rng)
            truth["near_duplicates"].append({"acq_account_id": acq_id, "acq_name": name,
                                             "matches_external_id": target["External_Id__c"],
                                             "existing_name": target["Name"]})
        else:
            name = f"{next(new_bases)} {rng.choice(SUFFIX)}"
        city, country = rng.choice(CITY)
        accounts.append({
            "acq_account_id": acq_id, "company_name": name, "industry": rng.choice(INDUSTRY),
            "tier": rng.choice(TIERS), "website": f"https://www.{slug(name)}.example",
            "billing_city": city, "billing_country": country,
            "annual_revenue": rng.randrange(1, 500) * 100000, "employee_count": rng.randrange(10, 20000),
            "legacy_health_score": rng.randrange(0, 101),
        })
    rng.shuffle(accounts)  # duplicates should not all sit at the top

    # bad account rows (never pick a duplicate row, so the two seeds stay separable)
    dup_ids = {d["acq_account_id"] for d in truth["near_duplicates"]}
    candidates = [a for a in accounts if a["acq_account_id"] not in dup_ids]
    bad = rng.sample(candidates, n_bad_acct)
    kinds = ["missing_name", "duplicate_source_id", "bad_revenue"]
    for k, row in enumerate(bad):
        kind = kinds[k % 3]
        if kind == "missing_name":
            row["company_name"] = ""
        elif kind == "duplicate_source_id":
            other = rng.choice([a for a in accounts if a is not row])
            row["acq_account_id"] = other["acq_account_id"]
        else:
            row["annual_revenue"] = rng.choice(["N/A", "-1", "tbd"])
        truth["bad_rows"]["accounts"].append({"acq_account_id": row["acq_account_id"], "kind": kind,
                                              "company_name": row["company_name"]})
    good_ids = [a["acq_account_id"] for a in accounts]

    # ---- contacts
    contacts = []
    for i in range(args.contacts):
        acct = rng.choice(good_ids)
        first, last = rng.choice(FIRST), rng.choice(LAST)
        domain = slug(next(a["company_name"] for a in accounts if a["acq_account_id"] == acct) or "example")
        contacts.append({"acq_contact_id": f"ACQ-C-{i+1:05d}", "acq_account_id": acct, "first_name": first,
                         "last_name": last, "email": f"{first}.{last}{i}@{domain}.example".lower(),
                         "phone": f"+1-555-{rng.randrange(1000, 9999)}", "title": rng.choice(["CTO", "Admin", "Buyer", "Engineer"])})
    n_bad_c = round(args.contacts * 0.03)
    for k, row in enumerate(rng.sample(contacts, n_bad_c)):
        kind = "missing_email" if k % 2 == 0 else "orphaned_account"
        if kind == "missing_email":
            row["email"] = ""
        else:
            row["acq_account_id"] = f"ACQ-A-9{rng.randrange(1000, 9999)}"
        truth["bad_rows"]["contacts"].append({"acq_contact_id": row["acq_contact_id"], "kind": kind})

    # ---- cases
    cases = []
    for i in range(args.cases):
        acct = rng.choice(good_ids)
        cases.append({"acq_case_id": f"ACQ-K-{i+1:05d}", "acq_account_id": acct, "subject": rng.choice(SUBJECTS),
                      "priority": rng.choice(PRIORITIES), "status": rng.choice(STATUSES),
                      "created_date": f"2026-{rng.randrange(8, 12):02d}-{rng.randrange(1, 28):02d}"})
    n_bad_k = round(args.cases * 0.03)
    for k, row in enumerate(rng.sample(cases, n_bad_k)):
        kind = ["garbage_priority", "orphaned_account", "missing_subject"][k % 3]
        if kind == "garbage_priority":
            row["priority"] = rng.choice(GARBAGE_PRIORITIES)
        elif kind == "orphaned_account":
            row["acq_account_id"] = f"ACQ-A-9{rng.randrange(1000, 9999)}"
        else:
            row["subject"] = ""
        truth["bad_rows"]["cases"].append({"acq_case_id": row["acq_case_id"], "kind": kind})

    truth["unmappable_columns"] = {"accounts": ["legacy_health_score"]}
    truth["ambiguous_values"] = {"accounts.tier": sorted(set(TIERS) - {""})}
    truth["counts"] = {"accounts": len(accounts), "contacts": len(contacts), "cases": len(cases),
                       "existing_core_accounts": len(existing)}

    write_csv(out / "accounts.csv", list(accounts[0]), accounts)
    write_csv(out / "contacts.csv", list(contacts[0]), contacts)
    write_csv(out / "cases.csv", list(cases[0]), cases)
    write_csv(truth_dir / "existing_accounts.csv", list(existing[0]), existing)
    (truth_dir / "truth.json").write_text(json.dumps(truth, indent=2))
    print(json.dumps(truth["counts"]), "| near-dups:", len(truth["near_duplicates"]),
          "| bad rows:", {k: len(v) for k, v in truth["bad_rows"].items()})


if __name__ == "__main__":
    main()
