"""Local MCP result and diagnostic policy, independent of engine internals."""
from __future__ import annotations

import logging

from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from .util import canonical_json


class SafeDiagnostics(logging.Filter):
    """Remove SDK request and exception values while retaining diagnostic levels."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "mcp" or record.name.startswith(("mcp.", "httpx2", "httpcore2", "uvicorn.error")):
            record.msg = "MCP diagnostic; request and exception values omitted."
            record.args = ()
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def configure_diagnostics() -> None:
    logging.basicConfig()
    policy = SafeDiagnostics()
    handlers = list(logging.getLogger().handlers)
    for item in logging.Logger.manager.loggerDict.values():
        if isinstance(item, logging.Logger):
            handlers.extend(item.handlers)
    for handler in handlers:
        if not any(isinstance(item, SafeDiagnostics) for item in handler.filters):
            handler.addFilter(policy)


def result(envelope: BaseModel) -> CallToolResult:
    body = envelope.model_dump(mode="json")
    return CallToolResult(content=[TextContent(type="text", text=canonical_json(body))],
                          structured_content=body, is_error=not body["ok"])
