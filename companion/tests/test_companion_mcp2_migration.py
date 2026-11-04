"""SDK 2 companion boundaries: unavailable evidence, local authority and safe errors."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import types

from seomonster_workbench.capture import capture_session, capture_stdio

CANARY = "synthetic-companion-secret-never-reflect"
ROOT = Path(__file__).resolve().parents[1]


class Session:
    def __init__(self, result=None, pages=None, fail=None):
        self.result = result or {"ok": True, "data": {}, "error": None}
        self.pages = pages
        self.fail = fail
        self.calls = []
        self.tool = types.Tool(name="system_status", input_schema={"type": "object", "properties": {"probe": {"type": "boolean"}}, "additionalProperties": False}, annotations=types.ToolAnnotations(read_only_hint=True))

    async def initialize(self):
        self.calls.append("initialize")
        return SimpleNamespace(server_info=types.Implementation(name="seo-mcp", version="fixture"), protocol_version="2025-11-25")

    async def list_tools(self, *, params=None):
        self.calls.append(("list", None if params is None else params.cursor))
        return self.pages.pop(0) if self.pages else types.ListToolsResult(tools=[self.tool])

    async def call_tool(self, name, arguments):
        self.calls.append(("call", name))
        if self.fail:
            raise self.fail
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(self.result))], is_error=not self.result["ok"])


def capture(session, **options):
    return asyncio.run(capture_session(session, "system_status", {"probe": False}, **options))


def test_capture_preserves_provider_error_flag_and_unknown_cost():
    expected = {"ok": False, "data": None, "error": {"code": "AUTH_MISSING", "service": "gsc", "message": "Provider unavailable."}}
    result = capture(Session(expected))
    assert result["result"] == expected
    assert result["provider"]["status"] == "unavailable"
    assert result["provider"]["cost"]["amount"] is None
    assert result["capture_receipt"]["origin"] == "mcp"


def test_sdk2_pagination_uses_typed_params_and_checks_every_page():
    session = Session()
    session.pages = [types.ListToolsResult(tools=[], next_cursor="next"), types.ListToolsResult(tools=[session.tool])]
    assert capture(session)["result"]["ok"]
    assert ("list", "next") in session.calls


def test_capture_rejects_result_flag_conflict():
    class Conflicting(Session):
        async def call_tool(self, *_):
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(self.result))], is_error=True)
    with pytest.raises(ValueError, match="flag"):
        capture(Conflicting())


def test_capture_rejects_disagreeing_structured_content():
    class Conflicting(Session):
        async def call_tool(self, *_):
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(self.result))], structured_content={"ok": False})
    with pytest.raises(ValueError, match="content"):
        capture(Conflicting())


def test_capture_never_resolves_remote_schema_references(monkeypatch):
    import urllib.request
    attempts = []
    def refuse(*args, **kwargs):
        attempts.append(True)
        raise OSError("Synthetic network access refused")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    session = Session()
    session.tool = types.Tool(name="system_status", input_schema={"$ref": "https://schema.example.invalid/private"}, annotations=types.ToolAnnotations(read_only_hint=True))
    with pytest.raises(ValueError, match="schema"):
        capture(session)
    assert not attempts
    assert not any(isinstance(c, tuple) and c[0] == "call" for c in session.calls)


@pytest.mark.parametrize("timeout", [0, -1, None, True, float("nan"), 121])
def test_invalid_timeout_is_rejected_before_transport(timeout):
    session = Session()
    with pytest.raises(ValueError, match="timeout"):
        capture(session, timeout=timeout)
    assert session.calls == []


def test_protocol_error_does_not_copy_upstream_message_or_retry():
    from mcp import MCPError
    session = Session(fail=MCPError(-32602, CANARY))
    result = capture(session)
    assert result["provider"]["status"] == "unavailable"
    assert result["result"]["error"]["code"] == "CAPTURE_PROTOCOL_ERROR"
    assert CANARY not in json.dumps(result)
    assert session.calls.count(("call", "system_status")) == 1


def test_write_is_denied_before_process_spawn():
    with pytest.raises(ValueError, match="allowlist"):
        asyncio.run(capture_stdio("must-not-run", [], "indexnow_submit", {}))


def facade(tmp_path):
    from seomonster_workbench.mcp_server import create_server
    return create_server(tmp_path / "fixture.db", "fixture")


def test_facade_typed_errors_keep_schema_and_no_execution(tmp_path):
    async def check():
        server = facade(tmp_path)
        tools = await server.list_tools()
        assert len(tools) == 17 and all(t.output_schema for t in tools)
        result = await server.call_tool("wb_recommendation_inspect", {"recommendation_id": "missing"})
        assert isinstance(result, types.CallToolResult) and result.is_error
        assert result.structured_content["ok"] is False
        assert json.loads(result.content[0].text) == result.structured_content
    asyncio.run(check())


def test_unknown_facade_tool_does_not_reflect_untrusted_name(tmp_path):
    from mcp.client import Client
    async def check():
        async with Client(facade(tmp_path), cache=None) as client:
            result = await client.call_tool(CANARY, {})
            assert result.is_error
            assert CANARY not in result.model_dump_json()
    asyncio.run(check())


def test_facade_bad_input_does_not_create_a_project(tmp_path):
    from mcp.client import Client
    from seomonster_workbench.engine import Workbench
    async def check():
        async with Client(facade(tmp_path), cache=None) as client:
            result = await client.call_tool("wb_project_create", {"name": CANARY, "business_context": CANARY, "sites": []})
            assert result.is_error and CANARY not in result.model_dump_json()
    asyncio.run(check())
    with Workbench(tmp_path / "fixture.db") as wb:
        assert not wb.store.has_project("fixture")


def test_http_guard_rejects_untrusted_host_before_app(tmp_path):
    from seomonster_workbench.mcp_server import LocalHTTPGuard
    called = []
    async def app(*_):
        called.append(True)
    token = "offline-fixture-token-32-characters-only"
    guard = LocalHTTPGuard(app, token)
    async def check():
        sent = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(event):
            sent.append(event)
        await guard({"type": "http", "headers": [(b"authorization", ("Bearer " + token).encode()), (b"host", b"evil.example")]}, receive, send)
        assert not called
        assert sent[0]["status"] == 403
    asyncio.run(check())


def test_invalid_provider_schema_does_not_reflect_values():
    session = Session()
    session.tool = types.Tool(name="system_status", input_schema={"type": CANARY}, annotations=types.ToolAnnotations(read_only_hint=True))
    with pytest.raises(ValueError) as info:
        capture(session)
    assert CANARY not in str(info.value)
    assert not any(isinstance(c, tuple) and c[0] == "call" for c in session.calls)


def test_repeated_registry_cursor_cannot_become_an_empty_observation():
    session = Session()
    session.pages = [types.ListToolsResult(tools=[], next_cursor="same"), types.ListToolsResult(tools=[], next_cursor="same")]
    with pytest.raises(ValueError, match="pagination"):
        capture(session)
    assert not any(isinstance(c, tuple) and c[0] == "call" for c in session.calls)


def test_empty_cursor_is_followed_as_an_opaque_value():
    session = Session()
    session.pages = [types.ListToolsResult(tools=[], next_cursor=""), types.ListToolsResult(tools=[session.tool])]
    assert capture(session)["result"]["ok"]
    assert ("list", "") in session.calls


@pytest.mark.parametrize("module_name", ["seomonster_workbench.capture", "seomonster_workbench.mcp_server"])
def test_missing_mcp_extra_has_fixed_install_diagnostic(module_name):
    import os
    import subprocess
    import sys
    code = '''import importlib.abc, runpy, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'mcp', 'jsonschema'}:
            raise ModuleNotFoundError('Synthetic missing optional dependency', name=fullname)
sys.meta_path.insert(0, Block())
runpy.run_module(sys.argv[1], run_name='__main__')
'''
    result = subprocess.run([sys.executable, "-B", "-c", code, module_name],
                            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
                            capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode != 0
    assert "seomonster-workbench[mcp]" in result.stderr
    assert "Traceback" not in result.stderr


def test_broken_local_schema_reference_has_a_fixed_error():
    session = Session()
    session.tool = types.Tool(name="system_status", input_schema={"$ref": "#/" + CANARY}, annotations=types.ToolAnnotations(read_only_hint=True))
    with pytest.raises(ValueError) as info:
        capture(session)
    assert CANARY not in str(info.value)


def test_capture_filters_sdk_diagnostics_before_initialization(caplog):
    import logging
    class DiagnosticSession(Session):
        async def initialize(self):
            logging.getLogger("mcp.client.session").warning("Invalid provider metadata: %s", CANARY)
            return await super().initialize()
    assert capture(DiagnosticSession())["result"]["ok"]
    assert CANARY not in caplog.text
    assert "MCP diagnostic" in caplog.text
