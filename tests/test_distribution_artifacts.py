"""Release inventories must keep engine artifacts independent and free of local records."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tomllib
import warnings
import zipfile
import zlib
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


# --- Canonical artifact identity -------------------------------------------------
# Synthetic private text is planted only in metadata that the identity must refuse;
# every refusal must use a fixed reason and never echo it.
MARKER = "syntheticprivatemarker7f3a"


def load_guard():
    spec = importlib.util.spec_from_file_location("check_distribution_under_test", ROOT / "scripts/check_distribution.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def zip_bytes(entries, *, comment=b"", compression=zipfile.ZIP_DEFLATED, level=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression, compresslevel=level) as stream:
        stream.comment = comment
        for entry in entries:
            info = zipfile.ZipInfo(entry["name"], date_time=entry.get("date", (2026, 1, 2, 3, 4, 6)))
            info.create_system = entry.get("system", 3)
            default_mode = 0o40755 if entry["name"].endswith("/") else 0o100644
            info.external_attr = entry.get("mode", default_mode) << 16
            info.extra = entry.get("extra", b"")
            info.comment = entry.get("comment", b"")
            info.compress_type = compression
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)  # deliberate duplicate-name fixtures
                stream.writestr(info, entry.get("data", b""))
    return buffer.getvalue()


def gzip_wrap(raw, *, flags=0, header_tail=b""):
    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = compressor.compress(raw) + compressor.flush()
    return (b"\x1f\x8b\x08" + bytes([flags]) + b"\x00\x00\x00\x00\x02\xff" + header_tail
            + body + struct.pack("<II", zlib.crc32(raw), len(raw) & 0xFFFFFFFF))


def tar_raw(entries, *, fmt=tarfile.USTAR_FORMAT, global_pax=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=fmt, pax_headers=global_pax or {}) as stream:
        for entry in entries:
            info = tarfile.TarInfo(entry["name"])
            info.type = entry.get("type", tarfile.REGTYPE)
            info.mode = entry.get("mode", 0o755 if info.type == tarfile.DIRTYPE else 0o644)
            info.mtime = entry.get("mtime", 1700000000)
            info.uname, info.gname = entry.get("uname", ""), entry.get("gname", "")
            info.uid, info.gid = entry.get("uid", 0), entry.get("gid", 0)
            info.linkname = entry.get("linkname", "")
            info.pax_headers = entry.get("pax", {})
            data = entry.get("data", b"")
            info.size = len(data)
            stream.addfile(info, io.BytesIO(data) if data else None)
    return buffer.getvalue()


def tar_bytes(entries, **options):
    return gzip_wrap(tar_raw(entries, **options))


def retar_header(raw, offset, start, value):
    """Patch one header field and recompute its checksum so only the planted bytes differ."""
    block = bytearray(raw[offset:offset + 512])
    block[start:start + len(value)] = value
    block[148:156] = b" " * 8
    block[148:156] = b"%06o\0 " % sum(block)
    return raw[:offset] + bytes(block) + raw[offset + 512:]


def write(tmp_path, name, data, folder="a"):
    path = tmp_path / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def canonical(path, *extra):
    guard = load_guard()
    out, err = io.StringIO(), io.StringIO()
    stdout, stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = guard.main(["--canonical", str(path), *extra])
    finally:
        sys.stdout, sys.stderr = stdout, stderr
    return code, out.getvalue(), err.getvalue()


def digest_of(path):
    code, out, err = canonical(path)
    assert code == 0, err
    return out.split()[0]


def assert_refused(path, reason):
    code, out, err = canonical(path)
    assert code == 1
    assert err == f"Artifact identity refused: {reason}.\n"
    assert out == ""
    assert MARKER not in out + err and MARKER.upper() not in out + err


FILES = [("pkg/a.py", b"print('synthetic')\n"), ("pkg/b.txt", b"synthetic fixture\n")]


def test_identity_document_serialization_is_exact(tmp_path):
    path = write(tmp_path, "engine.whl", zip_bytes([{"name": "pkg/", "mode": 0o40755}, {"name": "pkg/a.py", "data": b"x\n"}]))
    document = json.dumps({"artifact": "engine.whl", "format": "seomonster-artifact-identity/1", "members": [
        {"mode": "0755", "path": "pkg", "sha256": "", "size": 0, "type": "dir"},
        {"mode": "0644", "path": "pkg/a.py", "sha256": hashlib.sha256(b"x\n").hexdigest(), "size": 2, "type": "file"},
    ]}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    out_dir = tmp_path / "identity"
    out_dir.mkdir()
    code, out, err = canonical(path, "--identity-out", str(out_dir))
    assert code == 0, err
    assert out == f"{hashlib.sha256(document).hexdigest()}  engine.whl\n"
    assert (out_dir / "engine.whl.identity.json").read_bytes() == document


@pytest.mark.parametrize("suffix", [".whl", ".mcpb"])
def test_zip_identity_ignores_timestamps_order_and_compression(tmp_path, suffix):
    first = [{"name": name, "data": data, "date": (2026, 1, 2, 3, 4, 6)} for name, data in FILES]
    second = [{"name": name, "data": data, "date": (2031, 7, 8, 9, 10, 12)} for name, data in reversed(FILES)]
    a = write(tmp_path, "engine" + suffix, zip_bytes(first), "a")
    b = write(tmp_path, "engine" + suffix, zip_bytes(second, compression=zipfile.ZIP_STORED), "b")
    c = write(tmp_path, "engine" + suffix, zip_bytes(first, level=1), "c")
    assert digest_of(a) == digest_of(b) == digest_of(c)


def test_tar_identity_ignores_timestamps_order_and_compression(tmp_path):
    first = [{"name": "seo_monster-9.9.9/" + name, "data": data, "mtime": 1} for name, data in FILES]
    second = [{"name": "seo_monster-9.9.9/" + name, "data": data, "mtime": 2000000000} for name, data in reversed(FILES)]
    raw = tar_raw(second)
    compressor = zlib.compressobj(1, zlib.DEFLATED, -zlib.MAX_WBITS)
    fast = (b"\x1f\x8b\x08\x00\x01\x02\x03\x04\x04\x03" + compressor.compress(raw) + compressor.flush()
            + struct.pack("<II", zlib.crc32(raw), len(raw)))
    a = write(tmp_path, "engine.tar.gz", tar_bytes(first), "a")
    b = write(tmp_path, "engine.tar.gz", fast, "b")
    assert digest_of(a) == digest_of(b)


def zip_variants():
    base = [{"name": name, "data": data} for name, data in FILES]
    yield "byte", [{"name": "pkg/a.py", "data": b"print('synthetic!')\n"}, base[1]]
    yield "added", base + [{"name": "pkg/c.txt", "data": b""}]
    yield "removed", base[:1]
    yield "renamed", [base[0], {"name": "pkg/c.txt", "data": FILES[1][1]}]
    yield "type", base + [{"name": "pkg/d/", "mode": 0o40755}]
    yield "mode", [base[0], {"name": "pkg/b.txt", "data": FILES[1][1], "mode": 0o100755}]


@pytest.mark.parametrize(("label", "entries"), list(zip_variants()))
def test_zip_identity_changes_with_content_membership_type_or_mode(tmp_path, label, entries):
    base = write(tmp_path, "engine.whl", zip_bytes([{"name": n, "data": d} for n, d in FILES]), "a")
    changed = write(tmp_path, "engine.whl", zip_bytes(entries), "b")
    assert digest_of(base) != digest_of(changed)


@pytest.mark.parametrize("label", ["byte", "added", "removed", "renamed", "type", "mode"])
def test_tar_identity_changes_with_content_membership_type_or_mode(tmp_path, label):
    root = "seo_monster-9.9.9/"
    base = [{"name": root + n, "data": d} for n, d in FILES]
    changed = {
        "byte": [{"name": root + "pkg/a.py", "data": b"print('synthetic!')\n"}, base[1]],
        "added": base + [{"name": root + "pkg/c.txt"}],
        "removed": base[:1],
        "renamed": [base[0], {"name": root + "pkg/c.txt", "data": FILES[1][1]}],
        "type": base + [{"name": root + "pkg/d", "type": tarfile.DIRTYPE}],
        "mode": [base[0], {"name": root + "pkg/b.txt", "data": FILES[1][1], "mode": 0o755}],
    }[label]
    a = write(tmp_path, "engine.tar.gz", tar_bytes(base), "a")
    b = write(tmp_path, "engine.tar.gz", tar_bytes(changed), "b")
    assert digest_of(a) != digest_of(b)


def test_parent_directory_entries_with_children_are_accepted(tmp_path):
    zipped = write(tmp_path, "engine.whl", zip_bytes([{"name": "x/"}, {"name": "x/y", "data": b"child\n"}]))
    tarred = write(tmp_path, "engine.tar.gz", tar_bytes([
        {"name": "seo_monster-9.9.9/x", "type": tarfile.DIRTYPE}, {"name": "seo_monster-9.9.9/x/y", "data": b"child\n"}]))
    assert canonical(zipped)[0] == 0
    assert canonical(tarred)[0] == 0


def test_known_valid_zip_timestamp_extra_is_accepted(tmp_path):
    extra = struct.pack("<HHBi", 0x5455, 5, 1, 1700000000)
    path = write(tmp_path, "engine.whl", zip_bytes([{"name": "pkg/a.py", "data": b"x\n", "extra": extra}]))
    assert canonical(path)[0] == 0


@pytest.mark.parametrize(("label", "build", "reason"), [
    ("zip exact duplicate", lambda: zip_bytes([{"name": "a/b", "data": b"1"}, {"name": "a/b", "data": b"2"}]), "duplicate"),
    ("zip case-only duplicate", lambda: zip_bytes([{"name": "a/README", "data": b"1"}, {"name": "a/readme", "data": b"2"}]), "duplicate"),
    ("tar x/ and x directories", lambda: tar_bytes([{"name": "r/x/", "type": tarfile.DIRTYPE}, {"name": "r/x", "type": tarfile.DIRTYPE}]), "duplicate"),
    ("zip a/b and a//b", lambda: zip_bytes([{"name": "a/b", "data": b"1"}, {"name": "a//b", "data": b"2"}]), "name"),
    ("tar a/b and ./a/b", lambda: tar_bytes([{"name": "r/a/b", "data": b"1"}, {"name": "./r/a/b", "data": b"2"}]), "name"),
    ("zip file x plus child x/y", lambda: zip_bytes([{"name": "x", "data": b"1"}, {"name": "x/y", "data": b"2"}]), "collision"),
    ("zip dir x/ plus file x", lambda: zip_bytes([{"name": "x/"}, {"name": "x", "data": b"2"}]), "collision"),
    ("tar file x plus child x/y", lambda: tar_bytes([{"name": "r/x", "data": b"1"}, {"name": "r/x/y", "data": b"2"}]), "collision"),
    ("tar dir x plus file x", lambda: tar_bytes([{"name": "r/x", "type": tarfile.DIRTYPE}, {"name": "r/x", "data": b"2"}]), "collision"),
])
def test_duplicates_and_collisions_are_refused(tmp_path, label, build, reason):
    suffix = ".tar.gz" if label.startswith("tar") else ".whl"
    assert_refused(write(tmp_path, "engine" + suffix, build()), reason)


def zip_with_gap(data):
    """Insert synthetic bytes after the first member and repoint the directory so it still parses."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    second = sorted(archive.infolist(), key=lambda info: info.header_offset)[1]
    gap = MARKER.encode()
    insert_at = second.header_offset
    out = bytearray(data[:insert_at] + gap + data[insert_at:])
    eocd = len(out) - 22
    cd_offset = struct.unpack_from("<I", out, eocd + 16)[0]
    struct.pack_into("<I", out, eocd + 16, cd_offset + len(gap))
    position = cd_offset + len(gap)
    while position < eocd:
        name_length, extra_length, comment_length = struct.unpack_from("<HHH", out, position + 28)
        offset = struct.unpack_from("<I", out, position + 42)[0]
        if offset >= insert_at:
            struct.pack_into("<I", out, position + 42, offset + len(gap))
        position += 46 + name_length + extra_length + comment_length
    return bytes(out)


