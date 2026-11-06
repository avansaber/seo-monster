"""Operator-invoked, allowlisted MCP capture client. Never exposed as a generic host tool."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
from pathlib import Path
from copy import deepcopy
from typing import Any

try:
    from jsonschema import Draft202012Validator, ValidationError, SchemaError
    from mcp import ClientSession, StdioServerParameters, MCPError
    from mcp.types import CallToolResult, PaginatedRequestParams
    from mcp.client.stdio import stdio_client
except ModuleNotFoundError as exc:
    if exc.name and exc.name.split(".")[0] in {"mcp", "jsonschema", "pydantic"}:
        raise SystemExit("This interface requires seomonster-workbench[mcp]. Install the optional MCP extra.") from None
    raise

from .adapter import ADAPTER_VERSION, adapt_saved_result
from .engine import utc_now
from .mcp_boundary import configure_diagnostics
from .portable import validate_portable_export
from .util import canonical_json, digest


# Manually reviewed against SEOmonster 0.9.3. Never derive authority from annotations alone.
READ_TOOLS = {
    "system_status": "local_engine",
    "gsc_search_analytics": "gsc",
    "gsc_compare_periods": "gsc",
    "ga4_run_report": "ga4",
}


def _arguments(arguments: dict, timeout: float) -> dict:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("capture timeout must be between 0 and 120 seconds")
    if not isinstance(arguments, dict):
        raise ValueError("capture arguments must be a JSON object")
    frozen = deepcopy(arguments)
    validate_portable_export(frozen)
    return frozen


def _local_schema(schema):
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key in {"$ref", "$dynamicRef"} and (not isinstance(value, str) or not value.startswith("#")):
                raise ValueError("capture schema contains a remote reference")
            _local_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            _local_schema(value)


def _engine_envelope(result) -> dict:
    if not isinstance(result, CallToolResult) or len(result.content) != 1 or result.content[0].type != "text":
        raise ValueError("MCP result does not match the engine content contract")
    text = result.content[0].text
    if len(text.encode()) > 2_000_000:
        raise ValueError("engine content exceeds the capture limit")
    try:
        envelope = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("engine content is not a valid JSON envelope") from None
    if not isinstance(envelope, dict) or not {"ok", "data", "error"} <= set(envelope) or type(envelope["ok"]) is not bool:
        raise ValueError("invalid engine envelope")
    if result.is_error == envelope["ok"]:
        raise ValueError("engine envelope conflicts with the MCP error flag")
    if result.structured_content is not None and canonical_json(result.structured_content) != canonical_json(envelope):
        raise ValueError("engine text and structured content disagree")
    return envelope


def _transport_failure(code: str, message: str) -> dict:
    return {"ok": False, "data": None, "error": {"code": code, "service": "mcp_transport", "message": message}}


async def capture_session(
    session: ClientSession, tool: str, arguments: dict[str, Any], *,
    timeout: float = 30, captured_at: str | None = None, freshness: str = "unknown",
) -> dict[str, Any]:
    if tool not in READ_TOOLS:
        raise ValueError("tool is not in the read-only capture allowlist")
    arguments = _arguments(arguments, timeout)
    configure_diagnostics()
    at = captured_at or utc_now()
    receipt: dict[str, Any] = {"origin": "mcp", "external_write_routing": False,
                               "protocol_contract": "seomonster-mcp-sdk2/v1"}
    try:
        async with asyncio.timeout(timeout):
            initialized = await session.initialize()
            receipt.update({"server": initialized.server_info.model_dump(mode="json", by_alias=True),
                            "protocol_version": initialized.protocol_version})
            listed = await session.list_tools()
            definitions = list(listed.tools)
            cursor, visited, page_count = listed.next_cursor, set(), 1
            while cursor is not None:
                if not isinstance(cursor, str) or cursor in visited or page_count >= 20:
                    raise ValueError("invalid registry pagination")
                visited.add(cursor)
                listed = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
                definitions.extend(listed.tools)
                cursor, page_count = listed.next_cursor, page_count + 1
            matching = [item for item in definitions if item.name == tool]
            if len(matching) != 1:
                raise ValueError("expected tool missing or duplicated in server registry")
            definition = matching[0]
            if definition.annotations is None or definition.annotations.read_only_hint is not True:
                raise ValueError("allowlisted tool no longer advertises read-only semantics")
            _local_schema(definition.input_schema)
            try:
                Draft202012Validator.check_schema(definition.input_schema)
                Draft202012Validator(definition.input_schema).validate(arguments)
            except (ValidationError, SchemaError, ValueError, TypeError, RecursionError):
                raise ValueError("capture arguments or schema are invalid") from None
            except Exception:
                logging.getLogger(__name__).exception("Capture schema validation failed.", exc_info=False)
                raise ValueError("capture arguments or schema are invalid") from None
            receipt["input_schema_hash"] = digest(definition.input_schema)
            result = await session.call_tool(tool, arguments)
            envelope = _engine_envelope(result)
            receipt["result_content_hash"] = digest([item.model_dump(mode="json", by_alias=True) for item in result.content])
    except TimeoutError:
        receipt["origin"] = "adapter_transport_failure"
        envelope = _transport_failure("CAPTURE_TIMEOUT", "Read-only capture timed out; no provider values were inferred.")
    except MCPError as exc:
        receipt["origin"] = "adapter_transport_failure"
        code = "CAPTURE_TIMEOUT" if exc.code == -32001 else "CAPTURE_PROTOCOL_ERROR"
        envelope = _transport_failure(code, "Read-only transport rejected the request; no provider values were inferred.")
    except (ConnectionError, OSError):
        receipt["origin"] = "adapter_transport_failure"
        envelope = _transport_failure("CAPTURE_UNAVAILABLE", "Read-only transport was unavailable; no provider values were inferred.")
    data = envelope.get("data")
    partial = isinstance(data, dict) and bool(data.get("failed"))
    capture = {
        "adapter_version": ADAPTER_VERSION, "tool": tool, "arguments": arguments,
        "captured_at": at, "freshness": freshness,
        "provider": {"name": READ_TOOLS[tool],
                     "status": "unavailable" if not envelope["ok"] else "degraded" if partial else "available",
                     "cost": {"model": "unknown", "currency": "USD", "amount": None}},
        "result": envelope, "capture_receipt": receipt,
    }
    adapt_saved_result(capture, "capture-validation")
    validate_portable_export(capture)
    return capture


async def capture_stdio(command: str, args: list[str], tool: str, arguments: dict, **options) -> dict:
    # Check before spawning an operator-configured process or initializing its providers.
    if tool not in READ_TOOLS:
        raise ValueError("tool is not in the read-only capture allowlist")
    arguments = _arguments(arguments, options.get("timeout", 30))
    # Carry only the explicit configuration selector, not all host credentials.
    config_env = {"SEO_MCP_CONFIG": os.environ["SEO_MCP_CONFIG"]} if "SEO_MCP_CONFIG" in os.environ else {}
    configure_diagnostics()
    params = StdioServerParameters(command=command, args=args, env=config_env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            return await capture_session(session, tool, arguments, **options)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Capture an explicitly selected SEOmonster read tool over local MCP")
    parser.add_argument("--tool", choices=sorted(READ_TOOLS), required=True)
    parser.add_argument("--arguments", required=True, help="Path to call arguments JSON")
    parser.add_argument("--output", required=True, help="New capture file; existing files are never overwritten")
    parser.add_argument("--engine-command", default="seo-monster")
    parser.add_argument("--engine-arg", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--freshness", choices=["current", "stale", "unknown"], default="unknown")
    parsed = parser.parse_args(argv)
    if not 0 < parsed.timeout <= 120:
        parser.error("timeout must be between 0 and 120 seconds")
    try:
        if Path(parsed.output).exists():
            raise ValueError("output already exists")
        arguments = json.loads(Path(parsed.arguments).read_text())
        capture = asyncio.run(capture_stdio(
            parsed.engine_command, parsed.engine_arg, parsed.tool, arguments,
            timeout=parsed.timeout, freshness=parsed.freshness,
        ))
        with Path(parsed.output).open("x", encoding="utf-8") as stream:
            stream.write(canonical_json(capture) + "\n")
        print(canonical_json({"ok": True, "capture_hash": digest(capture), "provider_status": capture["provider"]["status"]}))
    except Exception:
        # Do not echo arbitrary upstream exception text, credentials, or private paths.
        print(canonical_json({"ok": False, "data": None, "error": {"code": "CAPTURE_FAILED", "message": "Capture failed validation or transport checks; inspect the local configuration."}}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
