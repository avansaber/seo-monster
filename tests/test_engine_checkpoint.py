"""Engine checkpoint contracts and Ruff migration regressions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sys
import tomllib
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import seo_mcp
from seo_mcp import server
from seo_mcp.tools import keyword_universe_tools
from seo_mcp.tools._helpers import preflight_get
from seo_mcp.tools._scoring import _clamp01
from seo_mcp.tools.system_status import _probe

ROOT = Path(__file__).resolve().parents[1]
TOOL_HASH = "7291eeda75d09e1dcbdcfd83a7d5401a9a1344b303889c5c1dec50af21db74f4"
PROMPT_HASH = "f62e91092ef63dc149e35e56fce12fa69be54a3bb860d4d586a2e0a646191522"


def _digest(value):
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _assert_contract(tools, prompts):
    assert len(tools) == 70
    assert len(prompts) == 13
    assert _digest(tools) == TOOL_HASH
    assert _digest(prompts) == PROMPT_HASH


def _registry():
    async def collect():
        tools = await server.list_tools()
        prompts = await server.list_prompts()
        return (
            [tool.model_dump(mode="json", exclude_none=True) for tool in tools],
            [prompt.model_dump(mode="json", exclude_none=True) for prompt in prompts],
        )

    return asyncio.run(collect())


def test_supported_mcp_bound_and_ruff_migration_policy():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "mcp>=1.0.0,<2" in project["project"]["dependencies"]
    assert "ruff>=0.17,<0.18" in project["project"]["optional-dependencies"]["dev"]
    policy = project["tool"]["ruff"]
    assert policy["required-version"] == ">=0.17,<0.18"
    assert not policy.get("lint", {}).get("ignore")
    assert not policy.get("lint", {}).get("per-file-ignores")
    assert not policy.get("lint", {}).get("select")


def _assert_locked_tools(lock):
    packages = {package["name"]: package for package in lock["package"]}
    assert packages["ruff"]["version"].startswith("0.17.")
    assert packages["mcp"]["version"].startswith("1.")
    requirements = packages["seo-monster"]["metadata"]["requires-dist"]
    assert {"name": "ruff", "marker": "extra == 'dev'", "specifier": ">=0.17,<0.18"} in requirements
    assert {"name": "mcp", "specifier": ">=1.0.0,<2"} in requirements


def test_lockfile_matches_supported_linter_and_sdk():
    _assert_locked_tools(tomllib.loads((ROOT / "uv.lock").read_text()))


@pytest.mark.parametrize(("package", "version"), [("ruff", "0.15.15"), ("mcp", "2.3.0")])
def test_lock_guard_rejects_stale_linter_and_sdk_major_mutations(package, version):
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    next(item for item in lock["package"] if item["name"] == package)["version"] = version
    with pytest.raises(AssertionError):
        _assert_locked_tools(lock)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("_STOPWORDS", "31be69d9b7bcfd1d18001f58b364e0a9b9058ee088392b6ea45b7bffc66f9078"),
        ("_ENTITY_STOPWORDS", "4603ca62e4f13e4d8d405f19d3b2c22b83e0d9e538bfb97f968b4f4ce0dd931f"),
        ("_GAP_CONNECTIVES", "12df16d005772081d17c77c5b9a310047d599d2d773171c1345cb4b98d3ccb0a"),
    ],
)
def test_lexical_word_sets_survive_the_lint_migration(name, expected):
    from seo_mcp.tools import _html

    module = keyword_universe_tools if name == "_GAP_CONNECTIVES" else _html
    assert _digest(sorted(getattr(module, name))) == expected

def test_registry_preserves_names_descriptions_schemas_and_annotations():
    _assert_contract(*_registry())


@pytest.mark.parametrize("mutation", ["remove_tool", "tool_schema", "prompt_description"])
def test_registry_guard_detects_contract_mutations(mutation):
    tools, prompts = _registry()
    if mutation == "remove_tool":
        tools.pop()
    elif mutation == "tool_schema":
        tools[0]["inputSchema"]["additionalProperties"] = True
    else:
        prompts[0]["description"] += " changed"
    with pytest.raises(AssertionError):
        _assert_contract(tools, prompts)


@pytest.mark.parametrize("launcher", ["module", "canonical", "legacy"])
def test_stdio_handshake_registry_and_safe_envelopes(tmp_path, launcher):
    config = tmp_path / "empty.toml"
    config.write_text("")

    async def exercise():
        console = "seo-monster" if launcher == "canonical" else "seo-mcp"
        suffix = ".exe" if sys.platform == "win32" else ""
        command = str(Path(sys.executable).with_name(console + suffix))
        params = StdioServerParameters(
            command=sys.executable if launcher == "module" else command,
            args=["-m", "seo_mcp.server"] if launcher == "module" else [],
            cwd=str(tmp_path),
            env={
                "SEO_MCP_CONFIG": str(config),
                "PYTHONPATH": str(Path(seo_mcp.__file__).resolve().parent.parent),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            prompts = await session.list_prompts()
            results = [
                await session.call_tool("system_status", {"probe": False}),
                await session.call_tool("__checkpoint_unknown_tool__", {}),
            ]
            return initialized, tools, prompts, results

    initialized, tools, prompts, results = asyncio.run(
        asyncio.wait_for(exercise(), timeout=20),
    )
    assert initialized.serverInfo.name == "seo-mcp"
    _assert_contract(
        [tool.model_dump(mode="json", exclude_none=True) for tool in tools.tools],
        [prompt.model_dump(mode="json", exclude_none=True) for prompt in prompts.prompts],
    )
    for result in results:
        assert result.isError is False
        assert sorted(json.loads(result.content[0].text)) == ["data", "error", "ok"]
    observed, rejected = [json.loads(result.content[0].text) for result in results]
    assert observed["ok"] is True and observed["error"] is None
    assert rejected["ok"] is False and rejected["data"] is None
    assert rejected["error"]["code"] == "INVALID_INPUT"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("widget.example", "widget.example"),
        ("web.example", "web.example"),
        ("www.widget.example", "widget.example"),
        ("https://www.widget.example/page", "widget.example"),
        ("www.www.example", "www.example"),
        ("example.org", "example.org"),
        ("sc-domain:widget.example", "widget.example"),
    ],
)
def test_keyword_domain_removes_only_one_literal_www_prefix(value, expected):
    assert keyword_universe_tools._domain(value) == expected


def test_keyword_provider_receives_the_intended_domain(make_config):
    class SavedProvider:
        def __init__(self):
            self.domains = []

        def ranked_keywords(self, domain, **_):
            self.domains.append(domain)
            return []

    provider = SavedProvider()
    result = keyword_universe_tools.keyword_universe(
        {"target_domain": "widget.example", "competitors": ["www.web.example"]},
        make_config(),
        {"dataforseo": provider},
    )
    assert result["ok"] is True
    assert provider.domains == ["widget.example", "web.example"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), -0.0, 0.4])
def test_clamp_migration_preserves_numeric_edge_behaviour(value):
    actual = _clamp01(value)
    if math.isnan(value):
        assert math.isnan(actual)
    elif math.isinf(value):
        assert actual == (1.0 if value > 0 else 0.0)
    elif value == 0:
        assert math.copysign(1, actual) == math.copysign(1, value)
    else:
        assert actual == value


def test_status_and_preflight_keep_optional_provider_failure_boundaries():
    class Unavailable(dict):
        def get(self, *_):
            raise RuntimeError("synthetic builder failure")

    class FailedProvider:
        def probe(self):
            raise RuntimeError("synthetic probe failure")

        def fetch(self, *_args, **_kwargs):
            raise RuntimeError("synthetic fetch failure")

    assert _probe(Unavailable(), "gsc", False) is None
    assert _probe(Unavailable(), "gsc", True) is False
    assert _probe({"gsc": FailedProvider()}, "gsc", True) is False
    assert preflight_get(Unavailable(), "https://example.invalid/") is None
    result = preflight_get({"http": FailedProvider()}, "https://example.invalid/")
    assert result == (None, "", "synthetic fetch failure")