def zip_flag(data, bit):
    out = bytearray(data)
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = out.find(signature)
        struct.pack_into("<H", out, position + offset, struct.unpack_from("<H", out, position + offset)[0] | bit)
    return bytes(out)


def zip_metadata_cases():
    plain = [{"name": "pkg/a.py", "data": b"x\n"}, {"name": "pkg/b.py", "data": b"y\n"}]
    marker = MARKER.encode()
    yield "archive comment", lambda: zip_bytes(plain, comment=marker)
    yield "member comment", lambda: zip_bytes([{**plain[0], "comment": marker}])
    yield "unknown extra id", lambda: zip_bytes([{**plain[0], "extra": struct.pack("<HH", 0xCAFE, len(marker)) + marker}])
    yield "timestamp extra with invalid payload", lambda: zip_bytes([{**plain[0], "extra": struct.pack("<HHB", 0x5455, 1 + len(marker), 1) + marker}])
    yield "invalid timestamp", lambda: zip_bytes([{**plain[0], "date": (2026, 13, 2, 3, 4, 6)}])
    yield "preamble", lambda: marker + zip_bytes(plain)
    yield "trailer", lambda: zip_bytes(plain) + marker
    yield "gap between members", lambda: zip_with_gap(zip_bytes(plain))
    yield "encrypted flag", lambda: zip_flag(zip_bytes(plain[:1]), 0x1)
    yield "data descriptor flag", lambda: zip_flag(zip_bytes(plain[:1]), 0x8)
    yield "non-empty directory", lambda: zip_bytes([{"name": "pkg/", "data": marker}])
    yield "unix mode on non-unix entry", lambda: zip_bytes([{**plain[0], "system": 0}])


