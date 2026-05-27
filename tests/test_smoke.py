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


def test_full_v1_surface_registered():
    pytest.importorskip("mcp")
    from seo_mcp import server

    names = set(server.registered_tool_names())
    expected = {
        "system_status",
        # GSC (Phase 2)
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
    assert names == expected
    # Full v1 surface from DESIGN.md: 22 tools.
    assert len(names) == 22
