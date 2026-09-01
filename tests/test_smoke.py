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


def test_legacy_alias_detection():
    pytest.importorskip("mcp")
    from seo_mcp.server import _legacy_alias_invocation

    # Detect the deprecated alias when invoked with its name.
    assert _legacy_alias_invocation("/usr/local/bin/seo-mcp") is True
    assert _legacy_alias_invocation("seo-mcp") is True
    assert _legacy_alias_invocation("seo-mcp.exe") is True  # Windows console script

    # Do NOT flag the canonical name or unrelated argv0 values.
    assert _legacy_alias_invocation("/usr/local/bin/seo-monster") is False
    assert _legacy_alias_invocation("seo-monster.exe") is False
    assert _legacy_alias_invocation("python") is False
    assert _legacy_alias_invocation(None) is False
    assert _legacy_alias_invocation("") is False


def test_legacy_alias_warning_emitted(capsys):
    pytest.importorskip("mcp")
    from seo_mcp.server import _warn_if_legacy_alias

    # Canonical name: nothing on stderr.
    _warn_if_legacy_alias("/path/to/seo-monster")
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""

    # Deprecated alias: one-line stderr notice.
    _warn_if_legacy_alias("/path/to/seo-mcp")
    captured = capsys.readouterr()
    assert "deprecation" in captured.err
    assert "seo-monster" in captured.err
    # And nothing on stdout (stdio protocol channel must stay clean).
    assert captured.out == ""


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


def test_annotation_specific_overrides_match_research_proposal_matrix():
    """Pinned to RESEARCH-AND-PROPOSAL.md §5.1 mapping. Two tools have
    non-default annotation values that the §5.1 matrix specifies explicitly;
    if either drifts back to a default we want a loud failure here. Round-5
    validation §10a.i caught system_status.openWorldHint=true (should be
    false because system_status reports server-internal state); the previous
    regression test only checked presence + the destructive/readOnly
    contradiction, missing this semantic case."""
    pytest.importorskip("mcp")
    from seo_mcp import server

    by_name = {d["name"]: d for d in server._TOOL_DEFS}

    # system_status: openWorldHint=false (server-internal state, not external)
    assert by_name["system_status"]["annotations"]["openWorldHint"] is False, (
        "system_status reports server-internal state and must have "
        "openWorldHint=false per RESEARCH §5.1"
    )

    # Cloudflare cache-purge tools: destructiveHint=true
    for purge in ("cf_purge_cache", "cf_purge_cache_all"):
        assert by_name[purge]["annotations"]["destructiveHint"] is True, (
            f"{purge} must have destructiveHint=true per RESEARCH §5.1"
        )
        assert by_name[purge]["annotations"]["readOnlyHint"] is False, (
            f"{purge} must have readOnlyHint=false"
        )


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


def test_manifest_tool_list_matches_tool_registry():
    """manifest.json must advertise exactly the tools the server registers.

    This drifted silently for roughly ten releases (63 advertised vs 70
    registered), which affects the .mcpb bundle catalog and every directory
    submission that reads the manifest. Asserting against the registry rather
    than a hardcoded count keeps the two in lockstep from here on.
    """
    import json

    from seo_mcp import server

    with open(os.path.join(REPO_ROOT, "manifest.json")) as fh:
        manifest = json.load(fh)
    manifest_names = {t["name"] for t in manifest["tools"]}
    code_names = {t["name"] for t in server._TOOL_DEFS}
    assert manifest_names == code_names


def test_version_stamps_agree():
    """Every version stamp must match, including the ones release.yml gates on.

    release.yml hard-gates pyproject / __init__ / manifest and fails *after*
    the tag is pushed, so a mismatch is expensive. server.json is not gated
    there, which is how the MCP registry entry drifted.
    """
    import json

    import tomllib as _tomllib

    from seo_mcp import __version__

    with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as fh:
        pyproject_version = _tomllib.load(fh)["project"]["version"]
    with open(os.path.join(REPO_ROOT, "manifest.json")) as fh:
        manifest = json.load(fh)
    with open(os.path.join(REPO_ROOT, "server.json")) as fh:
        server_json = json.load(fh)

    assert pyproject_version == __version__
    assert manifest["version"] == __version__
    assert server_json["version"] == __version__
    for pkg in server_json.get("packages", []):
        assert pkg["version"] == __version__


def test_server_json_description_fits_registry_limit():
    """server.json's description must satisfy the MCP registry's schema.

    The registry rejects a publish with HTTP 422 when description exceeds 100
    characters. That only surfaces at publish time, long after review, so it is
    asserted here instead. (Learned the hard way: a 183-character description
    was merged and then bounced by the registry.)
    """
    import json

    with open(os.path.join(REPO_ROOT, "server.json")) as fh:
        server_json = json.load(fh)
    description = server_json["description"]
    assert 0 < len(description) <= 100, (
        f"server.json description is {len(description)} chars; the MCP registry "
        f"schema allows at most 100"
    )
