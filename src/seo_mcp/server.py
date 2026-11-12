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
import logging
import os
import sys
from collections.abc import Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from functools import partial
from typing import Any

from jsonschema import Draft202012Validator, ValidationError
from mcp import MCPError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    ListPromptsResult,
    ListToolsResult,
    Prompt,
    PromptMessage,
    TextContent,
    Tool,
)

from . import __version__
from . import prompts as prompts_module
from .clients.ai_engines import build_ai_engines_client
from .clients.cloudflare import build_cf_client
from .clients.crux import build_crux_client
from .clients.dataforseo import build_dataforseo_client
from .clients.ga4 import build_ga4_client
from .clients.ga4_admin import build_ga4_admin_client
from .clients.gsc import build_gsc_client
from .clients.http import build_http_client
from .clients.indexnow import build_indexnow_client
from .clients.openpagerank import build_openpagerank_client
from .clients.psi import build_psi_client
from .clients.serpbase import build_serpbase_client
from .config import Config, load_config
from .errors import ErrorCode, err
from .tools import (
    ai_citation_tools,
    ai_readiness_tools,
    ai_referral_tools,
    budget_tools,
    cf_tools,
    content_brief_tools,
    content_tools,
    crux_tools,
    ga4_tools,
    gsc_keyword_tools,
    gsc_tools,
    hreflang_tools,
    indexnow_tools,
    keyword_universe_tools,
    linkgraph_tools,
    linkrec_tools,
    onpage_serp_gap_tools,
    onpage_tools,
    psi_tools,
    rank_attribution_tools,
    redirect_tools,
    robots_posture_tools,
    robots_tools,
    schema_tools,
    serp_adjacency_tools,
    sitemap_tools,
    system_status,
    topic_cluster_tools,
)

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
    *robots_posture_tools.TOOLS,
    *sitemap_tools.TOOLS,
    *crux_tools.TOOLS,
    *schema_tools.TOOLS,
    *hreflang_tools.TOOLS,
    *linkgraph_tools.TOOLS,
    *budget_tools.TOOLS,
    # v0.7 content intelligence (Layer 1)
    *content_tools.TOOLS,
    # v0.9 roadmap Wave 1: AI/GEO + discovery + rank-loop
    *ai_readiness_tools.TOOLS,
    *ai_referral_tools.TOOLS,
    *gsc_keyword_tools.TOOLS,
    *linkrec_tools.TOOLS,
    # v0.9 roadmap Wave 2: attribution + cluster map + data-wired brief
    *rank_attribution_tools.TOOLS,
    *topic_cluster_tools.TOOLS,
    *content_brief_tools.TOOLS,
    # v0.9 roadmap Wave 3: external-data discovery + SERP gap (DataForSEO/OPR)
    *serp_adjacency_tools.TOOLS,
    *keyword_universe_tools.TOOLS,
    *onpage_serp_gap_tools.TOOLS,
    # v0.9 roadmap Wave 4: AI citation tracking (headline)
    *ai_citation_tools.TOOLS,
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
    **robots_posture_tools.HANDLERS,
    **sitemap_tools.HANDLERS,
    **crux_tools.HANDLERS,
    **schema_tools.HANDLERS,
    **hreflang_tools.HANDLERS,
    **linkgraph_tools.HANDLERS,
    **budget_tools.HANDLERS,
    **content_tools.HANDLERS,
    **ai_readiness_tools.HANDLERS,
    **ai_referral_tools.HANDLERS,
    **gsc_keyword_tools.HANDLERS,
    **linkrec_tools.HANDLERS,
    **rank_attribution_tools.HANDLERS,
    **topic_cluster_tools.HANDLERS,
    **content_brief_tools.HANDLERS,
    **serp_adjacency_tools.HANDLERS,
    **keyword_universe_tools.HANDLERS,
    **onpage_serp_gap_tools.HANDLERS,
    **ai_citation_tools.HANDLERS,
}