@pytest.mark.parametrize(("label", "build"), list(zip_metadata_cases()))
def test_zip_disclosure_bearing_metadata_is_refused(tmp_path, label, build):
    assert_refused(write(tmp_path, "engine.whl", build()), "metadata")


def test_zip_original_name_with_nul_is_refused(tmp_path):
    data = zip_bytes([{"name": "pkg/aX" + MARKER, "data": b"x\n"}])
    data = data.replace(b"pkg/aX" + MARKER.encode(), b"pkg/a\0" + MARKER.encode())
    assert_refused(write(tmp_path, "engine.mcpb", data), "name")


def tar_metadata_cases():
    root = "seo_monster-9.9.9/"
    plain = {"name": root + "pkg/a.py", "data": b"x\n"}
    long_name = root + "pkg/" + MARKER + "/" + "n" * 90 + ".py"
    yield "uname", lambda: tar_bytes([{**plain, "uname": MARKER}])
    yield "gname", lambda: tar_bytes([{**plain, "gname": MARKER}])
    yield "uid", lambda: tar_bytes([{**plain, "uid": 501}])
    yield "pax comment", lambda: tar_bytes([{**plain, "pax": {"comment": MARKER}}], fmt=tarfile.PAX_FORMAT)
    yield "pax path", lambda: tar_bytes([{**plain, "pax": {"path": plain["name"]}}], fmt=tarfile.PAX_FORMAT)
    yield "global pax", lambda: tar_bytes([plain], fmt=tarfile.PAX_FORMAT, global_pax={"comment": MARKER})
    yield "gnu long name", lambda: tar_bytes([{**plain, "name": long_name}], fmt=tarfile.GNU_FORMAT)
    yield "gnu long link", lambda: tar_bytes([{**plain, "linkname": MARKER * 5}], fmt=tarfile.GNU_FORMAT)
    yield "header slack", lambda: gzip_wrap(retar_header(tar_raw([plain]), 0, 500, MARKER[:12].encode()))
    yield "name field after nul", lambda: gzip_wrap(retar_header(tar_raw([plain]), 0, len(plain["name"]) + 1, MARKER.encode()))
    yield "invalid timestamp", lambda: gzip_wrap(retar_header(tar_raw([plain]), 0, 136, b"\xff" * 12))
    yield "data padding", lambda: gzip_wrap(bytes_at(tar_raw([plain]), 512 + 2, MARKER.encode()))
    yield "after end of archive", lambda: gzip_wrap(tar_raw([plain]) + MARKER.encode())
    yield "non-empty directory", lambda: tar_bytes([{"name": root + "pkg", "type": tarfile.DIRTYPE, "data": MARKER.encode()}])
    yield "gzip file name", lambda: gzip_wrap(tar_raw([plain]), flags=0x08, header_tail=MARKER.encode() + b"\0")
    yield "gzip comment", lambda: gzip_wrap(tar_raw([plain]), flags=0x10, header_tail=MARKER.encode() + b"\0")
    yield "gzip extra", lambda: gzip_wrap(tar_raw([plain]), flags=0x04, header_tail=struct.pack("<H", len(MARKER)) + MARKER.encode())
    yield "concatenated gzip member", lambda: tar_bytes([plain]) + gzip_wrap(MARKER.encode())
    yield "trailing bytes after gzip", lambda: tar_bytes([plain]) + MARKER.encode()


