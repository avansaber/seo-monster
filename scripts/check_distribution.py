"""Refuse local coordination and private planning paths in staged or tracked source."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import PurePosixPath


def forbidden(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(set(path.parts) & {".private", "marketing"})
        or path.name in {"CLAUDE.md", "HANDOFF.md", "PLAN.md", "LISTINGS-PLAN.md", "RESEARCH-AND-PROPOSAL.md"}
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Inspect index additions and modifications")
    args = parser.parse_args()
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
