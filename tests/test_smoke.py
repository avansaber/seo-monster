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
    unknown-tool error. Catches a tool listed but never wired."""
    pytest.importorskip("mcp")
    from seo_mcp import server
    from seo_mcp.config import load_config

    config = load_config(env={}, config_path="/nonexistent.toml")
    for name in server.registered_tool_names():
        result = server.dispatch(name, {}, config, {})
        if not result["ok"]:
            assert result["error"]["code"] != "INVALID_INPUT", (
                f"registered tool {name!r} is not routed in dispatch"
            )
