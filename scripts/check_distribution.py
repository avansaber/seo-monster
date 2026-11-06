"""Refuse private paths in source and artifacts, and bind release artifacts to an annotated tag.

Every refusal prints a fixed reason class. Member names, archive metadata and tag
messages are never echoed, because a rejected artifact can carry private text.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tarfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath

IDENTITY_FORMAT = "seomonster-artifact-identity/1"
RELEASE_FORMAT = "seomonster-release-identity/1"
TAG_NAME = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", re.ASCII)
MAX_BYTES = 512 * 1024 * 1024
ZIP_TIMESTAMP_EXTRA = 0x5455  # the only ZIP extra field accepted, with a validated payload
ZIP_ALLOWED_FLAGS = 0x0806  # deflate options and UTF-8 names; no encryption or data descriptors
TAR_FIELDS = ((0, 100), (100, 8), (108, 8), (116, 8), (124, 12), (136, 12), (157, 100),
              (265, 32), (297, 32), (329, 8), (337, 8), (345, 155))


def forbidden(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(set(path.parts) & {".private", "marketing"})
        or path.name in {"CLAUDE.md", "HANDOFF.md", "PLAN.md", "LISTINGS-PLAN.md", "RESEARCH-AND-PROPOSAL.md"}
    )



def forbidden_artifact_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        "\\" in name or ":" in name
        or path.is_absolute() or ".." in path.parts or forbidden(name)
        or bool(set(path.parts) & {"companion", "archive", ".git"})
        or path.name == "IMPLEMENTATION_REPORT.md"
    )


def zip_member_is_plain(info: zipfile.ZipInfo) -> bool:
    kind = stat.S_IFMT(info.external_attr >> 16)
    return kind in {0, stat.S_IFREG, stat.S_IFDIR}


def artifact_members(path: Path) -> list[tuple[str, bool]]:
    """Return each member name and whether it is a plain file or directory."""
    if path.name.endswith((".whl", ".mcpb")):
        with zipfile.ZipFile(path) as stream:
            return [(info.filename, zip_member_is_plain(info)) for info in stream.infolist()]
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path) as stream:
            return [(member.name, member.isfile() or member.isdir()) for member in stream.getmembers()]
    raise ValueError("unsupported artifact")


class Refusal(Exception):
    """A refusal whose text is a fixed reason class, never archive or tag content."""


def require(condition: object, reason: str) -> None:
    if not condition:
        raise Refusal(reason)


def portable_path(name: str, *, is_dir: bool) -> str:
    require(name and all(" " <= character <= "~" for character in name), "name")
    require("\\" not in name and ":" not in name and not name.startswith("/"), "name")
    if is_dir and name.endswith("/"):
        name = name[:-1]
    require(all(part not in {"", ".", ".."} for part in name.split("/")), "name")
    require(not forbidden_artifact_member(name), "private")
    return name


def permission(bits: int) -> str:
    require(0 <= bits <= 0o7777, "metadata")
    require(not bits & 0o7000, "permission")
    return format(bits, "04o")


def record(path: str, is_dir: bool, mode: str, content: bytes) -> dict:
    return {"path": path, "type": "dir" if is_dir else "file", "mode": mode,
            "size": 0 if is_dir else len(content), "sha256": "" if is_dir else hashlib.sha256(content).hexdigest()}


def identity_document(name: str, records: list[dict]) -> bytes:
    kinds: dict[str, str] = {}
    for item in records:
        key = item["path"].casefold()
        if key in kinds:
            raise Refusal("duplicate" if kinds[key] == item["type"] else "collision")
        kinds[key] = item["type"]
    for key in kinds:
        parts = key.split("/")
        require(all(kinds.get("/".join(parts[:end])) != "file" for end in range(1, len(parts))), "collision")
    document = {"format": IDENTITY_FORMAT, "artifact": name, "members": sorted(records, key=lambda item: item["path"])}
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def gunzip(data: bytes) -> bytes:
    # Header flags must be zero: no stored file name, comment, extra field or header CRC.
    require(len(data) >= 18 and data[:4] == b"\x1f\x8b\x08\x00" and data[8] in (0, 2, 4), "metadata")
    stream = zlib.decompressobj(-zlib.MAX_WBITS)
    raw = stream.decompress(data[10:], MAX_BYTES)
    require(not stream.unconsumed_tail, "size")
    require(stream.eof, "unreadable")
    require(len(stream.unused_data) == 8, "metadata")
    require(struct.unpack("<II", stream.unused_data) == (zlib.crc32(raw), len(raw) & 0xFFFFFFFF), "unreadable")
    return raw


def tar_header_is_clean(block: bytes) -> bool:
    for start, length in TAR_FIELDS:
        field = block[start:start + length]
        end = field.find(0)
        if end >= 0 and any(field[end:]):
            return False
    return block[257:265] == b"ustar\x0000" and not any(block[500:512])


def tar_records(data: bytes) -> list[dict]:
    raw = gunzip(data)
    records, position = [], 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            # Extended headers (pax, GNU long names or links) would shift the data offset.
            require(member.offset == position and member.offset_data == position + 512, "metadata")
            require(not member.pax_headers and not archive.pax_headers, "metadata")
            require(tar_header_is_clean(raw[position:position + 512]), "metadata")
            require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE), "type")
            require(not (member.linkname or member.uname or member.gname), "metadata")
            require(member.uid == member.gid == member.devmajor == member.devminor == 0, "metadata")
            require(isinstance(member.mtime, int) and 0 <= member.mtime < 8 ** 11, "metadata")
            is_dir = member.type == tarfile.DIRTYPE
            require(not (is_dir and member.size), "metadata")
            end = member.offset_data + -(-member.size // 512) * 512
            require(end <= len(raw), "unreadable")
            content = raw[member.offset_data:member.offset_data + member.size]
            require(not any(raw[member.offset_data + member.size:end]), "metadata")
            records.append(record(portable_path(member.name, is_dir=is_dir), is_dir, permission(member.mode), content))
            position = end
    require(not any(raw[position:]), "metadata")
    return records


def zip_extra_is_valid(extra: bytes, *, central: bool) -> bool:
    position, seen = 0, False
    while position < len(extra):
        if position + 4 > len(extra) or seen:
            return False
        identifier, length = struct.unpack_from("<HH", extra, position)
        payload = extra[position + 4:position + 4 + length]
        if identifier != ZIP_TIMESTAMP_EXTRA or len(payload) != length or not payload or payload[0] & ~7:
            return False
        times = (payload[0] & 1) if central else payload[0].bit_count()
        if length != 1 + 4 * times:
            return False
        position, seen = position + 4 + length, True
    return True


def valid_time(date_time: tuple) -> bool:
    try:
        datetime.datetime(*date_time)
    except ValueError:
        return False
    return True


def zip_records(data: bytes) -> list[dict]:
    # The end record must close the file with no comment, and members must tile the
    # file from offset zero to the central directory with no hidden bytes between them.
    require(len(data) >= 22 and data[-22:-18] == b"PK\x05\x06", "metadata")
    disk, directory_disk, disk_entries, entries, directory_size, directory_offset, comment = struct.unpack("<HHHHIIH", data[-18:])
    require(disk == directory_disk == 0 and disk_entries == entries and not comment, "metadata")
    require(directory_offset + directory_size == len(data) - 22, "metadata")
    records, total = [], 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        require(len(infos) == entries and not archive.comment, "metadata")
        position = 0
        for info in sorted(infos, key=lambda item: item.header_offset):
            header = data[position:position + 30]
            require(info.header_offset == position and len(header) == 30 and header[:4] == b"PK\x03\x04", "metadata")
            # Every fixed local field must equal the central record that zipfile reads;
            # otherwise the local copy could carry unhashed text (for example in CRC or sizes).
            version, flags, method, local_time, local_date, crc, compressed, size, name_length, extra_length = (
                struct.unpack_from("<HHHHHIIIHH", header, 4))
            year, month, day, hour, minute, second = info.date_time
            central_date = (year - 1980) << 9 | month << 5 | day
            central_time = hour << 11 | minute << 5 | second // 2
            name = data[position + 30:position + 30 + name_length]
            encoding = "utf-8" if info.flag_bits & 0x800 else "cp437"
            require((version, flags, method, crc, compressed, size) == (
                info.extract_version, info.flag_bits, info.compress_type, info.CRC, info.compress_size, info.file_size), "metadata")
            require((local_date, local_time) == (central_date, central_time), "metadata")
            require(name == info.orig_filename.encode(encoding), "metadata")
            local_extra = data[position + 30 + name_length:position + 30 + name_length + extra_length]
            require(zip_extra_is_valid(local_extra, central=False), "metadata")
            position += 30 + name_length + extra_length + info.compress_size
        require(position == directory_offset, "metadata")
        for info in infos:
            require(not info.flag_bits & ~ZIP_ALLOWED_FLAGS, "metadata")
            require(info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED), "metadata")
            require(not info.comment and zip_extra_is_valid(info.extra, central=True), "metadata")
            require(valid_time(info.date_time) and info.internal_attr in (0, 1) and info.volume == 0, "metadata")
            require(not info.external_attr & 0xFFEF, "metadata")
            require(info.orig_filename == info.filename, "name")
            is_dir = info.filename.endswith("/")
            unix = info.external_attr >> 16
            if info.create_system == 3:
                require(stat.S_IFMT(unix) in ((0, stat.S_IFDIR) if is_dir else (0, stat.S_IFREG)), "type")
                mode = permission(stat.S_IMODE(unix))
            else:
                require(not unix, "metadata")
                mode = "none"
            total += info.file_size
            require(total <= MAX_BYTES, "size")
            with archive.open(info) as stream:
                content = stream.read(MAX_BYTES + 1)
            require(len(content) == info.file_size, "unreadable")
            require(not (is_dir and content), "metadata")
            records.append(record(portable_path(info.filename, is_dir=is_dir), is_dir, mode, content))
    return records


def canonical_identity(path: Path) -> bytes:
    """Return the canonical identity document of one wheel, sdist or MCPB."""
    try:
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_BYTES, "unreadable")
        data = path.read_bytes()
        if path.name.endswith((".whl", ".mcpb")):
            records = zip_records(data)
        elif path.name.endswith(".tar.gz"):
            records = tar_records(data)
        else:
            raise Refusal("unreadable")
        return identity_document(path.name, records)
    except Refusal:
        raise
    except Exception:  # noqa: BLE001 - fail closed with a fixed reason, never the parser's message
        raise Refusal("unreadable") from None


def release_artifact_names(version: str) -> tuple[str, str, str]:
    return (f"seo_monster-{version}-py3-none-any.whl", f"seo_monster-{version}.tar.gz", f"seo-monster-{version}.mcpb")


def git(reason: str, *arguments: str) -> bytes:
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    result = subprocess.run(["git", *arguments], capture_output=True, check=False, timeout=300, env=environment)
    require(result.returncode == 0, reason)
    return result.stdout


def parse_annotation(message: bytes, version: str) -> tuple[str, list[tuple[str, str]]]:
    try:
        text = message.decode("ascii")
    except UnicodeDecodeError:
        raise Refusal("annotation") from None
    require(text.endswith("\n") and "\r" not in text, "annotation")
    lines = text[:-1].split("\n")
    require(len(lines) == 5 and lines[0] == RELEASE_FORMAT, "annotation")
    tree = re.fullmatch(r"tree ([0-9a-f]{40}|[0-9a-f]{64})", lines[1])
    require(tree, "annotation")
    expected = []
    for line, name in zip(lines[2:], release_artifact_names(version)):
        found = re.fullmatch(r"artifact ([!-~]+) ([0-9a-f]{64})", line)
        require(found, "annotation")
        require(found.group(1) == name, "artifact-set")
        expected.append((name, found.group(2)))
    return tree.group(1), expected


def verify_release_tag(tag: str, dist: Path) -> None:
    """Refuse unless the annotated tag binds HEAD's tree and the canonical digests in dist."""
    require(TAG_NAME.fullmatch(tag), "tag-name")
    ref = f"refs/tags/{tag}"
    git("fetch", "fetch", "--quiet", "--no-tags", "origin", f"+{ref}:{ref}")
    require(git("lightweight", "cat-file", "-t", ref) == b"tag\n", "lightweight")
    headers, separator, message = git("tag-object", "cat-file", "tag", ref).partition(b"\n\n")
    lines = headers.split(b"\n")
    require(separator and len(lines) in (3, 4) and re.fullmatch(rb"object [0-9a-f]{40,64}", lines[0]), "tag-object")
    require(lines[1] == b"type commit" and lines[2] == b"tag " + tag.encode(), "tag-object")
    require(all(line.startswith(b"tagger ") for line in lines[3:]), "tag-object")
    commit = git("target", "rev-parse", "--verify", "--quiet", ref + "^{commit}").strip()
    head = git("target", "rev-parse", "--verify", "--quiet", "HEAD").strip()
    require(lines[0][7:] == commit == head, "target")
    tree = git("tree", "rev-parse", "--verify", "--quiet", ref + "^{tree}").strip().decode("ascii")
    accepted_tree, expected = parse_annotation(message, tag[1:])
    require(accepted_tree == tree, "tree")
    names = {name for name, _ in expected}
    try:
        present = set(os.listdir(dist))
    except OSError:
        raise Refusal("dist-contents") from None
    require(names <= present <= names | {".gitignore"}, "dist-contents")
    require(all((dist / name).is_file() and not (dist / name).is_symlink() for name in present), "dist-contents")
    mismatched = 0
    for name, digest in expected:
        try:
            document = canonical_identity(dist / name)
        except Refusal as refusal:
            raise Refusal(f"artifact {refusal}") from None
        mismatched += hashlib.sha256(document).hexdigest() != digest
    require(not mismatched, f"digest ({mismatched} of {len(expected)} artifacts)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Inspect index additions and modifications")
    parser.add_argument("--artifact", type=Path, action="append", default=[], help="Inspect an engine wheel, source archive or MCPB inventory")
    parser.add_argument("--canonical", type=Path, nargs="+", action="extend", default=[], help="Print canonical identity digests")
    parser.add_argument("--identity-out", type=Path, help="Also write each canonical identity document to this folder")
    parser.add_argument("--verify-tag", help="Verify dist against the accepted identity in this annotated release tag")
    parser.add_argument("--dist", type=Path, default=Path("dist"), help="Release artifact folder for --verify-tag")
    args = parser.parse_args(argv)
    if sum(map(bool, (args.staged, args.artifact, args.canonical, args.verify_tag))) > 1:
        parser.error("Choose one check at a time")
    if args.verify_tag is not None:
        try:
            verify_release_tag(args.verify_tag, args.dist)
        except Refusal as refusal:
            print(f"Release identity refused: {refusal}.", file=sys.stderr)
            return 1
        except Exception:  # noqa: BLE001 - fail closed without echoing tag or archive content
            print("Release identity refused: internal.", file=sys.stderr)
            return 1
        print("Release identity verified: tagged tree and 3 artifacts match the annotation.")
        return 0
    if args.canonical:
        for path in args.canonical:
            try:
                document = canonical_identity(path)
            except Refusal as refusal:
                print(f"Artifact identity refused: {refusal}.", file=sys.stderr)
                return 1
            if args.identity_out:
                (args.identity_out / f"{path.name}.identity.json").write_bytes(document)
            print(f"{hashlib.sha256(document).hexdigest()}  {path.name}")
        return 0
    if args.artifact:
        try:
            members = [member for path in args.artifact for member in artifact_members(path)]
        except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile):
            print("Cannot inspect artifact inventory.", file=sys.stderr)
            return 2
        if any(not plain or forbidden_artifact_member(name) for name, plain in members):
            print("Linked, non-portable, private or companion artifact members are not distributable.", file=sys.stderr)
            return 1
        return 0
    command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"] if args.staged else ["git", "ls-files", "-z"]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode:
        print("Cannot inspect source inventory.", file=sys.stderr)
        return 2
    if any(forbidden(name) for name in result.stdout.decode("utf-8", "replace").split("\0") if name):
        print("Private coordination or planning files are not distributable.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