def bytes_at(raw, offset, value):
    return raw[:offset] + value + raw[offset + len(value):]


@pytest.mark.parametrize(("label", "build"), list(tar_metadata_cases()))
def test_tar_disclosure_bearing_metadata_is_refused(tmp_path, label, build):
    assert_refused(write(tmp_path, "engine.tar.gz", build()), "metadata")


@pytest.mark.parametrize("bits", [0o4000, 0o2000, 0o1000])
def test_special_permission_bits_are_refused(tmp_path, bits):
    zipped = write(tmp_path, "engine.whl", zip_bytes([{"name": "pkg/a.py", "data": b"x", "mode": 0o100644 | bits}]))
    tarred = write(tmp_path, "engine.tar.gz", tar_bytes([{"name": "r/pkg/a.py", "data": b"x", "mode": 0o644 | bits}]))
    assert_refused(zipped, "permission")
    assert_refused(tarred, "permission")


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.CONTTYPE])
def test_unsupported_tar_member_types_are_refused(tmp_path, kind):
    entry = {"name": "r/pkg/a.py", "type": kind}
    if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
        entry["linkname"] = "r/" + MARKER
    assert_refused(write(tmp_path, "engine.tar.gz", tar_bytes([entry])), "type")


@pytest.mark.parametrize(("name", "mode"), [
    ("pkg/a.py", stat.S_IFLNK | 0o777), ("pkg/a.py", stat.S_IFSOCK | 0o644), ("pkg/a.py", stat.S_IFIFO | 0o644),
    ("pkg/a.py", stat.S_IFDIR | 0o755), ("pkg/", stat.S_IFREG | 0o644),
])
def test_unsupported_zip_member_types_are_refused(tmp_path, name, mode):
    assert_refused(write(tmp_path, "engine.whl", zip_bytes([{"name": name, "mode": mode}])), "type")


@pytest.mark.parametrize("name", ["pkg\\a.py", "C:/a.py", "/abs/a.py", "../a.py", "pkg/./a.py", "pkg//a.py",
                                  "pkg/a\n.py", "pkg/\u00e9.py", "pkg/a.py/"])
def test_non_portable_member_names_are_refused(tmp_path, name):
    zipped = zip_bytes([{"name": name, "data": b"x"}]) if not name.endswith("/") else zip_bytes([{"name": name, "mode": stat.S_IFREG | 0o644}])
    path = write(tmp_path, "engine.whl", zipped)
    code, out, err = canonical(path)
    assert code == 1 and out == ""
    assert err in {"Artifact identity refused: name.\n", "Artifact identity refused: type.\n"}


def test_tar_non_portable_names_are_refused(tmp_path):
    for index, name in enumerate(["/abs/a.py", "r/../a.py", "r/a\\b.py", "r/C:a.py"]):
        assert_refused(write(tmp_path, "engine.tar.gz", tar_bytes([{"name": name, "data": b"x"}]), str(index)), "name")


def test_private_members_are_refused_by_identity(tmp_path):
    assert_refused(write(tmp_path, "engine.whl", zip_bytes([{"name": ".private/a.md", "data": b"x"}])), "private")


def test_unreadable_artifact_has_a_fixed_refusal(tmp_path):
    assert_refused(write(tmp_path, "engine.whl", MARKER.encode()), "metadata")
    assert_refused(write(tmp_path, "engine.tar.gz", MARKER.encode(), "b"), "metadata")
    assert_refused(write(tmp_path, "engine.zip", MARKER.encode(), "c"), "unreadable")


# --- Release tag binding -----------------------------------------------------------
VERSION = "9.9.9"
NAMES = (f"seo_monster-{VERSION}-py3-none-any.whl", f"seo_monster-{VERSION}.tar.gz", f"seo-monster-{VERSION}.mcpb")


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check, timeout=30)


