"""Public engine metadata and local coordination-file exclusion contracts."""

import json
import tomllib
from pathlib import Path

from seo_mcp import __version__
from seo_mcp.errors import DOCS_BASE

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = "https://www.avansaber.com/seomonster/"


def test_canonical_metadata_and_cursor_version_agree():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    manifest = json.loads((ROOT / "manifest.json").read_text())
    registry = json.loads((ROOT / "server.json").read_text())
    cursor = json.loads((ROOT / ".cursor-plugin/plugin.json").read_text())
    assert project["project"]["urls"]["Homepage"] == manifest["homepage"] == registry["websiteUrl"] == cursor["homepage"] == CANONICAL
    assert project["project"]["urls"]["Documentation"] == manifest["documentation"] == CANONICAL + "docs/"
    assert cursor["version"] == registry["version"] == manifest["version"] == __version__
    assert "seomonster.avansaber.com" not in (ROOT / "README.md").read_text()
    assert DOCS_BASE == "https://github.com/avansaber/seo-monster/blob/main/README.md#"


def test_coordination_records_cannot_enter_engine_artifacts():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    source_exclusions = project["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    bundle_exclusions = (ROOT / ".mcpbignore").read_text().splitlines()
    for name in ("CLAUDE.md", "HANDOFF.md", ".private/", "companion/"):
        assert name in source_exclusions
        assert name in bundle_exclusions


def test_all_optional_provider_destinations_are_disclosed():
    privacy = (ROOT / "PRIVACY.md").read_text()
    readme = (ROOT / "README.md").read_text()
    for provider in ("CrUX", "DataForSEO", "Open PageRank", "Perplexity", "OpenAI", "Anthropic", "Gemini", "Google Ads"):
        assert provider in privacy and provider in readme
    for host in ("api.dataforseo.com", "openpagerank.com", "api.perplexity.ai", "api.openai.com",
                 "api.anthropic.com", "generativelanguage.googleapis.com"):
        assert host in privacy


def test_privacy_separates_no_maintainer_collection_from_local_storage():
    privacy = " ".join((ROOT / "PRIVACY.md").read_text().split())
    assert "The engine stores Google OAuth credentials locally" in privacy
    assert "AvanSaber Inc. does not collect" in privacy
    assert "workbench.db" in privacy
    assert ".seomonster-workbench.db" in privacy
    assert "exports and backups" in privacy
