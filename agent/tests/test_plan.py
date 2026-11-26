import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
from audit import AuditLog  # noqa: E402
from plan import Dispatcher, agent_loop, write_outputs  # noqa: E402
from tools import LOCAL_TOOL_DEFS, SUBMIT_PLAN_DEF, LocalTools  # noqa: E402


def use(i, name, **inp):
    return NS(type="tool_use", id=f"tu{i}", name=name, input=inp)


def resp(*blocks, stop="tool_use"):
    return NS(content=list(blocks), stop_reason=stop, usage=NS(input_tokens=100, output_tokens=10))


GOOD_PLAN = {
    "mappings": [
        {"source": "accounts.company_name", "target": "Account.Name", "transform": "none", "confidence": 0.99, "reasoning": "obvious"},
        {"source": "accounts.tier", "target": None, "transform": "none", "confidence": 0.9, "reasoning": "no tier field"},
        {"source": "cases.status", "target": "Case.Status", "transform": "map", "confidence": 0.6, "reasoning": "values ambiguous"},
    ],
    "dedupe_matches": [], "proposed_new_fields": [{"object": "Account", "field_api_name": "Tier__c", "type": "Text", "reason": "tier"}],
    "data_quality_issues": [], "notes": "n",
}


class FakeClient:
    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.beta = NS(messages=NS(create=self._create))

    async def _create(self, **kw):
        self.requests.append(kw)
        return self.script.pop(0)


class PlanLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        subprocess.run([sys.executable, "-I", str(AGENT / "data" / "generate.py"),
                        "--out", str(t / "out"), "--truth", str(t / "truth")], check=True, capture_output=True)
        self.audit = AuditLog(str(t / "audit.sqlite"), run_id="test")
        self.mcp_calls = []

        async def mcp_call(name, args):
            self.mcp_calls.append((name, args))
            return '{"records": []}', False

        self.dispatch = Dispatcher(LocalTools(str(t / "out")), mcp_call, {"run_soql_query"}, self.audit)

    def tearDown(self):
        self.audit.close()
        self.tmp.cleanup()

    def run_loop(self, script, **kw):
        client = FakeClient(script)
        tools = LOCAL_TOOL_DEFS + [{"name": "run_soql_query", "description": "", "input_schema": {"type": "object"}}, SUBMIT_PLAN_DEF]
        usage = asyncio.run(agent_loop(client, "claude-sonnet-5-5", tools, self.dispatch, "go", **kw))
        return client, usage

    def decisions(self):
        return [(r[4], r[7]) for r in self.audit.rows("test")]

    def test_happy_path_with_denials_and_rejected_plan(self):
        script = [
            resp(use(1, "read_csv_sample", file="accounts.csv", n=3)),
            resp(use(2, "deploy_metadata", sourceDir="force-app"),              # not on the allowlist
                 use(3, "run_soql_query", query="DELETE FROM Account"),          # not a SELECT
                 use(4, "run_soql_query", query="select Id from Account")),      # fine
            resp(use(5, "submit_plan", **{**GOOD_PLAN, "mappings": [{**GOOD_PLAN["mappings"][0], "confidence": 7}]})),  # invalid
            resp(use(6, "submit_plan", **GOOD_PLAN)),                            # accepted
        ]
        client, usage = self.run_loop(script)
        self.assertEqual(self.mcp_calls, [("run_soql_query", {"query": "select Id from Account"})])  # denied calls never reached MCP
        d = self.decisions()
        self.assertIn(("deploy_metadata", "denied: not on allowlist"), d)
        self.assertIn(("run_soql_query", "denied: not a SELECT"), d)
        self.assertIn(("submit_plan", "rejected: invalid plan"), d)
        self.assertEqual(d[-1], ("submit_plan", "plan accepted"))
        self.assertEqual(len(self.audit.rows("test")), 6)  # every call audited, none lost
        why = sorted(n["why"] for n in self.dispatch.plan["needs_human"])
        self.assertEqual(why, ["low confidence", "no target field"])  # computed in code
        self.assertEqual(usage["turns"], 4)
        # fallbacks opt-in is on by default and the beta header is sent
        self.assertEqual(client.requests[0]["fallbacks"], "default")
        self.assertIn("server-side-fallback-2026-07-01", client.requests[0]["betas"])

    def test_refusal_aborts_and_is_audited(self):
        with self.assertRaises(RuntimeError):
            self.run_loop([resp(stop="refusal")])
        self.assertIn(("model", "refusal"), self.decisions())

    def test_no_plan_is_nudged_once_then_stops(self):
        script = [resp(NS(type="text", text="done"), stop="end_turn"), resp(NS(type="text", text="still done"), stop="end_turn")]
        self.run_loop(script)
        self.assertIsNone(self.dispatch.plan)

    def test_path_traversal_is_an_audited_error(self):
        script = [resp(use(1, "read_csv_sample", file="../truth/truth.json")), resp(use(2, "submit_plan", **GOOD_PLAN))]
        self.run_loop(script)
        self.assertIn(("read_csv_sample", "error"), self.decisions())

    def test_org_and_directory_are_pinned_in_code(self):
        async def mcp_call(name, args):
            self.mcp_calls.append((name, args))
            return "{}", False

        pinned = {"usernameOrAlias": "dev-scratch", "directory": "/proj"}
        d = Dispatcher(self.dispatch.local, mcp_call, {"run_soql_query"}, self.audit, pinned)
        self.mcp_calls.clear()
        # the model tries to aim at another org, another directory, and the Tooling API
        asyncio.run(d("t1", "run_soql_query", {"query": "SELECT Id FROM Account", "usernameOrAlias": "prod-jwt",
                                               "directory": "/", "useToolingApi": True}))
        self.assertEqual(self.mcp_calls, [("run_soql_query", {"query": "SELECT Id FROM Account", **pinned})])
        self.assertTrue(self.decisions()[-1][1].startswith("allowed (ignored model-supplied"))

    def test_hidden_args_are_removed_from_the_schema_the_model_sees(self):
        from plan import hide_args
        real = {"name": "run_soql_query", "description": "", "input_schema": {
            "type": "object", "required": ["query", "usernameOrAlias", "directory"], "additionalProperties": False,
            "properties": {"query": {"type": "string"}, "usernameOrAlias": {"type": "string"},
                           "directory": {"type": "string"}, "useToolingApi": {"type": "boolean"}}}}
        shown = hide_args([real])[0]["input_schema"]
        self.assertEqual(list(shown["properties"]), ["query"])
        self.assertEqual(shown["required"], ["query"])
        self.assertIn("usernameOrAlias", real["input_schema"]["properties"])  # original untouched

    def test_outputs_written(self):
        script = [resp(use(1, "submit_plan", **GOOD_PLAN))]
        _, usage = self.run_loop(script)
        d = write_outputs(self.dispatch.plan, "test", {"model": "m", "tool_calls": 1, **usage}, str(Path(self.tmp.name) / "plans"))
        data = json.loads((d / "plan.json").read_text())
        self.assertEqual(len(data["needs_human"]), 2)
        self.assertIn("## Needs a human (2)", (d / "plan.md").read_text())


if __name__ == "__main__":
    unittest.main()