class ReleaseRepo:
    def __init__(self, work, dist):
        self.work, self.dist = work, dist

    @property
    def tree(self):
        return git(self.work, "rev-parse", "HEAD^{tree}").stdout.strip()

    def digests(self):
        return [hashlib.sha256(load_guard().canonical_identity(self.dist / name)).hexdigest() for name in NAMES]

    def message(self, *, tree=None, lines=None):
        if lines is None:
            lines = ["seomonster-release-identity/1", f"tree {tree or self.tree}"]
            lines += [f"artifact {name} {digest}" for name, digest in zip(NAMES, self.digests())]
        return "\n".join(lines) + "\n"

    def tag(self, name, message=None, target="HEAD", lightweight=False):
        if lightweight:
            git(self.work, "tag", name, target)
        else:
            path = self.work.parent / "tag-message.txt"
            path.write_text(self.message() if message is None else message)
            git(self.work, "tag", "-a", "--cleanup=verbatim", "-F", str(path), name, target)
        git(self.work, "push", "-q", "origin", f"refs/tags/{name}")

    def verify(self, name="v" + VERSION):
        guard = load_guard()
        out, err = io.StringIO(), io.StringIO()
        stdout, stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = guard.main([f"--verify-tag={name}", "--dist", str(self.dist)])
        finally:
            sys.stdout, sys.stderr = stdout, stderr
        assert MARKER not in out.getvalue() + err.getvalue()
        return code, out.getvalue(), err.getvalue()


