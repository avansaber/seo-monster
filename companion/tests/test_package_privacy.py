"""Keep internal working documents out of the companion source archive."""

import tomllib
from pathlib import Path


def test_companion_sdist_excludes_internal_working_documents():
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    excluded = config["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"]
    assert {
        "/CHANGELOG.md",
        "/docs/HOST_VALIDATION.md",
        "/docs/OPENAI_HOST_COMPATIBILITY.md",
        "/docs/ROADMAP.md",
    }.issubset(excluded)
