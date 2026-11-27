"""Pure transform for the load step: approved plan + exports -> Bulk-ready rows, with every rejection explained.

No `sf` calls and no network here, so it is tested offline against the seeded ground truth.
Decisions implemented (see DECISIONS.md):
  1. pending_customer cases load as Working
  2. tier loads raw into Account.Customer_Tier__c (only if the approved plan maps it there)
  3. every row sharing a source id is rejected, and so are the rows that point at it
  4. near-duplicates of existing Core accounts are merged: not inserted, their contacts/cases re-parented
"""
import csv
from collections import Counter
from pathlib import Path

from tools import KNOWN_PRIORITIES

SOURCE_SYSTEM = "AcquiredCo"
FILES = ("accounts", "contacts", "cases")
OBJECT = {"accounts": "Account", "contacts": "Contact", "cases": "Case"}

# Targets the executor has a rule for. A plan that maps anything else is refused, not guessed at.
SUPPORTED = {
    "Account.External_Id__c", "Account.Name", "Account.Industry", "Account.Customer_Tier__c", "Account.Website",
    "Account.BillingCity", "Account.BillingCountry", "Account.AnnualRevenue", "Account.NumberOfEmployees",
    "Contact.External_Id__c", "Contact.AccountId", "Contact.FirstName", "Contact.LastName", "Contact.Email",
    "Contact.Phone", "Contact.Title",
    "Case.External_Id__c", "Case.AccountId", "Case.Subject", "Case.Source_Priority__c", "Case.Status",
}
NOT_LOADED = {"Case.CreatedDate": "setting CreatedDate needs the Set Audit Fields permission; not loaded"}
REQUIRED = {
    "accounts": {"Account.External_Id__c", "Account.Name"},
    "contacts": {"Contact.External_Id__c", "Contact.AccountId", "Contact.LastName"},
    "cases": {"Case.External_Id__c", "Case.AccountId", "Case.Subject", "Case.Status"},
}
ID_TARGET = {f: f"{OBJECT[f]}.External_Id__c" for f in FILES}
PARENT_TARGET = {"contacts": "Contact.AccountId", "cases": "Case.AccountId"}
STATUS_MAP = {"open": "New", "in_progress": "Working", "pending_customer": "Working", "escalated": "Escalated"}


class PlanError(RuntimeError):
    """The approved plan cannot be executed as written."""


def resolve_mapping(plan: dict):
    """-> (loadable {source: target}, not_loaded [{source, reason}]). Raises PlanError for anything unsafe."""
    loadable, not_loaded = {}, []
    for m in plan["mappings"]:
        src, target = m["source"], m["target"]
        file = src.split(".", 1)[0]
        if file not in FILES:
            raise PlanError(f"plan maps unknown source {src!r}")
        if target is None:
            not_loaded.append({"source": src, "reason": "the approved plan has no target field for it"})
        elif target in NOT_LOADED:
            not_loaded.append({"source": src, "reason": NOT_LOADED[target]})
        elif target in SUPPORTED:
            if not target.startswith(OBJECT[file] + "."):
                raise PlanError(f"plan maps {src} to {target}, which is on a different object")
            loadable[src] = target
        else:
            raise PlanError(f"plan maps {src} to {target}, and the executor has no rule for that field")
    for file, required in REQUIRED.items():
        have = {t for s, t in loadable.items() if s.startswith(file + ".")}
        if required - have:
            raise PlanError(f"plan does not map required field(s) for {file}: {sorted(required - have)}")
    targets = list(loadable.values())
    dup = [t for t, n in Counter(targets).items() if n > 1]
    if dup:
        raise PlanError(f"plan maps more than one column to {dup}")
    return loadable, not_loaded


def _col(loadable, file, target):
    return next((s.split(".", 1)[1] for s, t in loadable.items() if s.startswith(file + ".") and t == target), None)


def _convert(target, value):
    """-> (loaded value, warning or None). Raises KeyError for a Case.Status value with no mapping."""
    v = (value or "").strip()
    if target == "Account.Name":
        return " ".join(v.split()), None
    if target in ("Account.AnnualRevenue", "Account.NumberOfEmployees"):
        try:
            n = float(v)
        except ValueError:
            n = 0
        if n <= 0:
            return "", "not a positive number; loaded blank"
        return str(int(n)), None
    if target == "Case.Status":
        mapped = STATUS_MAP[v.lower()]
        return mapped, ("pending_customer loaded as Working" if v.lower() == "pending_customer" else None)
    if target == "Case.Source_Priority__c":
        return v, (None if v.lower() in KNOWN_PRIORITIES else "unrecognized priority; the trigger defaults it to Medium")
    return v, None


