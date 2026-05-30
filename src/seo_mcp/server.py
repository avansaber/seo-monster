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
import os
import sys
from typing import Any, Mapping

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptMessage,
    TextContent,
    Tool,
)

from . import prompts as prompts_module
from .clients.cloudflare import build_cf_client
from .clients.crux import build_crux_client
from .clients.ga4 import build_ga4_client
from .clients.gsc import build_gsc_client
from .clients.http import build_http_client
from .clients.indexnow import build_indexnow_client
from .clients.psi import build_psi_client
from .config import Config, load_config
from .errors import ErrorCode, err
from .tools import (
    budget_tools,
    cf_tools,
    crux_tools,
    ga4_tools,
    gsc_tools,
    hreflang_tools,
    indexnow_tools,
    linkgraph_tools,
    onpage_tools,
    psi_tools,
    redirect_tools,
    robots_tools,
    schema_tools,
    sitemap_tools,
    system_status,
)


server = Server("seo-mcp")


# Tool schema dicts, one per registered tool. Tool modules expose plain dicts
# (import-light, testable without mcp); the server wraps them into mcp Tools.
# Tools are registered progressively per phase, so the catalog never advertises
# an undispatchable tool. Phase 2 added the 10 GSC tools and the PSI tool;
# Phase 3 added the 4 GA4 tools; Phase 4 added the 6 Cloudflare tools; v0.2.0
# added the 4 GSC intelligence tools + 2 IndexNow tools (28 total); v0.3.0
# added the 7 technical-SEO HTTP tools + crux_history (36 total); v0.4.0 adds
# the 5 structured-data + cross-site-consistency tools (41 total).
_TOOL_DEFS: list[dict[str, Any]] = [
    system_status.TOOL,
    *gsc_tools.TOOLS,
    *ga4_tools.TOOLS,
    *psi_tools.TOOLS,
    *cf_tools.TOOLS,
    *indexnow_tools.TOOLS,
    *onpage_tools.TOOLS,
    *redirect_tools.TOOLS,
    *robots_tools.TOOLS,
    *sitemap_tools.TOOLS,
    *crux_tools.TOOLS,
    *schema_tools.TOOLS,
    *hreflang_tools.TOOLS,
    *linkgraph_tools.TOOLS,
    *budget_tools.TOOLS,
]

# name -> handler with signature (arguments, config, clients) -> envelope.
# system_status is handled separately (it also needs the tool registry).
_HANDLERS: dict[str, Any] = {
    **gsc_tools.HANDLERS,
    **ga4_tools.HANDLERS,
    **psi_tools.HANDLERS,
    **cf_tools.HANDLERS,
    **indexnow_tools.HANDLERS,
    **onpage_tools.HANDLERS,
    **redirect_tools.HANDLERS,
    **robots_tools.HANDLERS,
    **sitemap_tools.HANDLERS,
    **crux_tools.HANDLERS,
    **schema_tools.HANDLERS,
    **hreflang_tools.HANDLERS,
    **linkgraph_tools.HANDLERS,
    **budget_tools.HANDLERS,
}

# service key -> builder(config) -> client. Used by the lazy ClientProvider.
# "http" backs the technical-SEO tools (inspect_meta, check_canonical, ...);
# build_http_client takes no config so it accepts and ignores the argument.
_CLIENT_BUILDERS: dict[str, Any] = {
    "gsc": build_gsc_client,
    "ga4": build_ga4_client,
    "psi": build_psi_client,
    "cf": build_cf_client,
    "indexnow": build_indexnow_client,
    "http": lambda _config: build_http_client(),
    "crux": build_crux_client,
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
            arguments, config, clients,
            registered_tool_names(),
            prompts_module.prompt_names(),
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
    """Advertise the currently-registered tools.

    Uses ``Tool.model_validate`` so the nested ``annotations`` sub-dict is
    parsed into a ``ToolAnnotations`` instance per the MCP 2025-03-26 spec.
    """
    return [Tool.model_validate(d) for d in _TOOL_DEFS]


@server.list_prompts()
async def list_prompts() -> list[Prompt]:
    """Advertise the workflow prompts the host can invoke.

    Prompts are named, parameterized recipes that chain existing tools (see
    ``prompts.py``). They replace the parent-session's idea of monolithic
    ``post_deploy_verify`` / ``pre_deploy_check`` tools, which would have
    sacrificed composability for convenience.
    """
    return [Prompt.model_validate(p) for p in prompts_module.PROMPTS]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict[str, Any] | None = None) -> GetPromptResult:
    """Render a prompt's body for the host.

    Each prompt returns a single user-role message with the workflow
    instructions templated against the user-supplied arguments. The host's
    LLM reads this message and executes the steps by calling the tools the
    workflow names.
    """
    body = prompts_module.render(name, arguments)
    return GetPromptResult(
        description=next(
            (p["description"] for p in prompts_module.PROMPTS if p["name"] == name),
            None,
        ),
        messages=[
            PromptMessage(role="user", content=TextContent(type="text", text=body)),
        ],
    )


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


def _legacy_alias_invocation(argv0: str | None) -> bool:
    """True when the process was launched via the deprecated ``seo-mcp``
    console alias (rather than the canonical ``seo-monster``).

    Both console scripts point at this main(); we tell them apart by
    sys.argv[0]'s basename. The argv0 argument is kept explicit (rather
    than reading sys.argv directly) so tests can drive it without
    monkeypatching the global.
    """
    if not argv0:
        return False
    base = os.path.basename(argv0)
    # Strip Windows ".exe" / launchers.
    if base.endswith(".exe"):
        base = base[:-4]
    return base == "seo-mcp"


def _warn_if_legacy_alias(argv0: str | None) -> None:
    """Emit a one-line stderr deprecation notice when launched via the
    ``seo-mcp`` alias. Stderr is the right channel for stdio MCP servers
    (the MCP protocol uses stdout); the host captures stderr in its log
    pane without polluting the JSON-RPC stream."""
    if _legacy_alias_invocation(argv0):
        print(
            "deprecation: the 'seo-mcp' console alias is kept for "
            "v0.1.x back-compat; prefer 'seo-monster' (or 'uvx seo-monster') "
            "in production configs. Alias will be removed in a future major "
            "release.",
            file=sys.stderr,
            flush=True,
        )


def main() -> None:
    """Console-script entry point.

    With no arguments, starts the MCP server over stdio. With a recognized
    subcommand, dispatches to that. Currently supported subcommands:

        seo-monster auth    - run the one-time Google OAuth consent flow.
        seo-monster setup   - interactively configure CF/PSI/IndexNow + defaults.

    The subcommand dispatch is intentionally simple (positional argv[1]) so
    we never confuse an MCP host that launches the server with no extra
    arguments with a user typing a CLI command.
    """
    _warn_if_legacy_alias(sys.argv[0] if sys.argv else None)
    argv = sys.argv[1:]
    if argv and argv[0] == "auth":
        from .cli import auth_main
        sys.exit(auth_main(argv[1:]))
    if argv and argv[0] == "setup":
        from .cli import setup_main
        sys.exit(setup_main(argv[1:]))
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
