import asyncio
import json
from types import SimpleNamespace

import pytest
from mcp.types import Tool, ToolAnnotations, TextContent, CallToolResult

from seomonster_workbench.capture import capture_session, capture_stdio


class MockSession:
    def __init__(self, envelope, *, read=True, delay=0):
        self.envelope, self.read, self.delay = envelope, read, delay
        self.calls = []

    async def initialize(self):
        self.calls.append("initialize")
        return SimpleNamespace(server_info=SimpleNamespace(model_dump=lambda **_: {"name": "seo-mcp", "version": "fixture"}), protocol_version="2025-11-25")

    async def list_tools(self):
        return SimpleNamespace(next_cursor=None, tools=[Tool(
            name="gsc_search_analytics", inputSchema={"type": "object"},
            annotations=ToolAnnotations(readOnlyHint=self.read),
        )])

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        await asyncio.sleep(self.delay)
        return CallToolResult(is_error=not self.envelope.get("ok", True), content=[TextContent(type="text", text=json.dumps(self.envelope))])


def test_capture_preserves_envelope_metadata_and_unknown_cost(fixtures_dir):
    saved = json.loads((fixtures_dir / "gsc_baseline.json").read_text())
    session = MockSession(saved["result"])
    result = asyncio.run(capture_session(session, saved["tool"], saved["arguments"], captured_at=saved["captured_at"]))
    assert result["result"] == saved["result"]
    assert result["freshness"] == "unknown"
    assert result["provider"]["cost"]["amount"] is None
    assert result["capture_receipt"]["input_schema_hash"]


def test_write_is_rejected_before_any_transport_or_process():
    session = MockSession({})
    with pytest.raises(ValueError, match="allowlist"):
        asyncio.run(capture_session(session, "gsc_submit_sitemap", {}))
    assert session.calls == []
    with pytest.raises(ValueError, match="allowlist"):
        asyncio.run(capture_stdio("must-not-be-spawned", [], "indexnow_submit", {}))


def test_read_annotation_drift_is_rejected_before_call():
    session = MockSession({}, read=False)
    with pytest.raises(ValueError, match="read-only"):
        asyncio.run(capture_session(session, "gsc_search_analytics", {}))
    assert session.calls == ["initialize"]


def test_timeout_becomes_unavailable_with_adapter_origin():
    session = MockSession({}, delay=1)
    result = asyncio.run(capture_session(session, "gsc_search_analytics", {}, timeout=0.01))
    assert result["result"]["data"] is None
    assert result["provider"]["status"] == "unavailable"
    assert result["capture_receipt"]["origin"] == "adapter_transport_failure"


def test_broken_provenance_envelope_does_not_create_capture():
    with pytest.raises(ValueError, match="envelope"):
        asyncio.run(capture_session(MockSession({"ok": True}), "gsc_search_analytics", {}))