# service key -> builder(config) -> client. Used by the lazy ClientProvider.
# "http" backs the technical-SEO tools (inspect_meta, check_canonical, ...);
# build_http_client takes no config so it accepts and ignores the argument.
_CLIENT_BUILDERS: dict[str, Any] = {
    "gsc": build_gsc_client,
    "ga4": build_ga4_client,
    "ga4_admin": build_ga4_admin_client,
    "psi": build_psi_client,
    "cf": build_cf_client,
    "indexnow": build_indexnow_client,
    "http": lambda _config: build_http_client(),
    "crux": build_crux_client,
    "dataforseo": build_dataforseo_client,
    "serpbase": build_serpbase_client,
    "openpagerank": build_openpagerank_client,
    "ai_engines": build_ai_engines_client,
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


class SafeDiagnostics(logging.Filter):
    """Keep SDK diagnostics useful without reflecting request or exception data."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "mcp" or record.name.startswith(("mcp.", "httpx2", "httpcore2")):
            record.msg = "MCP diagnostic; request and exception values omitted."
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True


def _configure_diagnostics() -> None:
    logging.basicConfig()
    policy = SafeDiagnostics()
    for handler in logging.getLogger().handlers:
        handler.addFilter(policy)
    for value in logging.Logger.manager.loggerDict.values():
        if isinstance(value, logging.Logger):
            for handler in value.handlers:
                handler.addFilter(policy)


def _tool_schema(name: str) -> Mapping[str, Any] | None:
    return deepcopy(next((item["inputSchema"] for item in _TOOL_DEFS if item["name"] == name), None))


def _failure(code: ErrorCode, message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(err(code, "general", message)))],
        is_error=True,
    )


def _serialize_result(result: Any) -> CallToolResult:
    if not isinstance(result, dict) or set(result) != {"ok", "data", "error"}:
        raise ValueError("Invalid result envelope")
    if type(result["ok"]) is not bool:
        raise ValueError("Invalid success marker")
    if result["ok"]:
        if result["error"] is not None:
            raise ValueError("Inconsistent success envelope")
    elif result["data"] is not None or not isinstance(result["error"], dict):
        raise ValueError("Inconsistent failure envelope")
    elif (
        result["error"].get("code") not in {str(code) for code in ErrorCode}
        or not isinstance(result["error"].get("message"), str)
    ):
        raise ValueError("Invalid failure details")
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result, indent=2, allow_nan=False))],
        is_error=not result["ok"],
    )


async def list_tools(ctx, params) -> ListToolsResult:
    """Return the complete engine catalog with protocol annotations."""
    return ListToolsResult(tools=[Tool.model_validate(item) for item in _TOOL_DEFS])


async def list_prompts(ctx, params) -> ListPromptsResult:
    """Return the engine's parameterized workflow prompts."""
    return ListPromptsResult(prompts=[Prompt.model_validate(item) for item in prompts_module.PROMPTS])


async def get_prompt(ctx, params) -> GetPromptResult:
    """Render a known prompt without reflecting malformed input in errors."""
    try:
        body = prompts_module.render(params.name, params.arguments)
        return GetPromptResult(
            description=next(p["description"] for p in prompts_module.PROMPTS if p["name"] == params.name),
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=body))],
        )
    except Exception:
        logging.getLogger(__name__).exception("Prompt request failed.", exc_info=False)
        raise MCPError(-32602, "Invalid prompt or arguments.") from None


@asynccontextmanager
async def _lifespan(instance):
    yield {"provider_gate": asyncio.Semaphore(4)}


def _execute(name: str, arguments: dict[str, Any]) -> CallToolResult:
    config = load_config()
    clients = build_clients(config)
    return _serialize_result(dispatch(name, arguments, config, clients))


async def call_tool(ctx, params) -> CallToolResult:
    """Validate first, then dispatch once within the bounded worker pool."""
    try:
        schema = _tool_schema(params.name)
        arguments = {} if params.arguments is None else params.arguments
        if schema is None or not isinstance(arguments, dict):
            return _failure(ErrorCode.INVALID_INPUT, "Invalid tool or arguments.")
        Draft202012Validator(schema).validate(arguments)
        json.dumps(arguments, allow_nan=False)
    except (ValidationError, ValueError, TypeError, RecursionError):
        return _failure(ErrorCode.INVALID_INPUT, "Invalid tool or arguments.")
    try:
        async with ctx.lifespan_context["provider_gate"]:
            worker = asyncio.create_task(asyncio.to_thread(partial(_execute, params.name, arguments)))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                # A running provider request cannot be undone. Hold its slot until it finishes.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        logging.getLogger(__name__).exception("Cancelled provider request ended.", exc_info=False)
                        break
                if worker.done() and not worker.cancelled():
                    worker.exception()
                raise
    except Exception:
        logging.getLogger(__name__).exception("Tool request failed.", exc_info=False)
        return _failure(ErrorCode.UPSTREAM_ERROR, "Tool operation could not be completed.")


async def _safe_request(ctx, call_next):
    try:
        return await call_next(ctx)
    except MCPError as exc:
        code = exc.code if exc.code in {-32700, -32600, -32601, -32602, -32603} else -32603
        raise MCPError(code, "Protocol request could not be completed.") from None
    except (ValidationError, ValueError, TypeError):
        if ctx.method == "tools/call":
            return _failure(ErrorCode.INVALID_INPUT, "Invalid tool request.")
        raise MCPError(-32602, "Invalid protocol request.") from None
    except Exception:
        logging.getLogger(__name__).exception("Protocol request failed.", exc_info=False)
        if ctx.method == "tools/call":
            return _failure(ErrorCode.UPSTREAM_ERROR, "Tool request could not be completed.")
        raise MCPError(-32603, "Protocol request could not be completed.") from None


server = Server(
    "seo-mcp", version=__version__, lifespan=_lifespan,
    get_tool_input_schema=_tool_schema,
    on_list_tools=list_tools, on_call_tool=call_tool,
    on_list_prompts=list_prompts, on_get_prompt=get_prompt,
)
server.middleware.insert(0, _safe_request)


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
    base = base.removesuffix(".exe")
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
    _configure_diagnostics()
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
