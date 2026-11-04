import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from seomonster_workbench.util import digest


CONTRACT = Path(__file__).resolve().parents[1] / "evaluation" / "contracts" / "seomonster-sdk2-v1.json"
REPO_ROOT = Path(__file__).resolve().parents[2]


def test_mcp_handshake_registry_and_envelope_match_contract():
    if importlib.util.find_spec("seo_mcp") is None or importlib.util.find_spec("mcp") is None:
        pytest.skip("SEOmonster and MCP are not installed in this environment")

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    expected = json.loads(CONTRACT.read_text())

    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-m", "seo_mcp.server"], cwd=str(CONTRACT.parents[1]),
                                      env={"SEO_MCP_CONFIG": "empty.toml"})
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                prompts = await session.list_prompts()
                status = await session.call_tool("system_status", {})
                rejected = await session.call_tool("__workbench_unknown_tool__", {})
                return initialized, tools, prompts, status, rejected

    initialized, tools, prompts, status, rejected = asyncio.run(check())
    assert initialized.server_info.name == "seo-mcp"
    assert [tool.name for tool in tools.tools] == expected["tools"]
    assert [prompt.name for prompt in prompts.prompts] == expected["prompts"]
    assert digest([tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in tools.tools]) == expected["tool_contract_sha256"]
    assert digest([p.model_dump(mode="json", by_alias=True, exclude_none=True) for p in prompts.prompts]) == expected["prompt_contract_sha256"]
    assert status.is_error is False
    payload = json.loads(status.content[0].text)
    assert sorted(payload) == expected["result_envelope_keys"]
    assert payload["ok"] is True and payload["error"] is None
    safe_error = json.loads(rejected.content[0].text)
    assert rejected.is_error is True
    assert sorted(safe_error) == expected["result_envelope_keys"]
    assert safe_error["ok"] is False and safe_error["data"] is None
    assert safe_error["error"]["code"] == "INVALID_INPUT"


def test_read_adapter_captures_actual_engine_status_without_probing():
    if importlib.util.find_spec("seo_mcp") is None or importlib.util.find_spec("mcp") is None:
        pytest.skip("SEOmonster and MCP are not installed in this environment")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from seomonster_workbench.capture import capture_session

    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-m", "seo_mcp.server"], cwd=str(CONTRACT.parents[1]),
                                      env={"SEO_MCP_CONFIG": "empty.toml"})
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                return await capture_session(session, "system_status", {"probe": False})

    captured = asyncio.run(check())
    assert captured["result"]["ok"] is True
    assert captured["capture_receipt"]["origin"] == "mcp"
    assert captured["provider"]["cost"]["amount"] is None
    assert captured["freshness"] == "unknown"


def test_capture_retains_real_engine_auth_failure_without_provider_access():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from seomonster_workbench.capture import capture_session

    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-m", "seo_mcp.server"], cwd=str(CONTRACT.parents[1]),
                                      env={"SEO_MCP_CONFIG": "empty.toml"})
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                return await capture_session(session, "gsc_search_analytics", {
                    "site_url": "sc-domain:example.test", "start_date": "2026-08-01", "end_date": "2026-08-02",
                })
    captured = asyncio.run(check())
    assert captured["result"]["ok"] is False
    assert captured["result"]["error"]["code"] == "AUTH_MISSING"
    assert captured["provider"]["status"] == "unavailable"
    assert captured["provider"]["cost"]["amount"] is None
    assert captured["capture_receipt"]["origin"] == "mcp"
