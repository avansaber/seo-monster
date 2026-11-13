"""Check exact dependency versions and declared floors against a dated advisory snapshot.

The evaluation is offline and deterministic: the same snapshot, inventory, project
metadata and --now give byte-identical output. Fetching current advisory data is a
separate, read-only step whose response is saved as a snapshot before evaluation.

An advisory may list several disjoint affected ranges, one record per release line, each with its own first-patched
version. Overlapping ranges, a first-patched version inside a sibling range, or sibling ranges with different
severities refuse, and a checker that predates this support refuses such a snapshot instead of mis-evaluating it.

Clearance fails closed. An unavailable, stale, future-dated, malformed or internally
inconsistent snapshot refuses. So does an inventory that is empty, contradictory,
malformed or not covered by the snapshot's exact-version queries. A missing
first-patched version stays null; it is never treated as a fix. Medium or low matches
need an owner decision; they are not reported as zero.

Exit status: 0 clear, 1 refused or owner decision required, 2 usage error.

Run it from the locked development environment, which declares its packaging dependency:

    uv run --no-sync python scripts/check_dependency_advisories.py inventory --snapshot S --lock uv.lock
    uv run --no-sync python scripts/check_dependency_advisories.py floors --snapshot S --pyproject pyproject.toml --lock uv.lock

A lock is always read with the pyproject.toml beside it, which names the lock's own root
project; only that project may come from a local source.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import re
import sys
import tomllib
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

SNAPSHOT_FORMAT = "seomonster-advisory-snapshot/1"
VERDICT_FORMAT = "seomonster-advisory-verdict/1"
RULE_VERSION = 1
SEVERITIES = ("critical", "high", "medium", "low")
BLOCKING = {"critical", "high"}
FUTURE_SKEW = datetime.timedelta(minutes=5)
MAX_AGE_LIMIT_HOURS = 24 * 365
LOCAL_SOURCES = {"editable", "virtual"}  # only the lock's own root project, at "."; others must come from a registry
KNOWN_SOURCES = LOCAL_SOURCES | {"registry", "git", "url", "path", "directory"}
NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", re.ASCII)
ADVISORY_ID = re.compile(r"GHSA(?:-[23456789cfghjmpqrvwx]{4}){3}", re.ASCII)
CLAUSE = re.compile(r"(<=|>=|<|>|=)\s*(\S+)", re.ASCII)
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", re.ASCII)


class Refusal(Exception):
    """A fixed reason class that refuses clearance."""


def normalise(name: object) -> str:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise Refusal("invalid-package-name")
    return re.sub(r"[-_.]+", "-", name).lower()


def version(text: object, reason: str) -> Version:
    if not isinstance(text, str):
        raise Refusal(reason)
    try:
        return Version(text)
    except InvalidVersion:
        raise Refusal(reason) from None


def parse_range(text: object) -> tuple[tuple[str, Version], ...]:
    """Parse a GitHub-style affected range such as '>= 2.11.0, <= 2.13.0' or '= 2.13.0'."""
    if not isinstance(text, str) or not text.strip():
        raise Refusal("invalid-affected-range")
    clauses = []
    for part in text.split(","):
        found = CLAUSE.fullmatch(part.strip())
        if not found:
            raise Refusal("invalid-affected-range")
        clauses.append((found.group(1), version(found.group(2), "invalid-affected-range")))
    clauses = tuple(clauses)
    if empty_range(clauses):
        raise Refusal("empty-affected-range")
    return clauses


def empty_range(clauses: tuple[tuple[str, Version], ...]) -> bool:
    """True when no version can satisfy every clause, such as '>= 3, < 2'."""
    exact = {bound for op, bound in clauses if op == "="}
    if exact:
        return len(exact) > 1 or not in_range(next(iter(exact)), clauses)
    low = high = None
    for op, bound in clauses:
        if op in {">", ">="} and (low is None or bound > low[1] or (bound == low[1] and op == ">")):
            low = (op, bound)
        if op in {"<", "<="} and (high is None or bound < high[1] or (bound == high[1] and op == "<")):
            high = (op, bound)
    if low is None or high is None:
        return False
    return low[1] > high[1] or (low[1] == high[1] and (low[0] == ">" or high[0] == "<"))


def in_range(value: Version, clauses: tuple[tuple[str, Version], ...]) -> bool:
    checks = {"<": value.__lt__, "<=": value.__le__, ">": value.__gt__, ">=": value.__ge__, "=": value.__eq__}
    return all(checks[op](bound) for op, bound in clauses)


def upper_bound(clauses: tuple[tuple[str, Version], ...]) -> tuple[str, Version] | None:
    """The tightest upper bound of a range, with '=' treated as inclusive."""
    best = None
    for op, bound in clauses:
        if op in {"<", "<=", "="}:
            inclusive = op != "<"
            if best is None or bound < best[1] or (bound == best[1] and not inclusive):
                best = ("<=" if inclusive else "<", bound)
    return best


def parse_time(text: object, reason: str) -> datetime.datetime:
    if not isinstance(text, str) or not TIMESTAMP.fullmatch(text):
        raise Refusal(reason)
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.UTC)
    except ValueError:
        raise Refusal(reason) from None


def load_snapshot(data: object, now: datetime.datetime, max_age: datetime.timedelta) -> dict:
    """Validate a snapshot and return its normalised advisories and exact-version queries."""
    if not isinstance(data, dict) or data.get("format") != SNAPSHOT_FORMAT:
        raise Refusal("invalid-snapshot-format")
    if data.get("rule_version") != RULE_VERSION:
        raise Refusal("unsupported-rule-version")
    if data.get("status") != "complete":
        raise Refusal("advisory-source-unavailable")
    retrieved = parse_time(data.get("retrieved_at"), "invalid-retrieved-at")
    if retrieved > now + FUTURE_SKEW:
        raise Refusal("future-dated-snapshot")
    if now - retrieved > max_age:
        raise Refusal("stale-snapshot")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources or not all(
            isinstance(s, dict) and all(isinstance(s.get(k), str) and s[k] for k in ("name", "url", "license"))
            for s in sources):
        raise Refusal("missing-source-attribution")

    advisories: dict[str, dict] = {}
    raw = data.get("advisories")
    if not isinstance(raw, list):
        raise Refusal("invalid-advisories")
    for item in raw:
        if not isinstance(item, dict):
            raise Refusal("invalid-advisories")
        ident = item.get("id")
        if not isinstance(ident, str) or not ADVISORY_ID.fullmatch(ident):
            raise Refusal("invalid-advisory-id")
        if item.get("severity") not in SEVERITIES:
            raise Refusal("invalid-severity")
        if "first_patched" not in item:
            raise Refusal("missing-first-patched-field")
        clauses = parse_range(item.get("affected"))
        patched = item["first_patched"]
        if patched is not None and in_range(version(patched, "invalid-first-patched"), clauses):
            raise Refusal("inconsistent-first-patched")
        record = {"id": ident, "package": normalise(item.get("package")), "severity": item["severity"],
                  "affected": item["affected"], "clauses": clauses, "first_patched": patched}
        # An advisory may list several disjoint affected ranges, one per release line, each with its own
        # first-patched version. Each range is its own record; a repeat of the same range must be identical.
        key = (ident, record["package"], clauses)
        if key in advisories and any(advisories[key][f] != record[f] for f in ("severity", "affected", "first_patched")):
            raise Refusal("conflicting-advisory-records")
        advisories[key] = record
    siblings: dict[tuple[str, str], list[dict]] = {}
    for record in advisories.values():
        siblings.setdefault((record["id"], record["package"]), []).append(record)
    for group in siblings.values():
        if len({r["severity"] for r in group}) > 1:
            raise Refusal("conflicting-advisory-records")  # one advisory, one severity, whatever its ranges
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                if not empty_range(first["clauses"] + second["clauses"]):
                    raise Refusal("overlapping-advisory-ranges")  # ambiguous first-patched version
            patched = first["first_patched"]
            if patched is not None and any(in_range(version(patched, "invalid-first-patched"), other["clauses"]) for other in group):
                raise Refusal("inconsistent-first-patched")  # a fix inside another line's affected range

    queries: dict[tuple[str, Version], frozenset[str]] = {}
    raw = data.get("queries")
    if not isinstance(raw, list) or not raw:
        raise Refusal("invalid-queries")
    known = {ident for ident, _, _ in advisories}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("advisories"), list) or not all(
                isinstance(i, str) and ADVISORY_ID.fullmatch(i) for i in item["advisories"]):
            raise Refusal("invalid-queries")
        key = (normalise(item.get("package")), version(item.get("version"), "invalid-query-version"))
        ids = frozenset(item["advisories"])
        if len(ids) != len(item["advisories"]) or not ids <= known:
            raise Refusal("unknown-query-advisory")
        if key in queries and queries[key] != ids:
            raise Refusal("conflicting-query-results")
        expected = {a["id"] for a in advisories.values() if a["package"] == key[0] and in_range(key[1], a["clauses"])}
        if ids != expected:
            raise Refusal("query-range-disagreement")  # every mode refuses a snapshot whose two views differ
        queries[key] = ids
    return {"advisories": advisories, "queries": queries, "retrieved_at": data["retrieved_at"]}


def inventory_pairs(data: object) -> list[tuple[str, Version]]:
    """Validate an inventory: {"kind": "lock" | "environment", "packages": [{"name", "version"}]}."""
    if not isinstance(data, dict) or data.get("kind") not in {"lock", "environment"}:
        raise Refusal("invalid-inventory")
    packages = data.get("packages")
    if not isinstance(packages, list) or not packages:
        raise Refusal("empty-inventory")
    pairs = set()
    seen: dict[str, Version] = {}
    for item in packages:
        if not isinstance(item, dict) or "version" not in item or "name" not in item:
            raise Refusal("invalid-inventory-entry")
        name = normalise(item["name"])
        value = version(item["version"], "invalid-inventory-version")
        if data["kind"] == "environment" and seen.setdefault(name, value) != value:
            raise Refusal("contradictory-inventory")
        pairs.add((name, value))
    return sorted(pairs)


def lock_entries(lock: object, root: str) -> list[dict]:
    """Every lock entry, validated; a malformed or non-registry third-party entry refuses, never drops."""
    if not isinstance(lock, dict) or not isinstance(lock.get("package"), list) or not lock["package"]:
        raise Refusal("invalid-lock")
    for item in lock["package"]:
        if not isinstance(item, dict) or not isinstance(item.get("source"), dict) or len(item["source"]) != 1:
            raise Refusal("invalid-lock-entry")
        name = normalise(item.get("name"))
        kind, where = next(iter(item["source"].items()))
        if kind not in KNOWN_SOURCES:
            raise Refusal("invalid-lock-entry")
        if kind not in LOCAL_SOURCES | {"registry"} or (kind in LOCAL_SOURCES and (name != root or where != ".")):
            raise Refusal("unverifiable-lock-source")
        if kind == "registry":
            version(item.get("version"), "invalid-lock-entry")
        for deps in [item.get("dependencies", [])] + list(item.get("optional-dependencies", {}).values()):
            if not isinstance(deps, list) or not all(isinstance(d, dict) and isinstance(d.get("name"), str)
                                                     and isinstance(d.get("extra", []), list) for d in deps):
                raise Refusal("invalid-lock-entry")
    return lock["package"]


def lock_inventory(lock: object, root: str) -> dict:
    """Registry packages of a uv lock; one package can hold several marker-split versions."""
    packages = [{"name": item["name"], "version": item["version"]}
                for item in lock_entries(lock, root) if "registry" in item["source"]]
    return {"kind": "lock", "packages": packages}


def installed_inventory() -> dict:
    from importlib import metadata
    return {"kind": "environment",
            "packages": [{"name": d.metadata["Name"], "version": d.version} for d in metadata.distributions()]}


def matches_for(snapshot: dict, name: str, value: Version) -> list[dict]:
    return [a for a in snapshot["advisories"].values() if a["package"] == name and in_range(value, a["clauses"])]


def verdict(matches: list[dict], reasons: list[str]) -> str:
    if reasons or any(m["severity"] in BLOCKING for m in matches):
        return "refused"
    return "owner-decision" if matches else "clear"


def evaluate_inventory(snapshot: dict, pairs: list[tuple[str, Version]]) -> dict:
    found, reasons = [], set()
    for name, value in pairs:
        expected = snapshot["queries"].get((name, value))
        hits = matches_for(snapshot, name, value)
        if expected is None:
            reasons.add(f"uncovered-inventory-package:{name}")
        elif expected != {h["id"] for h in hits}:
            reasons.add(f"source-disagreement:{name}")
        found += [{"id": h["id"], "package": name, "version": str(value), "severity": h["severity"],
                   "affected": h["affected"], "first_patched": h["first_patched"]} for h in hits]
    return {"matches": found, "reasons": sorted(reasons)}


def lock_closure(lock: dict, roots: list[Requirement], root: str) -> set[str]:
    """Names reachable from the given requirements through the lock, markers included."""
    graph: dict[str, list[dict]] = {}
    optional: dict[str, dict[str, list[dict]]] = {}
    for item in lock_entries(lock, root):
        name = normalise(item["name"])
        graph.setdefault(name, []).extend(item.get("dependencies", []))
        for extra, deps in item.get("optional-dependencies", {}).items():
            optional.setdefault(name, {}).setdefault(extra, []).extend(deps)
    pending = [(normalise(r.name), tuple(sorted(r.extras))) for r in roots]
    seen: set[str] = set()
    expanded: set[tuple[str, str]] = set()
    while pending:
        name, extras = pending.pop()
        if name not in graph:
            raise Refusal(f"requirement-not-in-lock:{name}")
        edges = list(graph[name]) if name not in seen else []
        seen.add(name)
        for extra in extras:
            if (name, extra) not in expanded:
                expanded.add((name, extra))
                edges += optional.get(name, {}).get(extra, [])
        pending += [(normalise(d["name"]), tuple(sorted(d.get("extra", [])))) for d in edges]
    return seen


def floor_excludes(requirement: Requirement, clauses: tuple[tuple[str, Version], ...]) -> bool:
    """True when every version allowed by an unconditional requirement lies outside the range."""
    if requirement.marker is not None:
        return False
    lower = None
    for spec in requirement.specifier:
        bound = version(spec.version.removesuffix(".*"), "invalid-requirement")
        if spec.operator == "==" and not spec.version.endswith(".*"):
            return not in_range(bound, clauses)
        if spec.operator in {">=", ">", "~=", "==="}:
            inclusive = spec.operator != ">"
            if lower is None or bound > lower[1] or (bound == lower[1] and not inclusive):
                lower = (inclusive, bound)
    top = upper_bound(clauses)
    if lower is None or top is None:
        return False
    inclusive_floor, floor = lower
    upper_op, ceiling = top
    if floor > ceiling:
        return True
    return floor == ceiling and (upper_op == "<" or not inclusive_floor)


def project_requirements(pyproject: object, extra: str | None) -> list[Requirement]:
    project = pyproject.get("project") if isinstance(pyproject, dict) else None
    if not isinstance(project, dict):
        raise Refusal("invalid-project-metadata")
    if extra is None:
        raw = project.get("dependencies")
    else:
        optional = project.get("optional-dependencies")
        if not isinstance(optional, dict) or extra not in optional:
            raise Refusal("unknown-extra")
        raw = optional[extra]
    if not isinstance(raw, list) or not all(isinstance(text, str) for text in raw):
        raise Refusal("invalid-project-metadata")
    if not raw:
        raise Refusal("empty-requirements")
    try:
        return [Requirement(text) for text in raw]
    except InvalidRequirement:
        raise Refusal("invalid-project-metadata") from None


def evaluate_floors(snapshot: dict, requirements: list[Requirement], lock: dict, root: str) -> dict:
    reachable = lock_closure(lock, requirements, root)
    declared: dict[str, list[Requirement]] = {}
    for requirement in requirements:
        declared.setdefault(normalise(requirement.name), []).append(requirement)
    gaps = []
    for (ident, package, _), advisory in sorted(snapshot["advisories"].items(), key=lambda kv: (kv[0][0], kv[0][1], kv[1]["affected"])):
        if package not in reachable:
            continue
        if not any(floor_excludes(r, advisory["clauses"]) for r in declared.get(package, [])):
            gaps.append({"id": ident, "package": package, "severity": advisory["severity"],
                         "affected": advisory["affected"], "first_patched": advisory["first_patched"]})
    return {"matches": gaps, "reasons": []}


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def render(result: dict | None, reasons: list[str], now: str, inputs: dict, policy: dict) -> dict:
    """Without an evaluation, matches and counts are null (unknown), never an empty list or zero."""
    reasons = sorted(set(reasons) | set((result or {}).get("reasons", [])))
    if result is None:
        evaluation, matches, counts = "unavailable", None, None
        state = "refused"
    else:
        matches = sorted(result["matches"], key=lambda m: (m["package"], m.get("version", ""), m["id"]))
        counts = {s: sum(m["severity"] == s for m in matches) for s in SEVERITIES}
        evaluation = "partial" if reasons else "complete"
        state = verdict(matches, reasons)
    return {"format": VERDICT_FORMAT, "rule_version": RULE_VERSION, "evaluated_at": now, "verdict": state,
            "evaluation": evaluation, "reasons": reasons, "severity_counts": counts, "matches": matches,
            "inputs": inputs, "policy": policy}


def run(argv: list[str]) -> tuple[int, dict]:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["inventory", "floors"])
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--lock", type=Path, help="uv.lock: inventory source, or the graph for floors")
    parser.add_argument("--environment", type=Path, help="inventory JSON with kind and packages")
    parser.add_argument("--installed", action="store_true", help="inventory of this interpreter's environment")
    parser.add_argument("--pyproject", type=Path)
    parser.add_argument("--extra")
    parser.add_argument("--now", help="evaluation time, YYYY-MM-DDTHH:MM:SSZ; default is the current time")
    parser.add_argument("--max-age-hours", default="24")
    args = parser.parse_args(argv)
    now_text = args.now or datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    digests: dict[str, object] = {}
    source_kind = None
    if args.mode == "inventory":
        source_kind = "lock" if args.lock else "environment" if args.environment else "installed" if args.installed else None
    try:
        max_age_hours = float(args.max_age_hours)
    except ValueError:
        max_age_hours = math.nan
    policy = {"mode": args.mode, "inventory_source": source_kind, "extra": args.extra,
              "max_age_hours": max_age_hours if math.isfinite(max_age_hours) else None,
              "blocking_severities": sorted(BLOCKING)}

    def read(path: Path, label: str) -> bytes:
        try:
            raw = path.read_bytes()
        except OSError:
            raise Refusal(f"unavailable-input:{label}") from None
        digests[label] = hashlib.sha256(raw).hexdigest()
        return raw

    try:
        now = parse_time(now_text, "invalid-now")
        if not (math.isfinite(max_age_hours) and 0 < max_age_hours <= MAX_AGE_LIMIT_HOURS):
            raise Refusal("invalid-max-age")
        try:
            data = json.loads(read(args.snapshot, "snapshot"))
        except ValueError:
            raise Refusal("invalid-snapshot-json") from None
        snapshot = load_snapshot(data, now, datetime.timedelta(hours=max_age_hours))
        lock = lock_root = None
        if args.lock:
            try:
                lock = tomllib.loads(read(args.lock, "lock").decode())
            except (ValueError, UnicodeDecodeError):
                raise Refusal("invalid-lock") from None
            try:
                owner = tomllib.loads(read(args.lock.parent / "pyproject.toml", "lock-project").decode())
            except (ValueError, UnicodeDecodeError):
                raise Refusal("invalid-project-metadata") from None
            project = owner.get("project")
            lock_root = normalise(project.get("name") if isinstance(project, dict) else None)
        if args.mode == "inventory":
            if sum(map(bool, (args.lock, args.environment, args.installed))) != 1:
                parser.error("inventory needs exactly one of --lock, --environment or --installed")
            if lock is not None:
                source = lock_inventory(lock, lock_root)
            elif args.environment:
                try:
                    source = json.loads(read(args.environment, "environment"))
                except ValueError:
                    raise Refusal("invalid-inventory") from None
            else:
                source = installed_inventory()
            pairs = inventory_pairs(source)
            digests["inventory"] = canonical_digest([[name, str(value)] for name, value in pairs])
            digests["inventory_count"] = len(pairs)
            result = evaluate_inventory(snapshot, pairs)
        else:
            if not (args.pyproject and lock is not None) or args.environment or args.installed:
                parser.error("floors needs --pyproject and --lock")
            try:
                pyproject = tomllib.loads(read(args.pyproject, "pyproject").decode())
            except (ValueError, UnicodeDecodeError):
                raise Refusal("invalid-pyproject") from None
            requirements = project_requirements(pyproject, args.extra)
            digests["requirements"] = canonical_digest(sorted(str(r) for r in requirements))
            result = evaluate_floors(snapshot, requirements, lock, lock_root)
        out = render(result, [], now_text, digests, policy)
    except Refusal as refusal:
        out = render(None, [str(refusal)], now_text, digests, policy)
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        out = render(None, ["malformed-input"], now_text, digests, policy)  # fixed class; input is never echoed
    return (0 if out["verdict"] == "clear" else 1), out


def main(argv: list[str] | None = None) -> int:
    status, out = run(sys.argv[1:] if argv is None else argv)
    print(json.dumps(out, sort_keys=True, indent=1, ensure_ascii=True))
    return status


if __name__ == "__main__":
    sys.exit(main())
