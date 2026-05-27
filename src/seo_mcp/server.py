"""seo-mcp server entry point.

Single ``Server`` instance, one ``list_tools`` registry, one ``call_tool``
dispatcher, STDIO transport, console-script ``main`` (shape adopted from the
tailtest production MCP server).

Tools are registered progressively per build phase: ``list_tools`` only
advertises tools that are actually dispatchable, so the catalog never lies.
Phase 1 registers ``system_status`` only.

The dispatch logic is split out as a plain function so tests can drive it with
injected fake clients without going through STDIO.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from . import __version__
from .config import Config, load_config
from .errors import ErrorCode, err
from .tools import system_status


server = Server("seo-mcp")


# Tool schema dicts, one per registered tool. Tool modules expose plain dicts
# (import-light, testable without mcp); the server wraps them into mcp Tools.
# Later phases append their service tools here.
_TOOL_DEFS: list[dict[str, Any]] = [
    system_status.TOOL,
]


def registered_tool_names() -> list[str]:
    """Names of all currently-dispatchable tools."""
    return [d["name"] for d in _TOOL_DEFS]


def build_clients(config: Config) -> dict[str, Any]:
    """Construct the per-service network clients for this config.

    Phase 1 wires no data clients yet (no GSC/GA4/PSI/CF client modules until
    later phases), so this returns an empty mapping. ``system_status`` with
    ``probe`` simply reports ``reachable: null`` for services that have no
    client wired. Later phases populate this mapping.
    """
    return {}


def dispatch(
    name: str,
    arguments: Mapping[str, Any],
    config: Config,
    clients: Mapping[str, Any],
) -> dict[str, Any]:
    """Route a tool call to its handler and return the result envelope.

    Pure and synchronous so tests can call it directly. Unknown tool names
    return an INVALID_INPUT envelope rather than raising.
    """
    if name == "system_status":
        return system_status.handle(
            arguments, config, clients, registered_tool_names()
        )

    return err(
        ErrorCode.INVALID_INPUT,
        "general",
        f"Unknown tool: {name!r}.",
        remediation="Call system_status to see the available tools.",
    )


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Advertise the currently-registered tools."""
    return [Tool(**d) for d in _TOOL_DEFS]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """MCP entry point: resolve config + clients, dispatch, serialize."""
    config = load_config()
    clients = build_clients(config)
    result = dispatch(name, arguments or {}, config, clients)
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _async_main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Console-script entry point for ``seo-mcp``."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
