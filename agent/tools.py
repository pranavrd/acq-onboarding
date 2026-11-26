"""Local (non-MCP) tools for the planning agent, plus plan validation and rendering.

Everything here is read-only and deterministic: no network, no writes outside the plan output.
"""
import csv
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

ALLOWED_FILES = {"accounts.csv", "contacts.csv", "cases.csv"}
# Mirrors the keys of CasePriorityNormalizer.cls; anything else falls through to the default priority.
KNOWN_PRIORITIES = {"p0", "p1", "p2", "p3", "p4", "sev0", "sev1", "sev2", "sev3", "sev4", "urgent", "critical",
                    "blocker", "high", "normal", "medium", "minor", "trivial", "low"}
CORP_WORDS = {"inc", "llc", "corp", "corporation", "ltd", "group", "holdings", "the", "co", "company"}
LOW_CONFIDENCE = 0.8


# ---------------------------------------------------------------- fuzzy matching
def normalize_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return " ".join(w for w in s.split() if w not in CORP_WORDS)


TOKEN_MIN = 0.8 # each word must have a close counterpart: typos pass, a different word does not


def similarity(a: str, b: str) -> float:
    """Whole-name ratio, capped at 0.6 when a word has no close counterpart in the other name.

    The cap stops "Lumen Materials" matching "Blue Materials" just because they share one word.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    score = SequenceMatcher(None, na, nb).ratio()
    short, long_ = sorted((na.split(), nb.split()), key=len)
    for tok in short:
        if max(SequenceMatcher(None, tok, other).ratio() for other in long_) < TOKEN_MIN:
            return min(score, 0.6)
    return score


# ---------------------------------------------------------------- local tools
class LocalTools:
    def __init__(self, input_dir: str):
        self.dir = Path(input_dir)

    def _path(self, file: str) -> Path:
        if file not in ALLOWED_FILES:  # also blocks path separators and other filenames
            raise ValueError(f"file must be one of {sorted(ALLOWED_FILES)}")
        return self.dir / file

    def _rows(self, file: str):
        with self._path(file).open(newline="") as f:
            return list(csv.DictReader(f))

    def read_csv_sample(self, file: str, n: int = 10):
        rows = self._rows(file)
        n = max(1, min(int(n), 20))
        return {"file": file, "headers": list(rows[0]) if rows else [], "total_rows": len(rows), "sample": rows[:n]}

    def column_values(self, file: str, column: str, limit: int = 40):
        rows = self._rows(file)
        if rows and column not in rows[0]:
            raise ValueError(f"unknown column {column!r}; columns are {list(rows[0])}")
        counts = Counter(r[column] for r in rows)
        limit = max(1, min(int(limit), 100))
        return {"file": file, "column": column, "distinct": len(counts),
                "blank": counts.get("", 0), "top": counts.most_common(limit)}

    def profile_quality(self):
        """Deterministic data-quality checks over all three exports: counts plus example ids, never guesses."""
        accounts, contacts, cases = (self._rows(f) for f in ("accounts.csv", "contacts.csv", "cases.csv"))
        account_ids = {r["acq_account_id"] for r in accounts}
        findings = []

        def add(file, kind, bad, id_field, **extra):
            if bad:
                findings.append({"file": file, "kind": kind, "count": len(bad),
                                 "example_ids": [r[id_field] for r in bad[:10]], **extra})

        def duplicates(rows, field):
            counts = Counter(r[field] for r in rows)
            dups = [k for k, n in counts.items() if n > 1]
            return [{field: k} for k in dups]

        def not_positive_number(v):
            try:
                return float(v) <= 0
            except (TypeError, ValueError):
                return True

        add("accounts.csv", "blank company_name", [r for r in accounts if not r["company_name"].strip()], "acq_account_id")
        add("accounts.csv", "duplicate acq_account_id (count = distinct ids that appear more than once)",
            duplicates(accounts, "acq_account_id"), "acq_account_id")
        add("accounts.csv", "annual_revenue is not a positive number", [r for r in accounts if not_positive_number(r["annual_revenue"])], "acq_account_id")
        add("accounts.csv", "employee_count is not a positive number", [r for r in accounts if not_positive_number(r["employee_count"])], "acq_account_id")
        add("accounts.csv", "blank tier", [r for r in accounts if not r["tier"].strip()], "acq_account_id")
        add("contacts.csv", "blank email", [r for r in contacts if not r["email"].strip()], "acq_contact_id")
        add("contacts.csv", "blank last_name", [r for r in contacts if not r["last_name"].strip()], "acq_contact_id")
        add("contacts.csv", "orphaned acq_account_id (no such account in accounts.csv)",
            [r for r in contacts if r["acq_account_id"] not in account_ids], "acq_contact_id")
        add("contacts.csv", "duplicate acq_contact_id", duplicates(contacts, "acq_contact_id"), "acq_contact_id")
        add("cases.csv", "blank subject", [r for r in cases if not r["subject"].strip()], "acq_case_id")
        add("cases.csv", "orphaned acq_account_id (no such account in accounts.csv)",
            [r for r in cases if r["acq_account_id"] not in account_ids], "acq_case_id")
        unknown = [r for r in cases if r["priority"].strip().lower() not in KNOWN_PRIORITIES]
        add("cases.csv", "unrecognized priority (the Apex normalizer defaults these to Medium)", unknown, "acq_case_id",
            values=dict(Counter(r["priority"] for r in unknown)))
        add("cases.csv", "duplicate acq_case_id", duplicates(cases, "acq_case_id"), "acq_case_id")
        return {"rows": {"accounts": len(accounts), "contacts": len(contacts), "cases": len(cases)}, "findings": findings}

    def fuzzy_match(self, existing_accounts, threshold: float = 0.85):
        """For each acquired account, the best match among existing org accounts at/above threshold.

        existing_accounts: [{"external_id": "...", "name": "..."}] taken from a SOQL query.
        """
        threshold = max(0.5, min(float(threshold), 1.0))
        out = []
        for row in self._rows("accounts.csv"):
            name = row.get("company_name", "")
            if not name.strip():
                continue
            best = max(existing_accounts, key=lambda e: similarity(name, e["name"]), default=None)
            if best is None:
                continue
            score = similarity(name, best["name"])
            if score >= threshold:
                out.append({"acq_account_id": row["acq_account_id"], "acq_name": name,
                            "existing_external_id": best["external_id"], "existing_name": best["name"],
                            "similarity": round(score, 3)})
        out.sort(key=lambda m: -m["similarity"])
        return {"threshold": threshold, "matches": out[:100], "total_matches": len(out)}


LOCAL_TOOL_DEFS = [
    {"name": "read_csv_sample",
     "description": "Read the headers, total row count and the first n rows (max 20) of one acquired-company export.",
     "input_schema": {"type": "object", "properties": {
         "file": {"type": "string", "enum": sorted(ALLOWED_FILES)},
         "n": {"type": "integer", "description": "rows to sample, 1-20"}}, "required": ["file"]}},
    {"name": "column_values",
     "description": "Distinct values and counts for one column of an export. Use it to see messy categorical columns such as tier, priority, status.",
     "input_schema": {"type": "object", "properties": {
         "file": {"type": "string", "enum": sorted(ALLOWED_FILES)},
         "column": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["file", "column"]}},
    {"name": "profile_quality",
     "description": "Run deterministic data-quality checks over all three exports: blanks, duplicate ids, orphaned account ids, "
                    "non-numeric numbers, unrecognized priorities. Returns exact counts and example ids. Use it for the data_quality_issues "
                    "section instead of estimating from samples.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "fuzzy_match",
     "description": "Deterministic name matching of every acquired account against the existing org accounts you pass in. "
                    "Pass the accounts you got from SOQL as [{external_id, name}]. Returns candidate duplicates with similarity scores; you decide which are real.",
     "input_schema": {"type": "object", "properties": {
         "existing_accounts": {"type": "array", "items": {"type": "object", "properties": {
             "external_id": {"type": "string"}, "name": {"type": "string"}}, "required": ["external_id", "name"]}},
         "threshold": {"type": "number", "description": "0.5-1.0, default 0.85"}}, "required": ["existing_accounts"]}},
]

SUBMIT_PLAN_DEF = {
    "name": "submit_plan",
    "description": "Submit the final mapping and dedupe plan. Call exactly once, when you are done investigating.",
    "input_schema": {"type": "object", "properties": {
        "mappings": {"type": "array", "description": "One entry per source column in every export.", "items": {"type": "object", "properties": {
            "source": {"type": "string", "description": "file.column, e.g. accounts.company_name"},
            "target": {"type": ["string", "null"], "description": "Object.Field in the org, or null if there is no field"},
            "transform": {"type": "string", "description": "how the value is converted, or 'none'"},
            "confidence": {"type": "number", "description": "0.0-1.0"},
            "reasoning": {"type": "string"}}, "required": ["source", "target", "transform", "confidence", "reasoning"]}},
        "dedupe_matches": {"type": "array", "items": {"type": "object", "properties": {
            "acq_account_id": {"type": "string"}, "existing_external_id": {"type": "string"},
            "confidence": {"type": "number"}, "reasoning": {"type": "string"}},
            "required": ["acq_account_id", "existing_external_id", "confidence", "reasoning"]}},
        "proposed_new_fields": {"type": "array", "items": {"type": "object", "properties": {
            "object": {"type": "string"}, "field_api_name": {"type": "string"}, "type": {"type": "string"},
            "reason": {"type": "string"}}, "required": ["object", "field_api_name", "type", "reason"]}},
        "data_quality_issues": {"type": "array", "description": "One entry per kind of problem. Copy count and example ids from profile_quality; do not estimate.", "items": {"type": "object", "properties": {
            "file": {"type": "string"}, "kind": {"type": "string"},
            "count": {"type": "integer", "description": "how many rows are affected, from profile_quality"},
            "row_ids": {"type": "array", "items": {"type": "string"}, "description": "up to 10 example ids"},
            "action": {"type": "string", "description": "reject, fix, or needs_human"}},
            "required": ["file", "kind", "count", "row_ids", "action"]}},
        "notes": {"type": "string"}},
        "required": ["mappings", "dedupe_matches", "proposed_new_fields", "data_quality_issues", "notes"]},
}


# ---------------------------------------------------------------- plan handling
def validate_plan(plan) -> list[str]:
    errors = []
    if not isinstance(plan, dict):
        return ["plan must be an object"]
    for key in SUBMIT_PLAN_DEF["input_schema"]["required"]:
        if key not in plan:
            errors.append(f"missing key {key!r}")
    for section in ("mappings", "dedupe_matches"):
        for i, item in enumerate(plan.get(section) or []):
            c = item.get("confidence") if isinstance(item, dict) else None
            if not isinstance(c, (int, float)) or isinstance(c, bool) or not 0 <= c <= 1:
                errors.append(f"{section}[{i}].confidence must be a number between 0 and 1")
    if not plan.get("mappings"):
        errors.append("mappings must not be empty")
    return errors


def _issue_ref(q: dict) -> str:
    ids = ", ".join(q.get("row_ids") or [])
    n = q.get("count")
    return f"{q.get('file', '?')} ({n} rows: {ids})" if n is not None and ids else (q.get("row_id") or q.get("file", "?"))


def finalize_plan(plan: dict) -> dict:
    """Add the 'needs human' section. Computed here, not by the model, so the threshold can't be talked around."""
    needs = []
    for m in plan["mappings"]:
        if m["confidence"] < LOW_CONFIDENCE or m["target"] is None:
            needs.append({"kind": "mapping", "ref": m["source"], "confidence": m["confidence"],
                          "why": "no target field" if m["target"] is None else "low confidence"})
    for d in plan["dedupe_matches"]:
        if d["confidence"] < LOW_CONFIDENCE:
            needs.append({"kind": "dedupe", "ref": d["acq_account_id"], "confidence": d["confidence"],
                          "why": "uncertain match"})
    for q in plan["data_quality_issues"]:
        if q["action"] == "needs_human":
            needs.append({"kind": "data_quality", "ref": _issue_ref(q), "confidence": None, "why": q["kind"]})
    return {**plan, "needs_human": needs, "low_confidence_threshold": LOW_CONFIDENCE}


