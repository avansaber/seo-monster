"""SDK 2 boundary, privacy and distribution regressions using offline providers."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import types

ROOT = Path(__file__).resolve().parents[1]
CANARY = "synthetic-boundary-secret-never-disclose"


def module():
    from seo_mcp import server
    return server


def context():
    return SimpleNamespace(lifespan_context={"provider_gate": asyncio.Semaphore(4)})


def invoke(name="system_status", arguments=None):
    async def run():
        return await module().call_tool(context(), SimpleNamespace(name=name, arguments=arguments))
    return asyncio.run(run())


def payload(result):
    assert isinstance(result, types.CallToolResult)
    assert len(result.content) == 1 and result.content[0].type == "text"
    return json.loads(result.content[0].text)


def inject(monkeypatch, operation):
    mod = module()
    monkeypatch.setattr(mod, "load_config", lambda: object())
    monkeypatch.setattr(mod, "build_clients", lambda _: {})
    monkeypatch.setattr(mod, "dispatch", operation)


def test_sdk2_target_and_validation_dependency_declared():
    import tomllib
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "mcp>=2.3.0,<3" in project["project"]["dependencies"]
    assert "jsonschema>=4.20,<5" in project["project"]["dependencies"]


def test_typed_catalog_and_actual_version():
    async def run():
        mod = module()
        tools = await mod.list_tools(context(), None)
        prompts = await mod.list_prompts(context(), None)
        assert isinstance(tools, types.ListToolsResult) and len(tools.tools) == 70
        assert isinstance(prompts, types.ListPromptsResult) and len(prompts.prompts) == 13
        info = mod.server.create_initialization_options()
        from seo_mcp import __version__
        assert info.server_version == __version__
        assert tools.tools[0].model_dump(by_alias=True)["inputSchema"]
    asyncio.run(run())


@pytest.mark.parametrize("arguments", [{"probe": "true"}, {"unexpected": CANARY}, [], False, 0, "invalid"])
def test_invalid_arguments_rejected_before_config_or_dispatch(monkeypatch, arguments):
    mod = module()
    def forbidden(*_):
        pytest.fail("invalid input reached configuration or provider dispatch")
    monkeypatch.setattr(mod, "load_config", forbidden)
    monkeypatch.setattr(mod, "dispatch", forbidden)
    result = invoke(arguments=arguments)
    assert result.is_error is True
    assert payload(result)["error"]["code"] == "INVALID_INPUT"
    assert CANARY not in result.model_dump_json()


def test_unknown_tool_is_safe_before_configuration(monkeypatch):
    mod = module()
    monkeypatch.setattr(mod, "load_config", lambda: pytest.fail("unknown tool initialized configuration"))
    result = invoke(CANARY)
    assert result.is_error and payload(result)["ok"] is False
    assert CANARY not in result.model_dump_json()


@pytest.mark.parametrize("failure", [RuntimeError(CANARY), ValueError(CANARY), OSError(CANARY)])
def test_unexpected_handler_errors_are_fixed_messages(monkeypatch, failure):
    def explode(*_):
        raise failure
    inject(monkeypatch, explode)
    result = invoke(arguments={"probe": False})
    assert result.is_error and payload(result)["error"]["code"] == "UPSTREAM_ERROR"
    assert CANARY not in result.model_dump_json()


@pytest.mark.parametrize("bad", [None, [], {"ok": "true"}, {"ok": True, "data": float("nan"), "error": None}, {"ok": True, "data": object(), "error": None}])
def test_invalid_or_non_json_results_cannot_escape_boundary(monkeypatch, bad):
    inject(monkeypatch, lambda *_: bad)
    result = invoke()
    assert result.is_error and payload(result)["ok"] is False


def test_provider_failure_sets_error_flag_and_retains_safe_envelope(monkeypatch):
    from seo_mcp.errors import ErrorCode, err
    expected = err(ErrorCode.AUTH_MISSING, "gsc", "Provider is unavailable.")
    inject(monkeypatch, lambda *_: expected)
    result = invoke()
    assert result.is_error and payload(result) == expected


def test_success_has_false_error_flag(monkeypatch):
    expected = {"ok": True, "data": {"approved_context": "Approved source material."}, "error": None}
    inject(monkeypatch, lambda *_: expected)
    result = invoke()
    assert result.is_error is False and payload(result) == expected


def test_sync_provider_does_not_block_protocol_loop(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def operation(*_):
        entered.set()
        assert release.wait(2)
        return {"ok": True, "data": {}, "error": None}
    inject(monkeypatch, operation)
    async def run():
        task = asyncio.create_task(module().call_tool(context(), types.CallToolRequestParams(name="system_status")))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(.005)
            assert entered.is_set()
            await asyncio.sleep(.01)
            assert not task.done()
        finally:
            release.set()
            await task
    asyncio.run(asyncio.wait_for(run(), 3))


def test_cancellation_never_retries_or_releases_active_worker_slot(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def operation(*_):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return {"ok": True, "data": {}, "error": None}
    inject(monkeypatch, operation)
    async def run():
        ctx = context()
        ctx.lifespan_context["provider_gate"] = asyncio.Semaphore(1)
        task = asyncio.create_task(module().call_tool(ctx, types.CallToolRequestParams(name="system_status")))
        second = None
        try:
            while not entered.is_set():
                await asyncio.sleep(.005)
            task.cancel()
            await asyncio.sleep(.02)
            second = asyncio.create_task(module().call_tool(ctx, types.CallToolRequestParams(name="system_status")))
            await asyncio.sleep(.02)
            assert calls == [1]
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            if second:
                await second
        assert calls == [1, 1]
    asyncio.run(asyncio.wait_for(run(), 3))


def test_sdk_diagnostics_do_not_include_exception_values():
    mod = module()
    record = logging.LogRecord("mcp.server.test", logging.ERROR, "fixture.py", 1, "%s", (CANARY,), (RuntimeError, RuntimeError(CANARY), None))
    record.exc_text, record.stack_info = CANARY, CANARY
    assert mod.SafeDiagnostics().filter(record)
    assert CANARY not in record.getMessage()
    assert record.exc_info is None and record.exc_text is None and record.stack_info is None


def test_unknown_prompt_uses_fixed_protocol_error():
    async def run():
        from mcp import MCPError
        with pytest.raises(MCPError) as info:
            await module().get_prompt(context(), types.GetPromptRequestParams(name=CANARY))
        assert CANARY not in str(info.value)
    asyncio.run(run())


def test_coordination_files_are_ignored(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    for name in ("HANDOFF.md", "CLAUDE.md"):
        assert subprocess.run(["git", "check-ignore", "-q", name], cwd=tmp_path, check=False).returncode == 0


def test_distribution_guard_refuses_staged_private_material(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "HANDOFF.md").write_text("Synthetic private coordination fixture.\n")
    subprocess.run(["git", "add", "HANDOFF.md"], cwd=tmp_path, check=True)
    import sys
    result = subprocess.run([sys.executable, str(ROOT / "scripts/check_distribution.py"), "--staged"], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "Private coordination or planning files" in result.stderr


def test_release_guard_includes_coordination_names():
    text = (ROOT / ".github/workflows/release.yml").read_text()
    assert "HANDOFF" in text and "CLAUDE" in text


def test_middleware_preserves_method_not_found_without_reflecting_input():
    from mcp import MCPError
    async def run():
        async def reject(_):
            raise MCPError(-32601, CANARY)
        ctx = SimpleNamespace(method=CANARY)
        with pytest.raises(MCPError) as info:
            await module()._safe_request(ctx, reject)
        assert info.value.code == -32601
        assert CANARY not in str(info.value)
    asyncio.run(run())


def test_registry_schema_lookup_cannot_expose_mutable_catalog():
    mod = module()
    schema = mod._tool_schema("system_status")
    schema["properties"]["probe"]["type"] = "string"
    assert mod._tool_schema("system_status")["properties"]["probe"]["type"] == "boolean"


def test_malformed_wire_requests_keep_stdout_json_and_stderr_private(tmp_path):
    import os
    import sys
    config = tmp_path / "empty.toml"
    config.write_text("")
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": CANARY}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "system_status", "arguments": {"probe": CANARY}}},
        {"jsonrpc": "2.0", "id": 4, "method": "prompts/get", "params": {"name": CANARY}},
        {"jsonrpc": "2.0", "id": 5, "method": CANARY, "params": {"secret": CANARY}},
    ]
    result = subprocess.run(
        [sys.executable, "-B", "-m", "seo_mcp.server"],
        input="".join(json.dumps(m) + "\n" for m in messages),
        env={"PATH": os.environ.get("PATH", ""), "SEO_MCP_CONFIG": str(config),
             "PYTHONPATH": str(Path(module().__file__).resolve().parent.parent), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0
    assert CANARY not in result.stdout + result.stderr
    frames = [json.loads(line) for line in result.stdout.splitlines()]
    assert frames and all(frame.get("jsonrpc") == "2.0" for frame in frames)


@pytest.mark.parametrize("error", [{}, {"code": 42, "message": CANARY}, {"code": "UNKNOWN", "message": CANARY}])
def test_malformed_error_envelope_is_not_forwarded(monkeypatch, error):
    inject(monkeypatch, lambda *_: {"ok": False, "data": None, "error": error})
    result = invoke()
    assert payload(result)["error"]["code"] == "UPSTREAM_ERROR"
    assert CANARY not in result.model_dump_json()


def test_ci_keeps_supported_sdk_blocking_and_disables_resync():
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    required = text.split("  mcp-canary:")[0]
    assert "continue-on-error" not in required
    assert "--locked" in required and "--no-sync pytest" in required
    assert "scripts/check_distribution.py" in required
    assert "3.14" in required and "3.11" in required
    assert "companion" not in required


@pytest.mark.parametrize("mode", ["auto", "2026-07-28"])
def test_modern_discovery_catalog_prompts_and_tool_envelopes(monkeypatch, mode):
    from mcp.client import Client
    inject(monkeypatch, lambda *_: {"ok": True, "data": {"fixture": True}, "error": None})
    async def run():
        async with Client(module().server, mode=mode, cache=None) as client:
            assert client.protocol_version == "2026-07-28"
            assert len((await client.list_tools()).tools) == 70
            assert len((await client.list_prompts()).prompts) == 13
            assert (await client.get_prompt("weekly_review", {"days": "7"})).messages
            assert payload(await client.call_tool("system_status", {"probe": False}))["ok"] is True
    asyncio.run(run())