def _shared(rows, id_col):
    c = Counter(r[id_col] for r in rows)
    return {i for i, n in c.items() if n > 1}


def transform(plan: dict, accounts: list, contacts: list, cases: list) -> dict:
    loadable, not_loaded = resolve_mapping(plan)
    data = {"accounts": accounts, "contacts": contacts, "cases": cases}
    out = {f: [] for f in FILES}
    rejected, warnings = [], []
    dedupe = {d["acq_account_id"]: d["existing_external_id"] for d in plan.get("dedupe_matches", [])}
    reparented = {"contacts": 0, "cases": 0}

    def reject(file, id_, reason):
        rejected.append({"file": file, "id": id_, "reason": reason})

    def build(file, row, id_):
        o = {}
        for src, target in loadable.items():
            if not src.startswith(file + "."):
                continue
            col = src.split(".", 1)[1]
            if target == PARENT_TARGET.get(file):
                continue  # relationship column is added by the caller
            value, warn = _convert(target, row[col])
            o[target.split(".", 1)[1]] = value
            if warn:
                warnings.append({"file": f"{file}.csv", "id": id_, "warning": f"{col}: {warn}"})
        o["Source_System__c"] = SOURCE_SYSTEM
        return o

    # ---- accounts
    id_col = _col(loadable, "accounts", ID_TARGET["accounts"])
    name_col = _col(loadable, "accounts", "Account.Name")
    shared = _shared(accounts, id_col)
    all_account_ids = {r[id_col] for r in accounts}
    rejected_account_ids, merged = set(), {}
    for r in accounts:
        id_ = r[id_col]
        if not id_.strip():
            reject("accounts.csv", id_, "blank source id")
        elif id_ in shared:
            reject("accounts.csv", id_, "source id shared by more than one row")
            rejected_account_ids.add(id_)
        elif not r[name_col].strip():
            reject("accounts.csv", id_, "blank company name")
            rejected_account_ids.add(id_)
        elif id_ in dedupe:
            merged[id_] = dedupe[id_]
        else:
            out["accounts"].append(build("accounts", r, id_))

    # ---- contacts and cases
    for file, rows in (("contacts", contacts), ("cases", cases)):
        fid = _col(loadable, file, ID_TARGET[file])
        parent_col = _col(loadable, file, PARENT_TARGET[file])
        required_text = _col(loadable, file, "Contact.LastName" if file == "contacts" else "Case.Subject")
        shared_ids = _shared(rows, fid)
        for r in rows:
            id_, parent = r[fid], r[parent_col]
            if not id_.strip():
                reject(f"{file}.csv", id_, "blank source id")
            elif id_ in shared_ids:
                reject(f"{file}.csv", id_, "source id shared by more than one row")
            elif parent not in all_account_ids:
                reject(f"{file}.csv", id_, "orphaned: parent account does not exist in the export")
            elif parent in rejected_account_ids:
                reject(f"{file}.csv", id_, "parent rejected")
            elif not r[required_text].strip():
                reject(f"{file}.csv", id_, "blank " + ("last name" if file == "contacts" else "subject"))
            else:
                try:
                    o = build(file, r, id_)
                except KeyError as e:
                    reject(f"{file}.csv", id_, f"unmapped status {e.args[0]!r}")
                    continue
                if parent in merged:
                    reparented[file] += 1
                o["Account.External_Id__c"] = merged.get(parent, parent)
                out[file].append(o)

    return {"rows": out, "rejected": rejected, "warnings": warnings, "not_loaded": not_loaded,
            "merged": {"accounts": merged, "contacts_reparented": reparented["contacts"], "cases_reparented": reparented["cases"]},
            "in": {f: len(data[f]) for f in FILES}}


def read_exports(input_dir):
    d = Path(input_dir)
    out = []
    for f in FILES:
        with (d / f"{f}.csv").open(newline="") as fh:
            out.append(list(csv.DictReader(fh)))
    return out


def write_load_files(result: dict, out_dir) -> dict:
    """Write LF-terminated CSVs for the Bulk API. Returns {file: path}."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths = {}
    for f in FILES:
        rows = result["rows"][f]
        if not rows:
            continue
        fields = list(rows[0])
        p = d / f"{f}.csv"
        with p.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        paths[f] = str(p)
    return paths
