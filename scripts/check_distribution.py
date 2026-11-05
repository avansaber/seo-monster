"""Refuse local coordination and private planning paths in staged or tracked source."""
from __future__ import annotations

import argparse
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Inspect index additions and modifications")
    parser.add_argument("--artifact", type=Path, action="append", default=[], help="Inspect an engine wheel, source archive or MCPB inventory")
    args = parser.parse_args()
    if args.artifact:
        if args.staged:
            parser.error("Choose source or artifact inventory checks separately")
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
