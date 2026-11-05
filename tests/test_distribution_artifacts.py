"""Release inventories must keep engine artifacts independent and free of local records."""
from __future__ import annotations

import io
import stat
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
NEGATED_JSON_NAMES = (".mcp.json", "manifest.json", "package.json", "server.json")


def archive(path: Path, member: str) -> None:
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "w:gz") as stream:
            entry = tarfile.TarInfo("seo_monster-0.9.3/" + member)
            content = b"synthetic archive fixture\n"
            entry.size = len(content)
            stream.addfile(entry, io.BytesIO(content))
    else:
        with zipfile.ZipFile(path, "w") as stream:
            stream.writestr(member, "synthetic archive fixture\n")


def check(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/check_distribution.py"), "--artifact", str(path)],
                          capture_output=True, text=True, check=False, timeout=10)


@pytest.mark.parametrize("suffix", [".whl", ".tar.gz", ".mcpb"])
def test_clean_engine_artifact_inventory_is_accepted(tmp_path, suffix):
    path = tmp_path / ("engine" + suffix)
    archive(path, "src/seo_mcp/server.py")
    assert check(path).returncode == 0


@pytest.mark.parametrize("suffix", [".whl", ".tar.gz", ".mcpb"])
@pytest.mark.parametrize("member", [
    "companion/plugins/seomonster-workbench/.mcp.json",
    ".private/fixture/.mcp.json", "archive/fixture/.mcp.json", "marketing/fixture/.mcp.json",
    "docs/IMPLEMENTATION_REPORT.md", "HANDOFF.md", "CLAUDE.md", "../outside.py",
])
def test_artifact_inventory_refuses_private_or_companion_members(tmp_path, suffix, member):
    path = tmp_path / ("engine" + suffix)
    archive(path, member)
    result = check(path)
    assert result.returncode == 1
    assert "not distributable" in result.stderr
    assert member not in result.stderr


def test_invalid_artifact_has_a_fixed_error(tmp_path):
    path = tmp_path / "engine.whl"
    path.write_text("synthetic invalid archive")
    result = check(path)
    assert result.returncode == 2
    assert "Cannot inspect artifact inventory" in result.stderr
    assert str(path) not in result.stderr


@pytest.mark.parametrize("suffix", [".whl", ".tar.gz", ".mcpb"])
@pytest.mark.parametrize("member", [
    ".private\\fixture\\.mcp.json", "companion\\plugins\\fixture\\.mcp.json",
    "..\\outside.py", "src\\seo_mcp\\server.py", "C:/outside.py",
])
def test_artifact_inventory_refuses_non_portable_member_names(tmp_path, suffix, member):
    path = tmp_path / ("engine" + suffix)
    archive(path, member)
    result = check(path)
    assert result.returncode == 1
    assert "not distributable" in result.stderr
    assert member not in result.stderr


def link_archive(path: Path, kind: str) -> None:
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "w:gz") as stream:
            entry = tarfile.TarInfo("seo_monster-0.9.3/src/seo_mcp/safe.py")
            entry.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE, "fifo": tarfile.FIFOTYPE}[kind]
            entry.linkname = "../.private/synthetic" if kind == "symlink" else ""
            if kind == "hardlink":
                entry.linkname = "seo_monster-0.9.3/src/seo_mcp/server.py"
            stream.addfile(entry)
    else:
        with zipfile.ZipFile(path, "w") as stream:
            info = zipfile.ZipInfo("src/seo_mcp/safe.py")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            stream.writestr(info, "../.private/synthetic")


@pytest.mark.parametrize(("suffix", "kind"), [
    (".whl", "symlink"), (".mcpb", "symlink"),
    (".tar.gz", "symlink"), (".tar.gz", "hardlink"), (".tar.gz", "fifo"),
])
def test_artifact_inventory_refuses_links_and_special_members(tmp_path, suffix, kind):
    path = tmp_path / ("engine" + suffix)
    link_archive(path, kind)
    result = check(path)
    assert result.returncode == 1
    assert "not distributable" in result.stderr
    assert "synthetic" not in result.stderr


@pytest.mark.parametrize("suffix", [".whl", ".tar.gz", ".mcpb"])
def test_regular_unix_files_and_directories_are_accepted(tmp_path, suffix):
    path = tmp_path / ("engine" + suffix)
    if suffix == ".tar.gz":
        with tarfile.open(path, "w:gz") as stream:
            folder = tarfile.TarInfo("seo_monster-0.9.3/src")
            folder.type = tarfile.DIRTYPE
            stream.addfile(folder)
            entry = tarfile.TarInfo("seo_monster-0.9.3/src/seo_mcp/server.py")
            stream.addfile(entry, io.BytesIO(b""))
    else:
        with zipfile.ZipFile(path, "w") as stream:
            info = zipfile.ZipInfo("seo_mcp/server.py")
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            stream.writestr(info, "")
            stream.writestr("seo_mcp/", "")
    assert check(path).returncode == 0


def test_engine_builds_use_a_source_allowlist_and_drop_nested_negated_json():
    targets = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["targets"]
    allowlist = targets["sdist"]["only-include"]
    roots = {PurePosixPath(entry).parts[0] for entry in allowlist}
    assert not roots & {"companion", "archive", "marketing", ".private", ".claude", ".cursor", ".config",
                        ".credentials", "build", "dist", "venv", ".venv", "node_modules"}
    assert all((ROOT / entry).exists() for entry in allowlist)
    for name in NEGATED_JSON_NAMES:
        assert f"*/**/{name}" in targets["sdist"]["exclude"]
        assert f"*/**/{name}" in targets["wheel"]["exclude"]


def test_ci_builds_engine_artifacts_from_a_dirty_tree_and_checks_them():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "ci-dirty-plant" in workflow
    assert "uv build --sdist --wheel" in workflow
    assert "scripts/check_distribution.py --artifact" in workflow
