"""Offline packaging contract; remote schemas are checked separately at release review."""

import json
from pathlib import Path
import tomllib
import subprocess

import pytest

from seomonster_workbench.portable import validate_portable_export


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "seomonster-workbench"


def test_plugin_portable_and_codex_manifests_agree():
    portable = json.loads((PLUGIN / "plugin.json").read_text())
    legacy = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert portable["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert portable["name"] == legacy["name"] == project["name"]
    assert portable["version"] == legacy["version"] == project["version"]
    assert portable["license"] == legacy["license"] == "MIT"
    assert (PLUGIN / "LICENSE").is_file()
    assert (PLUGIN / legacy["skills"] / "seo-evidence-loop" / "SKILL.md").is_file()
    assert len(legacy["interface"]["defaultPrompt"]) == 3
    assert "Write" in legacy["interface"]["capabilities"]  # local record writes are not mislabeled read-only
    for manifest in (portable, legacy):
        validate_portable_export(manifest)


def test_plugin_uses_installed_entrypoint_without_paths_secrets_or_remote_url():
    portable = json.loads((PLUGIN / "mcp.json").read_text())
    legacy = json.loads((PLUGIN / ".mcp.json").read_text())
    assert portable["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    assert set(portable["mcpServers"]) == set(legacy["mcpServers"]) == {"seomonster-workbench"}
    server = portable["mcpServers"]["seomonster-workbench"]
    assert server == {"type": "stdio", "command": "sm-workbench-mcp", "args": []}
    assert legacy["mcpServers"]["seomonster-workbench"] == {k: v for k, v in server.items() if k != "type"}
    assert "sm-workbench-mcp" in tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]


def test_required_distributable_json_is_not_gitignored():
    if not (ROOT.parent / ".git").exists():
        pytest.skip("source archive has no Git ignore rules")
    reviewed = [*ROOT.glob("schemas/*.json"), *ROOT.glob("evaluation/**/*.json"),
                *PLUGIN.glob("*.json"), PLUGIN / ".codex-plugin" / "plugin.json", ROOT / "examples/marketplace.json"]
    result = subprocess.run(["git", "check-ignore", "--no-index", *[str(p.relative_to(ROOT.parent)) for p in reviewed]],
                            cwd=ROOT.parent, capture_output=True, text=True)
    assert result.returncode == 1 and not result.stdout, "Required fixtures/manifests would be omitted from Git"
