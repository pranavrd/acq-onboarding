#!/usr/bin/env python3
"""Planning agent (read-only). Reads the exports and the org, proposes a mapping and dedupe plan.

  python agent/plan.py --input agent/data/out --org dev-scratch

Safety properties (each one is checked by a test in agent/tests/test_plan.py):
  - The model only sees read-only tools: three local CSV/matching tools, three allowlisted MCP tools, submit_plan.
  - Every call is checked against the allowlist again at dispatch time, and denied calls are audited.
  - SOQL must start with SELECT.
  - Every call, allowed or denied, is written to the append-only audit log.
  - "Needs human" is computed in code from the confidence threshold, not by the model.
Writes plans/<run_id>/plan.json and plan.md. It never writes to an org.
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import AuditLog  # noqa: E402
from tools import (LOCAL_TOOL_DEFS, SUBMIT_PLAN_DEF, LocalTools, finalize_plan,  # noqa: E402
                   render_markdown, validate_plan)

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_RESULT_CHARS = 20000
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """You plan the migration of an acquired company's CRM exports into a Salesforce org. You are read-only: you investigate and propose; a human approves and a separate step loads data.

Exports (CSV): accounts.csv, contacts.csv, cases.csv. Use read_csv_sample and column_values to look at them.
Org: use the org alias given in the user message. Use run_soql_query to read it. Useful queries:
  - Existing customers: SELECT Name, External_Id__c, Source_System__c FROM Account WHERE Source_System__c = 'Core'
  - Fields on an object: SELECT QualifiedApiName, DataType, Label FROM FieldDefinition WHERE EntityDefinition.QualifiedApiName = 'Account'
  - Allowed values of a picklist (verify before mapping to one; never guess a value):
    SELECT Value FROM PicklistValueInfo WHERE EntityParticle.EntityDefinition.QualifiedApiName = 'Case' AND EntityParticle.QualifiedApiName = 'Status'
    Do this for every picklist you map to: Case Status, Account Industry, Source_System__c. (FieldDefinition has no IsCustom column and does not support COUNT.)
The org already has External_Id__c and Source_System__c on Account, Contact and Case, and Source_Priority__c on Case. It has no field for customer tier.
Case Priority must end up as High, Medium or Low (a trigger normalizes Source_Priority__c). Unknown priorities default to Medium and must be flagged.

Do this, in order:
1. Look at every export (headers, a sample, and the messy categorical columns).
2. Describe the target objects with FieldDefinition and read the existing accounts.
3. Call fuzzy_match with the existing accounts. Judge each candidate yourself; keep real duplicates, drop lookalikes.
4. Map EVERY source column. If the org has no field for a column, set target to null and say so; propose a new field only when it clearly has business value.
5. Call profile_quality once. Turn every finding into one data_quality_issues entry, copying its count and example ids exactly; add your own entries only for things you verified with a tool. Choose the action: reject (row cannot be loaded), fix (a rule can repair it), needs_human (a decision is needed).
6. Call submit_plan once.

