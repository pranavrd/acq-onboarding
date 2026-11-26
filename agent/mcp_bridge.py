"""Bridge to the Salesforce DX MCP server, with a read-only allowlist.

The server has no read-only mode (it also ships deploy and delete tools), so safety comes from two layers:
  1. Server flags: pinned version, one named org, GA toolsets `data` and `orgs` only, no --allow-non-ga-tools.
  2. This allowlist: only these tool names are ever shown to the model or callable by it.

NOTE: @salesforce/mcp reached end of life on 2026-11-02. It is pinned, not maintained. Production would use
Salesforce's hosted MCP servers with per-user OAuth.

Run `python agent/mcp_bridge.py --org dev-scratch` to print what the server exposes and the exact input schemas.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

DX_MCP_VERSION = "0.30.15"
# Least privilege: SOQL only. list_all_orgs would show the model the devhub/prod aliases and usernames;
# get_username is unnecessary because the org is pinned in code (see plan.py).
READ_ONLY_TOOLS = {"run_soql_query"}


class AllowlistError(RuntimeError):
    pass


@asynccontextmanager
async def dx_mcp(org: str, allowed=READ_ONLY_TOOLS, version: str = DX_MCP_VERSION):
    """Yield (call, tool_defs, exposed_names).

    call(name, args) -> (text, is_error). tool_defs are Anthropic-format definitions for allowed tools only.
    """
    if org.upper().startswith(("ALLOW_ALL", "DEFAULT_")):
        raise AllowlistError("pass an explicit org alias; DEFAULT_*/ALLOW_ALL_ORGS would widen access")
    params = StdioServerParameters(
        command="npx",
        args=["-y", f"@salesforce/mcp@{version}", "--orgs", org, "--toolsets", "data,orgs", "--no-telemetry"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            exposed = {t.name for t in tools}
            missing = set(allowed) - exposed
            if missing:
                raise AllowlistError(f"server does not expose {sorted(missing)}; it exposes {sorted(exposed)}")
            defs = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                    for t in tools if t.name in allowed]

            async def call(name: str, args: dict):
                if name not in allowed:  # last line of defense; the dispatcher checks first
                    raise AllowlistError(f"{name} is not on the allowlist")
                result = await session.call_tool(name, args)
                text = "\n".join(getattr(c, "text", "") for c in result.content)
                return text, bool(result.is_error)

            yield call, defs, exposed


async def _list(org: str):
    import json
    async with dx_mcp(org) as (_, defs, exposed):
        print("server exposes:", sorted(exposed))
        print("allowed (shown to the model):")
        for d in defs:
            print(f"  {d['name']}: {json.dumps(d['input_schema'])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", required=True)
    asyncio.run(_list(ap.parse_args().org))