def isolated_git_env(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Synthetic Tester")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "synthetic@example.invalid")


@pytest.fixture
def release_repo(tmp_path, monkeypatch):
    isolated_git_env(monkeypatch, tmp_path)
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(tmp_path, "init", "-q", "-b", "main", str(work))
    (work / "README.md").write_text("synthetic release fixture\n")
    git(work, "add", "README.md")
    git(work, "commit", "-q", "-m", "synthetic")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    dist = work / "dist"
    dist.mkdir()
    (dist / ".gitignore").write_text("*")
    (dist / NAMES[0]).write_bytes(zip_bytes([{"name": "seo_mcp/__init__.py", "data": b"x\n"}]))
    (dist / NAMES[1]).write_bytes(tar_bytes([{"name": f"seo_monster-{VERSION}/README.md", "data": b"x\n"}]))
    (dist / NAMES[2]).write_bytes(zip_bytes([{"name": "manifest.json", "data": b"{}\n"}]))
    monkeypatch.chdir(work)
    return ReleaseRepo(work, dist)


def test_valid_annotated_tag_binds_tree_and_artifacts(release_repo):
    release_repo.tag("v" + VERSION)
    code, out, err = release_repo.verify()
    assert (code, err) == (0, "")
    assert out == "Release identity verified: tagged tree and 3 artifacts match the annotation.\n"


def refused(release_repo, reason, name="v" + VERSION):
    code, out, err = release_repo.verify(name)
    assert code == 1 and out == ""
    assert err == f"Release identity refused: {reason}.\n"


@pytest.mark.parametrize("name", ["v9.9", "v9.9.9-rc1", "9.9.9", "v09.9.9", "v9.9.9 ", "-v9.9.9", "v9.9.9/x", "v\u0669.9.9"])
def test_tag_names_are_validated_before_git(release_repo, name):
    refused(release_repo, "tag-name", name)


def test_lightweight_tag_is_refused(release_repo):
    release_repo.tag("v" + VERSION, lightweight=True)
    refused(release_repo, "lightweight")


def test_missing_remote_tag_is_refused(release_repo):
    refused(release_repo, "fetch")


def test_tag_on_another_commit_is_refused(release_repo):
    message = release_repo.message()
    git(release_repo.work, "commit", "-q", "--allow-empty", "-m", "synthetic later")
    release_repo.tag("v" + VERSION, message=message, target="HEAD~1")
    refused(release_repo, "target")


def test_tag_of_a_tree_or_nested_tag_is_refused(release_repo):
    release_repo.tag("v" + VERSION, target="HEAD^{tree}")
    refused(release_repo, "tag-object")


def test_nested_tag_is_refused(release_repo):
    release_repo.tag("v0.0.1")
    release_repo.tag("v" + VERSION, target="v0.0.1")
    refused(release_repo, "tag-object")


def test_tree_mismatch_is_refused(release_repo):
    release_repo.tag("v" + VERSION, message=release_repo.message(tree="0" * 40))
    refused(release_repo, "tree")


def annotation_cases(repo):
    good = repo.message().splitlines()
    yield "missing header", good[1:]
    yield "wrong header", ["seomonster-release-identity/2"] + good[1:]
    yield "extra line", good + [MARKER]
    yield "missing artifact", good[:-1]
    yield "uppercase digest", good[:2] + [good[2].upper().replace("ARTIFACT", "artifact")] + good[3:]
    yield "trailing space", good[:-1] + [good[-1] + " "]
    yield "comment line", good + ["# " + MARKER]
    yield "carriage return", [line + "\r" for line in good]
    yield "short tree", [good[0], "tree abc"] + good[2:]
    yield "signature block", good + ["-----BEGIN PGP SIGNATURE-----", MARKER, "-----END PGP SIGNATURE-----"]


@pytest.mark.parametrize("label", ["missing header", "wrong header", "extra line", "missing artifact", "uppercase digest",
                                   "trailing space", "comment line", "carriage return", "short tree", "signature block"])
def test_malformed_annotations_are_refused(release_repo, label):
    lines = dict(annotation_cases(release_repo))[label]
    release_repo.tag("v" + VERSION, message="\n".join(lines) + "\n")
    refused(release_repo, "annotation")


@pytest.mark.parametrize("label", ["wrong version", "duplicated", "reordered"])
def test_wrong_duplicated_or_reordered_artifact_names_are_refused(release_repo, label):
    good = release_repo.message().splitlines()
    lines = {
        "wrong version": good[:2] + [good[2].replace(VERSION, "9.9.8")] + good[3:],
        "duplicated": good[:3] + [good[2]] + good[4:],
        "reordered": good[:2] + [good[3], good[2], good[4]],
    }[label]
    release_repo.tag("v" + VERSION, message="\n".join(lines) + "\n")
    refused(release_repo, "artifact-set")


def test_artifact_digest_drift_is_refused(release_repo):
    release_repo.tag("v" + VERSION)
    (release_repo.dist / NAMES[2]).write_bytes(zip_bytes([{"name": "manifest.json", "data": b"{\"x\":1}\n"}]))
    refused(release_repo, "digest (1 of 3 artifacts)")


def test_dist_contents_must_be_exactly_the_three_artifacts(release_repo):
    release_repo.tag("v" + VERSION)
    extra = release_repo.dist / f"seo_monster-{VERSION}-{MARKER}.whl"
    extra.write_bytes(b"x")
    refused(release_repo, "dist-contents")
    extra.unlink()
    (release_repo.dist / NAMES[1]).unlink()
    refused(release_repo, "dist-contents")


def test_symlinked_artifact_is_refused(release_repo, tmp_path):
    release_repo.tag("v" + VERSION)
    target = tmp_path / "elsewhere.whl"
    shutil.move(release_repo.dist / NAMES[0], target)
    (release_repo.dist / NAMES[0]).symlink_to(target)
    refused(release_repo, "dist-contents")


def test_artifact_identity_refusal_is_a_release_refusal(release_repo):
    (release_repo.dist / NAMES[1]).write_bytes(tar_bytes([{"name": f"seo_monster-{VERSION}/README.md", "data": b"x\n"}]))
    release_repo.tag("v" + VERSION)
    (release_repo.dist / NAMES[1]).write_bytes(tar_bytes([{"name": f"seo_monster-{VERSION}/README.md", "data": b"x\n", "uname": MARKER}]))
    refused(release_repo, "artifact metadata")


# --- Release workflow ----------------------------------------------------------------
RELEASE = ROOT / ".github/workflows/release.yml"


def workflow_steps(path=RELEASE):
    steps, current, block = [], None, None
    lines = path.read_text().splitlines()
    for line in lines[lines.index("    steps:") + 1:]:
        if line.startswith("      - "):
            current, block = {}, None
            steps.append(current)
            line = "        " + line[8:]
        if current is None:
            continue
        if block and (line.startswith(" " * 10) or not line.strip()):
            current[block].append(line[10:])
            continue
        block = None
        found = re.match(r"        ([a-z-]+):\s*(.*)$", line)
        if found:
            key, value = found.groups()
            if value in ("|", ""):
                current[key], block = [], key
            else:
                current[key] = value
    return [{k: ("\n".join(v).strip("\n") + "\n" if isinstance(v, list) else v) for k, v in step.items()} for step in steps]


def step_index(steps, prefix):
    matches = [i for i, step in enumerate(steps) if step.get("name", "").startswith(prefix)]
    assert len(matches) == 1, prefix
    return matches[0]


GATE = "Verify release identity"


def test_identity_gate_runs_after_builds_and_before_extraction_and_every_upload():
    steps = workflow_steps()
    gate = step_index(steps, GATE)
    assert step_index(steps, "Build wheel + sdist") < gate
    assert step_index(steps, "Pack the .mcpb bundle") < gate
    for later in ("Security scan", "Upload artifacts to the GitHub release", "Keep only Python distributions", "Publish to PyPI"):
        assert gate < step_index(steps, later)
    run = steps[gate]["run"]
    assert '--verify-tag="$TAG_NAME" --dist dist' in run
    assert "TAG_NAME: ${{ github.ref_name }}" in steps[gate]["env"]


def test_uploads_cannot_bypass_a_failed_gate():
    text = RELEASE.read_text()
    assert "continue-on-error" not in text
    steps = workflow_steps()
    assert "if" not in steps[step_index(steps, "Upload artifacts to the GitHub release")]
    for name in ("Keep only Python distributions", "Publish to PyPI"):
        assert steps[step_index(steps, name)]["if"] == "${{ vars.PYPI_PUBLISH_ENABLED == 'true' }}"
    for step in steps:
        assert not re.search(r"always\(|failure\(|cancelled\(", step.get("if", ""))
        assert "${{" not in step.get("run", "")


def test_release_build_inputs_are_pinned_and_locked():
    steps = workflow_steps()
    pack = steps[step_index(steps, "Pack the .mcpb bundle")]["run"]
    assert "npx -y @anthropic-ai/mcpb@2.1.2 pack" in pack
    text = RELEASE.read_text()
    assert "@latest" not in text and "uv pip install" not in text
    assert "uv sync --locked --extra dev" in steps[step_index(steps, "Install Python 3.11")]["run"]
    assert "uv run --no-sync pytest" in steps[step_index(steps, "Run the offline test suite")]["run"]
    build_system = tomllib.loads((ROOT / "pyproject.toml").read_text())["build-system"]
    assert build_system["requires"] == ["hatchling==1.33.0"]


def test_ci_prints_canonical_digests_of_dirty_tree_artifacts():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "scripts/check_distribution.py --canonical" in workflow


def run_step(name, cwd, env, failing_tools=()):
    step = workflow_steps()[step_index(workflow_steps(), name)]
    tools = cwd / "tools-bin"
    tools.mkdir(exist_ok=True)
    if not (tools / "python3").exists():
        (tools / "python3").symlink_to(sys.executable)
    for tool in failing_tools:  # a detector that errors (status 2) instead of reporting no match
        (tools / tool).write_text("#!/bin/sh\nexit 2\n")
        (tools / tool).chmod(0o755)
    full_env = {**os.environ, **env, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(["bash", "-c", step["run"]], check=False, cwd=cwd, env=full_env, capture_output=True, text=True, timeout=120)


def scan_workspace(tmp_path, wheel_entries, sdist_entries):
    work = tmp_path / "scan"
    (work / "dist").mkdir(parents=True)
    (work / "scripts").mkdir()
    shutil.copy(ROOT / "scripts/check_distribution.py", work / "scripts/check_distribution.py")
    (work / "dist" / NAMES[0]).write_bytes(zip_bytes(wheel_entries))
    (work / "dist" / NAMES[1]).write_bytes(tar_bytes(sdist_entries))
    (work / "dist" / NAMES[2]).write_bytes(zip_bytes([{"name": "manifest.json", "data": b"{}\n"}]))
    return work


def synthetic_token():
    return "gh" + "p_" + MARKER + "0" * 12


@pytest.mark.parametrize("case", ["credential", "private path", "clean"])
def test_release_security_scan_never_prints_rejected_content(tmp_path, case):
    wheel = [{"name": "seo_mcp/__init__.py", "data": b"x = 1\n"}]
    sdist = [{"name": f"seo_monster-{VERSION}/README.md", "data": b"synthetic\n"}]
    if case == "credential":
        wheel.append({"name": "seo_mcp/settings.py", "data": f"token = '{synthetic_token()}'\n".encode()})
    if case == "private path":
        sdist.append({"name": f"seo_monster-{VERSION}/docs/{MARKER}/EMAIL_DRAFT.md", "data": b"synthetic\n"})
    result = run_step("Security scan", scan_workspace(tmp_path, wheel, sdist), {"TAG_VERSION": VERSION})
    assert result.returncode == (0 if case == "clean" else 1), result.stdout + result.stderr
    assert MARKER not in result.stdout + result.stderr


def test_release_gate_step_refuses_without_echoing_the_annotation(release_repo):
    good = release_repo.message().splitlines()
    release_repo.tag("v" + VERSION, message="\n".join(good + [MARKER]) + "\n")
    shutil.copytree(ROOT / "scripts", release_repo.work / "scripts")
    result = run_step(GATE, release_repo.work, {"TAG_NAME": "v" + VERSION})
    assert result.returncode == 1
    assert "Release identity refused: annotation." in result.stderr
    assert MARKER not in result.stdout + result.stderr


def test_release_gate_step_accepts_a_valid_tag(release_repo):
    release_repo.tag("v" + VERSION)
    shutil.copytree(ROOT / "scripts", release_repo.work / "scripts")
    result = run_step(GATE, release_repo.work, {"TAG_NAME": "v" + VERSION})
    assert result.returncode == 0, result.stderr


def test_planning_document_step_reports_counts_not_names(tmp_path, monkeypatch):
    isolated_git_env(monkeypatch, tmp_path)
    work = tmp_path / "tree"
    git(tmp_path, "init", "-q", str(work))
    shutil.copytree(ROOT / "scripts", work / "scripts")
    (work / "marketing").mkdir()
    (work / "marketing" / f"{MARKER}.md").write_text("synthetic\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "synthetic")
    result = run_step("Reject internal planning docs", work, {})
    assert result.returncode == 1
    assert MARKER not in result.stdout + result.stderr


# --- Public release facts --------------------------------------------------------------
def project_version():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


CONTENT_LINK = re.compile(r"https://(?:github\.com/avansaber/seo-monster/(?:blob|tree|raw)/([^/]+)/|"
                          r"raw\.githubusercontent\.com/avansaber/seo-monster/([^/]+)/)(\S*)")


def readme_targets():
    text = (ROOT / "README.md").read_text()
    targets = re.findall(r"\]\(\s*<?([^)\s>]+)", text)
    targets += re.findall(r"\b(?:src|srcset|href)=\"([^\"]+)\"", text)
    targets += re.findall(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?\s*$", text, flags=re.MULTILINE)
    return targets


def test_readme_file_and_image_links_resolve_on_pypi_at_the_release_tag():
    targets = readme_targets()
    relative = [t for t in targets if not re.match(r"(?:https?:|mailto:|#)", t)]
    assert relative == []
    pinned = [CONTENT_LINK.match(t) for t in targets if CONTENT_LINK.match(t)]
    assert len(pinned) >= 8
    for found in pinned:
        assert (found.group(1) or found.group(2)) == "v" + project_version()
        assert (ROOT / found.group(3).split("#")[0]).exists()
    assert any("/actions/workflows/ci.yml/badge.svg" in t for t in targets)
    assert "https://github.com/avansaber/seo-monster/releases/latest" in targets


def test_release_facts_name_the_current_version():
    version = project_version()
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert [p["version"] for p in lock["package"] if p["name"] == "seo-monster"] == [version]
    changelog = (ROOT / "CHANGELOG.md").read_text()
    first = re.search(r"^## \[([^\]]+)\]", changelog, flags=re.MULTILINE)
    assert first.group(1) == version
    section = changelog[first.start():changelog.index("\n## [", first.end())]
    assert "unreleased" not in section.lower()
    assert "below version 2" not in section
    assert "### Upgrade" in section
    privacy = (ROOT / "PRIVACY.md").read_text().splitlines()[2]
    assert f"SEOMonster {version} engine" in privacy and "unreleased" not in privacy.lower()
    assert "unreleased" not in (ROOT / "PART1_VALIDATION.md").read_text().lower()


def local_header_cases():
    marker = b"SYNTHETICPII"  # 12 bytes, the size of the CRC and both size fields
    yield "crc and sizes replaced by text", 14, marker
    yield "version needed", 4, struct.pack("<H", 45)
    yield "flags", 6, struct.pack("<H", 0x800)
    yield "method", 8, struct.pack("<H", 0)
    yield "time", 10, struct.pack("<H", 0x1234)
    yield "date", 12, struct.pack("<H", 0x5A21)
    yield "crc", 14, b"\0\0\0\0"
    yield "name bytes", 30, b"q"


@pytest.mark.parametrize(("label", "offset", "value"), list(local_header_cases()))
def test_local_header_must_agree_with_the_central_directory(tmp_path, label, offset, value):
    data = zip_bytes([{"name": "pkg/a.py", "data": b"print('synthetic')\n"}])
    assert data[offset:offset + len(value)] != value
    assert_refused(write(tmp_path, "engine.whl", bytes_at(data, offset, value)), "metadata")


@pytest.mark.parametrize("tool", ["grep", "find"])
def test_release_security_scan_fails_closed_when_a_detector_errors(tmp_path, tool):
    wheel = [{"name": "seo_mcp/__init__.py", "data": b"x = 1\n"}]
    sdist = [{"name": f"seo_monster-{VERSION}/README.md", "data": b"synthetic\n"}]
    result = run_step("Security scan", scan_workspace(tmp_path, wheel, sdist), {"TAG_VERSION": VERSION}, failing_tools=(tool,))
    assert result.returncode == 1
    assert "CLEAN" not in result.stdout
    assert "could not complete; refusing to release" in result.stdout + result.stderr


def test_planning_document_step_fails_closed_when_grep_errors(tmp_path, monkeypatch):
    isolated_git_env(monkeypatch, tmp_path)
    work = tmp_path / "tree"
    git(tmp_path, "init", "-q", str(work))
    shutil.copytree(ROOT / "scripts", work / "scripts")
    (work / "README.md").write_text("synthetic\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "synthetic")
    assert run_step("Reject internal planning docs", work, {}).returncode == 0
    result = run_step("Reject internal planning docs", work, {}, failing_tools=("grep",))
    assert result.returncode == 1
    assert "free of internal planning docs" not in result.stdout


def test_tar_extended_header_without_records_is_refused(tmp_path):
    # An empty pax header adds no records but its own header fields can still carry text.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as stream:
        extended = tarfile.TarInfo(MARKER)
        extended.type = tarfile.XHDTYPE
        stream.addfile(extended)
        member = tarfile.TarInfo("r/pkg/a.py")
        member.size = 2
        stream.addfile(member, io.BytesIO(b"x\n"))
    assert_refused(write(tmp_path, "engine.tar.gz", gzip_wrap(buffer.getvalue())), "metadata")


def test_zip_bytes_between_last_member_and_central_directory_are_refused(tmp_path):
    data = zip_bytes([{"name": "pkg/a.py", "data": b"x\n"}])
    eocd = len(data) - 22
    directory_offset = struct.unpack_from("<I", data, eocd + 16)[0]
    out = bytearray(data[:directory_offset] + MARKER.encode() + data[directory_offset:])
    struct.pack_into("<I", out, len(out) - 22 + 16, directory_offset + len(MARKER))
    assert_refused(write(tmp_path, "engine.whl", bytes(out)), "metadata")


def test_fake_trailing_end_record_is_refused(tmp_path):
    # zipfile keeps reading the earlier genuine end record; the fake tail keeps size/offset arithmetic consistent.
    data = zip_bytes([{"name": "pkg/a.py", "data": b"x\n"}])
    entries, directory_size, directory_offset = struct.unpack_from("<HII", data, len(data) - 22 + 10)
    fake = b"TEXT" + struct.pack("<HHHHIIH", 0, 0, entries, entries, directory_size + 22, directory_offset, 0)
    assert_refused(write(tmp_path, "engine.whl", data + fake), "metadata")


@pytest.mark.parametrize(("field", "delta"), [(12, 1), (12, -1), (16, 4), (16, -4)])
def test_inconsistent_end_record_is_a_metadata_refusal(tmp_path, field, delta):
    data = bytearray(zip_bytes([{"name": "pkg/a.py", "data": b"x\n"}, {"name": "pkg/b.py", "data": b"y\n"}]))
    position = len(data) - 22 + field
    struct.pack_into("<I", data, position, struct.unpack_from("<I", data, position)[0] + delta)
    assert_refused(write(tmp_path, "engine.whl", bytes(data)), "metadata")