Rules: never invent a field that does not exist; if a mapping is a guess, give it a low confidence (below 0.8) instead of a confident-sounding one; confidence is your honest probability the mapping is right."""


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + f"\n...[truncated {len(text) - MAX_RESULT_CHARS} chars]"


# MCP arguments the model never chooses: which org, which directory, and Tooling API access.
# They are removed from the schema the model sees and set (or dropped) in code.
HIDDEN_MCP_ARGS = {"usernameOrAlias", "directory", "useToolingApi"}


def hide_args(tool_defs, hidden=HIDDEN_MCP_ARGS):
    out = []
    for d in tool_defs:
        schema = dict(d["input_schema"])
        schema["properties"] = {k: v for k, v in schema.get("properties", {}).items() if k not in hidden}
        schema["required"] = [k for k in schema.get("required", []) if k not in hidden]
        out.append({**d, "input_schema": schema})
    return out


class Dispatcher:
    """Single choke point for every tool call: allowlist, guards, audit."""

    def __init__(self, local: LocalTools, mcp_call, mcp_allowed, audit: AuditLog, pinned=None):
        self.local, self.mcp_call, self.mcp_allowed, self.audit = local, mcp_call, set(mcp_allowed), audit
        self.pinned = dict(pinned or {})
        self.local_names = {d["name"] for d in LOCAL_TOOL_DEFS}
        self.plan = None
        self.calls = 0

    def _log(self, step, name, args, summary, decision):
        self.audit.record(step, name, args, summary, decision)

    async def __call__(self, step: str, name: str, args: dict):
        """Returns (result_text, is_error)."""
        self.calls += 1
        if name == "submit_plan":
            errors = validate_plan(args)
            if errors:
                self._log(step, name, {"keys": sorted(args)}, "; ".join(errors), "rejected: invalid plan")
                return "Plan rejected: " + "; ".join(errors), True
            self.plan = finalize_plan(args)
            self._log(step, name, {"mappings": len(args["mappings"]), "dedupe": len(args["dedupe_matches"])},
                      f"needs_human={len(self.plan['needs_human'])}", "plan accepted")
            return "Plan accepted.", False
        if name in self.local_names:
            try:
                result = getattr(self.local, name)(**args)
            except (ValueError, TypeError, KeyError) as e:
                self._log(step, name, args, str(e), "error")
                return f"Error: {e}", True
            text = json.dumps(result)
            self._log(step, name, args, text[:200], "allowed")
            return _truncate(text), False
        if name in self.mcp_allowed:
            if name == "run_soql_query":
                q = args.get("query")
                if not isinstance(q, str) or not re.match(r"\s*SELECT\b", q, re.IGNORECASE):
                    self._log(step, name, args, "query must be a SELECT", "denied: not a SELECT")
                    return "Denied: only SELECT queries are allowed.", True
            overridden = sorted(k for k in args if k in HIDDEN_MCP_ARGS)
            sent = {**{k: v for k, v in args.items() if k not in HIDDEN_MCP_ARGS}, **self.pinned}
            text, is_error = await self.mcp_call(name, sent)
            decision = "error" if is_error else "allowed"
            if overridden:
                decision += f" (ignored model-supplied {overridden})"
            self._log(step, name, sent, text[:200], decision)
            return _truncate(text), is_error
        self._log(step, name, args, "tool is not on the allowlist", "denied: not on allowlist")
        return f"Denied: {name} is not an available tool.", True


async def agent_loop(client, model, tools, dispatch: Dispatcher, user_prompt, max_turns=30, fallbacks=True):
    """Returns usage dict. dispatch.plan is set when the model submits a valid plan."""
    messages = [{"role": "user", "content": user_prompt}]
    usage = {"input_tokens": 0, "output_tokens": 0, "turns": 0}
    nudged = False
    for turn in range(1, max_turns + 1):
        kwargs = dict(model=model, max_tokens=16000, system=SYSTEM, tools=tools, messages=messages,
                      output_config={"effort": "medium"})
        if fallbacks:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        resp = await client.beta.messages.create(**kwargs)
        usage["turns"] = turn
        usage["input_tokens"] += resp.usage.input_tokens
        usage["output_tokens"] += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            dispatch.audit.record(f"turn{turn}", "model", {}, str(getattr(resp, "stop_details", "")), "refusal")
            raise RuntimeError("model refused the request; see audit log")
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            if dispatch.plan is None and not nudged:
                nudged = True
                messages.append({"role": "user", "content": "Call submit_plan now with your plan."})
                continue
            break
        results = []
        for b in uses:
            text, is_error = await dispatch(f"turn{turn}", b.name, b.input)
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": text, **({"is_error": True} if is_error else {})})
        messages.append({"role": "user", "content": results})
        if dispatch.plan is not None:
            break
    return usage


def write_outputs(plan: dict, run_id: str, meta: dict, out_dir: str = "plans") -> Path:
    d = Path(out_dir) / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps({"run_id": run_id, "meta": meta, **plan}, indent=2))
    (d / "plan.md").write_text(render_markdown(plan, run_id, meta))
    return d


def propose(run_dir, audit, truth_path, repo_root, allow_failed_eval=False, opener=None):
    """Open a PR for the plan, but only if it passes the eval. Returns (status, detail).

    A plan that fails the eval is not put in front of a human for approval unless asked for explicitly.
    """
    from eval_plan import summarize
    from open_pr import OpenPrError, open_plan_pr

    opener = opener or open_plan_pr
    text, passed = summarize(json.loads((Path(run_dir) / "plan.json").read_text()), truth_path)
    if not passed and not allow_failed_eval:
        audit.record("done", "open_pr", {"run_dir": str(run_dir)}, text[-300:], "not opened: eval failed")
        return "blocked", text
    try:
        url = opener(run_dir, repo_root, eval_summary=text)
    except OpenPrError as e:
        audit.record("done", "open_pr", {"run_dir": str(run_dir)}, str(e), "error")
        return "error", str(e)
    audit.record("done", "open_pr", {"run_dir": str(run_dir)}, url, "pr opened")
    return "opened", url


async def main_async(args):
    from anthropic import AsyncAnthropic
    from mcp_bridge import READ_ONLY_TOOLS, dx_mcp

    # No key check: the SDK resolves credentials itself (ANTHROPIC_API_KEY, or the profile from `ant auth login`).
    audit = AuditLog(args.audit_db)
    print(f"run_id {audit.run_id}")
    async with dx_mcp(args.org) as (mcp_call, mcp_defs, exposed):
        audit.record("startup", "mcp_server", {"org": args.org}, f"exposed={sorted(exposed)}",
                     f"allowed={sorted(READ_ONLY_TOOLS)}")
        pinned = {"usernameOrAlias": args.org, "directory": str(Path(__file__).resolve().parents[1])}
        dispatch = Dispatcher(LocalTools(args.input), mcp_call, READ_ONLY_TOOLS, audit, pinned)
        tools = LOCAL_TOOL_DEFS + hide_args(mcp_defs) + [SUBMIT_PLAN_DEF]
        prompt = (f"Plan the onboarding. Target org alias: {args.org}. Exports are in the input directory "
                  f"(accounts.csv, contacts.csv, cases.csv).")
        usage = await agent_loop(AsyncAnthropic(), args.model, tools, dispatch, prompt, args.max_turns, not args.no_fallbacks)
    if dispatch.plan is None:
        sys.exit(f"no valid plan produced; see audit log (run {audit.run_id})")
    meta = {"model": args.model, "tool_calls": dispatch.calls, **usage}
    d = write_outputs(dispatch.plan, audit.run_id, meta, args.plans_dir)
    audit.record("done", "write_plan", {"dir": str(d)}, f"needs_human={len(dispatch.plan['needs_human'])}", "plan written")
    print(f"wrote {d}/plan.json and plan.md | needs human: {len(dispatch.plan['needs_human'])} | tokens {usage['input_tokens']}/{usage['output_tokens']}")
    if args.open_pr:
        status, detail = propose(d, audit, args.truth, Path(__file__).resolve().parents[1], args.open_pr_anyway)
        if status == "opened":
            print(f"opened PR: {detail}")
        else:
            print(detail)
            sys.exit(f"PR not opened ({status}); the plan is still at {d}")


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="agent/data/out")
    ap.add_argument("--org", required=True, help="explicit org alias, e.g. dev-scratch")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--no-fallbacks", action="store_true")
    ap.add_argument("--audit-db", default="agent/audit.sqlite")
    ap.add_argument("--plans-dir", default="plans")
    ap.add_argument("--open-pr", action="store_true", help="propose the plan as a PR (only if it passes the eval)")
    ap.add_argument("--open-pr-anyway", action="store_true", help="open the PR even if the eval fails")
    ap.add_argument("--truth", default="agent/data/truth/truth.json", help="ground truth for the eval, if present")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