def render_markdown(plan: dict, run_id: str, meta: dict) -> str:
    lines = [f"# Onboarding plan `{run_id}`", "",
             f"Model: `{meta.get('model')}` | tool calls: {meta.get('tool_calls')} | "
             f"tokens in/out: {meta.get('input_tokens')}/{meta.get('output_tokens')}", "",
             "Read-only plan. Nothing was written to any org.", ""]
    nh = plan["needs_human"]
    lines += [f"## Needs a human ({len(nh)})", ""]
    lines += [f"- **{n['kind']}** `{n['ref']}`: {n['why']}" + (f" (confidence {n['confidence']})" if n["confidence"] is not None else "") for n in nh] or ["None."]
    lines += ["", "## Field mappings", "", "| Source | Target | Transform | Confidence | Reasoning |", "|---|---|---|---|---|"]
    for m in plan["mappings"]:
        lines.append(f"| `{m['source']}` | `{m['target']}` | {m['transform']} | {m['confidence']} | {m['reasoning']} |")
    lines += ["", f"## Duplicates of existing accounts ({len(plan['dedupe_matches'])})", ""]
    lines += [f"- `{d['acq_account_id']}` -> `{d['existing_external_id']}` ({d['confidence']}): {d['reasoning']}" for d in plan["dedupe_matches"][:50]]
    if len(plan["dedupe_matches"]) > 50:
        lines.append(f"- ... and {len(plan['dedupe_matches']) - 50} more in plan.json")
    lines += ["", "## Proposed new fields", ""]
    lines += [f"- `{f['object']}.{f['field_api_name']}` ({f['type']}): {f['reason']}" for f in plan["proposed_new_fields"]] or ["None."]
    lines += ["", f"## Data quality issues ({len(plan['data_quality_issues'])})", ""]
    lines += [f"- `{q['file']}`: {q['kind']}" + (f" ({q['count']} rows; e.g. {', '.join(q.get('row_ids') or [])})" if q.get("count") is not None else "")
              + f" -> {q['action']}" for q in plan["data_quality_issues"][:50]]
    if len(plan["data_quality_issues"]) > 50:
        lines.append(f"- ... and {len(plan['data_quality_issues']) - 50} more in plan.json")
    lines += ["", "## Notes", "", plan["notes"], ""]
    return "\n".join(lines)
