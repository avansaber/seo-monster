"""Scaffold smoke tests.

Structural checks on the server module and packaging, plus a routing
consistency check that every advertised tool is actually dispatched. The
structural checks parse source with AST so they hold even without the mcp SDK;
the routing check imports the server (skipped if mcp is somehow absent).
"""

from __future__ import annotations

import ast
import os
import tomllib

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PATH = os.path.join(REPO_ROOT, "src", "seo_mcp", "server.py")
PYPROJECT_PATH = os.path.join(REPO_ROOT, "pyproject.toml")


def _server_tree() -> ast.Module:
    with open(SERVER_PATH) as f:
        return ast.parse(f.read())


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
    return out


def test_server_declares_core_functions():
    funcs = _functions(_server_tree())
    for name in ("list_tools", "call_tool", "dispatch", "build_clients", "main"):
        assert name in funcs, f"server.py missing {name}"


def test_list_and_call_tool_are_async():
    funcs = _functions(_server_tree())
    assert isinstance(funcs["list_tools"], ast.AsyncFunctionDef)
    assert isinstance(funcs["call_tool"], ast.AsyncFunctionDef)


def test_pyproject_declares_console_script_and_python_version():
    with open(PYPROJECT_PATH, "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["scripts"]["seo-mcp"] == "seo_mcp.server:main"
    assert data["project"]["requires-python"] == ">=3.11"


def test_pyproject_declares_lean_dependencies():
    with open(PYPROJECT_PATH, "rb") as f:
        data = tomllib.load(f)
    deps = " ".join(data["project"]["dependencies"]).lower()
    assert "mcp" in deps
    assert "google-api-python-client" in deps
    # PSI and Cloudflare ride on stdlib urllib: no http client dependency.
    assert "requests" not in deps
    assert "httpx" not in deps


def test_system_status_is_registered_in_phase_1():
    from seo_mcp.tools.system_status import TOOL

    assert TOOL["name"] == "system_status"


def test_every_registered_tool_is_routed():
    """Each advertised tool must be dispatched, not fall through to the
    unknown-tool error. Catches a tool listed but never wired. A tool's own
    INVALID_INPUT (e.g. a missing required arg) is fine; only the unknown-tool
    fall-through (identified by its message) is a failure."""
    pytest.importorskip("mcp")
    from seo_mcp import server
    from seo_mcp.config import load_config

    config = load_config(env={}, config_path="/nonexistent.toml")
    for name in server.registered_tool_names():
        result = server.dispatch(name, {}, config, {})
        if not result["ok"]:
            assert "Unknown tool" not in (result["error"]["message"] or ""), (
                f"registered tool {name!r} is not routed in dispatch"
            )


def test_every_tool_carries_mcp_annotations():
    """MCP 2025-03-26 introduced tool annotations. Anthropic's Connectors
    Directory rejects ~30% of submissions for missing them. This test guards
    against any future tool sliding in without the four hint fields, so we
    cannot regress on the Directory submission criteria."""
    pytest.importorskip("mcp")
    from seo_mcp import server

    required = {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
    for tool_def in server._TOOL_DEFS:
        name = tool_def["name"]
        assert "annotations" in tool_def, f"{name}: missing annotations block"
        keys = set(tool_def["annotations"].keys())
        missing = required - keys
        assert not missing, f"{name}: annotations missing fields {missing}"
        # Spec-correctness: a destructive tool cannot also be readOnly.
        a = tool_def["annotations"]
        if a.get("destructiveHint"):
            assert not a.get("readOnlyHint"), f"{name}: destructive + readOnly is contradictory"


def test_full_v02_surface_registered():
    pytest.importorskip("mcp")
    from seo_mcp import server

    names = set(server.registered_tool_names())
    expected = {
        "system_status",
        # GSC v1 (Phase 2)
        "gsc_list_properties",
        "gsc_search_analytics",
        "gsc_top_queries",
        "gsc_top_pages",
        "gsc_compare_periods",
        "gsc_inspect_url",
        "gsc_batch_inspect_urls",
        "gsc_list_sitemaps",
        "gsc_submit_sitemap",
        "gsc_request_indexing",
        # GSC v0.2.0 query intelligence
        "gsc_query_opportunities",
        "gsc_query_gaps",
        "gsc_new_queries",
        "gsc_top_pages_by_query",
        # PSI (Phase 2)
        "psi_analyze",
        # GA4 (Phase 3)
        "ga4_run_report",
        "ga4_top_landing_pages",
        "ga4_traffic_by_channel",
        "ga4_organic_search_overview",
        # Cloudflare (Phase 4)
        "cf_list_zones",
        "cf_zone_info",
        "cf_list_dns",
        "cf_web_analytics",
        "cf_purge_cache",
        "cf_purge_cache_all",
    }
    # v0.2.0 baseline: 26 tools. IndexNow lands in commit 3/6 of this sprint
    # and will push the assertion to 28.
    assert expected <= names, f"missing: {expected - names}"
