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
from .clients.gsc import build_gsc_client
from .clients.psi import build_psi_client
from .config import Config, load_config
from .errors import ErrorCode, err
from .tools import gsc_tools, psi_tools, system_status


server = Server("seo-mcp")


# Tool schema dicts, one per registered tool. Tool modules expose plain dicts
# (import-light, testable without mcp); the server wraps them into mcp Tools.
# Tools are registered progressively per phase, so the catalog never advertises
# an undispatchable tool. Phase 2 adds the 10 GSC tools and the PSI tool.
_TOOL_DEFS: list[dict[str, Any]] = [
    system_status.TOOL,
    *gsc_tools.TOOLS,
    *psi_tools.TOOLS,
]

# name -> handler with signature (arguments, config, clients) -> envelope.
# system_status is handled separately (it also needs the tool registry).
_HANDLERS: dict[str, Any] = {
    **gsc_tools.HANDLERS,
    **psi_tools.HANDLERS,
}

# service key -> builder(config) -> client. Used by the lazy ClientProvider.
_CLIENT_BUILDERS: dict[str, Any] = {
    "gsc": build_gsc_client,
    "psi": build_psi_client,
}


def registered_tool_names() -> list[str]:
    """Names of all currently-dispatchable tools."""
    return [d["name"] for d in _TOOL_DEFS]


class ClientProvider:
    """Lazily builds and caches per-service clients.

    A client is built only when first requested, so calling a PSI tool never
    triggers Google's OAuth flow, and ``system_status`` without ``probe`` builds
    nothing. ``get`` returns None for a service with no builder registered (so
    ``system_status`` reports ``reachable: null`` for those). Builders may raise
    (e.g. MissingGoogleAuth); callers convert that into AUTH_MISSING.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._cache: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        if key in self._cache:
            return self._cache[key]
        builder = _CLIENT_BUILDERS.get(key)
        if builder is None:
            return None
        client = builder(self._config)
        self._cache[key] = client
        return client


def build_clients(config: Config) -> ClientProvider:
    """Return the lazy client provider for this config."""
    return ClientProvider(config)


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

    handler = _HANDLERS.get(name)
    if handler is None:
        return err(
            ErrorCode.INVALID_INPUT,
            "general",
            f"Unknown tool: {name!r}.",
            remediation="Call system_status to see the available tools.",
        )
    return handler(arguments, config, clients)


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
