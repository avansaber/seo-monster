"""Regressions for the grpcio and pydantic advisories that the 0.11.0 lock and floors did not cover.

The 2026-11-13 snapshot extends the 2026-11-06 one with six maintainer repository advisories
that OSV and GitHub's global database did not list at retrieval time. Like the earlier fixture
it holds public advisory facts and replays offline; it is not a current clearance.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_dependency_advisories.py"
FIXTURES = ROOT / "tests" / "fixtures" / "dependency_advisories"
OLD_SNAPSHOT = FIXTURES / "snapshot-2026-11-06.json"
SNAPSHOT = FIXTURES / "snapshot-2026-11-13.json"
LOCK_0_11_0 = FIXTURES / "inventory-0.11.0-lock.json"

ADDED = {
    "GHSA-2xm2-c8xx-p9vh": ("grpcio", "critical", "1.80.1"),
    "GHSA-7jmm-cgrp-9cvh": ("grpcio", "medium", "1.80.1"),
    "GHSA-qpmf-fgh3-ff25": ("pydantic", "medium", "2.14.2"),
    "GHSA-gj4f-r7mf-jjxh": ("pydantic", "medium", "2.14.0"),
    "GHSA-3r8g-cqwr-jr29": ("pydantic", "medium", "2.14.2"),
    "GHSA-955g-x97g-8mcp": ("pydantic", "medium", "2.14.0"),
}

spec = importlib.util.spec_from_file_location("check_dependency_advisories_grpc_pydantic", SCRIPT)
advisories = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = advisories
spec.loader.exec_module(advisories)


def snapshot_data() -> dict:
    return json.loads(SNAPSHOT.read_text())


def replay_now() -> str:
    return snapshot_data()["retrieved_at"]


def cli(*args: str) -> tuple[int, dict]:
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), *args, "--now", replay_now()], capture_output=True, check=False)
    assert result.returncode in (0, 1), result.stderr.decode()
    return result.returncode, json.loads(result.stdout)


def declared(path: Path, extra: str | None = None) -> dict:
    pyproject = tomllib.loads(path.read_text())
    return {advisories.normalise(r.name): r for r in advisories.project_requirements(pyproject, extra)}


def test_extended_snapshot_keeps_every_earlier_record_and_adds_only_the_six_maintainer_advisories():
    old = {(a["id"], a["package"], a["affected"]): a for a in json.loads(OLD_SNAPSHOT.read_text())["advisories"]}
    new = {(a["id"], a["package"], a["affected"]): a for a in snapshot_data()["advisories"]}
    assert all(new[key] == record for key, record in old.items())
    assert {i for i, _, _ in new} - {i for i, _, _ in old} == set(ADDED)
    for (ident, _, _), record in new.items():
        if ident in ADDED:
            package, severity, patched = ADDED[ident]
            assert (record["package"], record["severity"]) == (package, severity)
            if package == "pydantic":  # one published range each; grpcio's lines are checked line by line below
                assert record["first_patched"] == patched
    assert any(s["name"].startswith("Maintainer repository security advisories") for s in snapshot_data()["sources"])


def test_replay_of_the_published_0_11_0_lock_finds_the_added_advisories():
    status, out = cli("inventory", "--snapshot", str(SNAPSHOT), "--environment", str(LOCK_0_11_0))
    assert status == 1 and out["verdict"] == "refused" and out["reasons"] == []
    assert {m["id"] for m in out["matches"]} == set(ADDED)
    assert {(m["package"], m["version"]) for m in out["matches"]} == {("grpcio", "1.80.0"), ("pydantic", "2.13.4")}
    assert out["severity_counts"] == {"critical": 1, "high": 0, "medium": 5, "low": 0}


def test_locked_versions_match_no_known_range_in_the_extended_snapshot():
    status, out = cli("inventory", "--snapshot", str(SNAPSHOT), "--lock", str(ROOT / "uv.lock"))
    assert (status, out["verdict"], out["reasons"], out["matches"]) == (0, "clear", [], [])


def test_declared_runtime_floors_exclude_every_range_in_the_extended_snapshot():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    reachable = advisories.lock_closure(lock, advisories.project_requirements(pyproject, None), "seo-monster")
    assert {"grpcio", "pydantic"} <= reachable  # the check below is not vacuous
    status, out = cli("floors", "--snapshot", str(SNAPSHOT), "--pyproject", str(ROOT / "pyproject.toml"), "--lock", str(ROOT / "uv.lock"))
    assert (status, out["verdict"], out["matches"]) == (0, "clear", [])


def test_workbench_mcp_extra_floors_pydantic_and_stays_clear():
    status, out = cli("floors", "--snapshot", str(SNAPSHOT), "--pyproject", str(ROOT / "companion" / "pyproject.toml"),
                      "--extra", "mcp", "--lock", str(ROOT / "uv.lock"))
    assert (status, out["verdict"], out["matches"]) == (0, "clear", [])
    assert advisories.floor_excludes(declared(ROOT / "companion" / "pyproject.toml", "mcp")["pydantic"],
                                     advisories.parse_range(">= 2.0, < 2.14.2"))
    assert (ROOT / "companion" / "pyproject.toml").read_text().count("grpcio") == 0  # Workbench never reaches grpcio


def test_existing_0_11_0_installs_cannot_satisfy_the_published_requirements():
    # pip and uv keep an installed dependency that still satisfies the requirement, so the
    # vulnerable 0.11.0 locked versions must be rejected by the declared metadata itself.
    requirements = declared(ROOT / "pyproject.toml")
    old = {advisories.normalise(p["name"]): p["version"] for p in json.loads(LOCK_0_11_0.read_text())["packages"]}
    affected = {advisories.normalise(a["package"]) for a in snapshot_data()["advisories"] if a["id"] in ADDED}
    assert affected == {"grpcio", "pydantic"}
    survivors = {name: old[name] for name in affected
                 if name not in requirements or requirements[name].specifier.contains(Version(old[name]), prereleases=True)}
    assert survivors == {}
    # and the floor admits the first release that patches every recorded line
    assert requirements["grpcio"].specifier.contains(Version("1.85.1")) and requirements["pydantic"].specifier.contains(Version("2.14.2"))


def test_mcpb_bundle_launches_from_the_lock_and_ships_it():
    manifest = json.loads((ROOT / "manifest.json").read_text())["server"]["mcp_config"]
    assert manifest["command"] == "uv" and manifest["args"][:3] == ["run", "--project", "${__dirname}"]
    ignored = [line.strip() for line in (ROOT / ".mcpbignore").read_text().splitlines() if line.strip() and not line.startswith("#")]
    assert not any(pattern in ("uv.lock", "*.lock") for pattern in ignored)
    locked = {p["name"]: p["version"] for p in tomllib.loads((ROOT / "uv.lock").read_text())["package"]}
    requirements = declared(ROOT / "pyproject.toml")
    assert all(requirements[name].specifier.contains(Version(locked[name]), prereleases=True) for name in ("grpcio", "pydantic"))


# Every published affected line of both grpcio advisories, written out independently of the fixture:
# a version is affected when lower <= version < patched (None means no lower bound). GHSA-7jmm also
# lists exactly 1.85.0, which is the half-open line [1.85.0, 1.85.1).
GRPCIO_LINES = {
    "GHSA-2xm2-c8xx-p9vh": [(None, "1.78.2"), ("1.80.0", "1.80.1"), ("1.81.0", "1.81.2"), ("1.82.0", "1.82.3"),
                            ("1.83.0", "1.83.2"), ("1.84.0", "1.84.1")],
    "GHSA-7jmm-cgrp-9cvh": [(None, "1.78.2"), ("1.80.0", "1.80.1"), ("1.81.0", "1.81.2"), ("1.82.0", "1.82.3"),
                            ("1.83.0", "1.83.2"), ("1.84.0", "1.84.2"), ("1.85.0", "1.85.1")],
}
GRPCIO_VERSIONS = ["0.1.0", "1.0.0", "1.78.1", "1.78.2", "1.79.0", "1.80.0", "1.80.1", "1.81.0", "1.81.1", "1.81.2",
                   "1.82.0", "1.82.2", "1.82.3", "1.83.0", "1.83.1", "1.83.2", "1.84.0", "1.84.1", "1.84.2",
                   "1.85.0", "1.85.1", "1.86.0"]


def grpcio_affected(version: str) -> set[str]:
    found = set()
    for ident, lines in GRPCIO_LINES.items():
        for lower, patched in lines:
            if (lower is None or Version(version) >= Version(lower)) and Version(version) < Version(patched):
                found.add(ident)
    return found


def with_grpcio_queries() -> dict:
    data = snapshot_data()
    have = {(q["package"], q["version"]) for q in data["queries"]}
    for version in GRPCIO_VERSIONS:
        if ("grpcio", version) not in have:
            data["queries"].append({"advisories": sorted(grpcio_affected(version)), "package": "grpcio", "version": version})
    return data


def replay(tmp_path: Path, data: dict, version: str) -> dict:
    snapshot = tmp_path / "snapshot.json"
    inventory = tmp_path / "inventory.json"
    snapshot.write_text(json.dumps(data))
    inventory.write_text(json.dumps({"kind": "environment", "packages": [{"name": "grpcio", "version": version}]}))
    return cli("inventory", "--snapshot", str(snapshot), "--environment", str(inventory))[1]


def test_both_grpcio_advisories_record_every_published_line():
    records = [a for a in snapshot_data()["advisories"] if a["package"] == "grpcio"]
    for ident, lines in GRPCIO_LINES.items():
        assert sum(a["id"] == ident for a in records) == len(lines)
        assert {a["first_patched"] for a in records if a["id"] == ident} == {patched for _, patched in lines}
    assert {a["severity"] for a in records if a["id"] == "GHSA-2xm2-c8xx-p9vh"} == {"critical"}
    assert {a["severity"] for a in records if a["id"] == "GHSA-7jmm-cgrp-9cvh"} == {"medium"}


@pytest.mark.parametrize("version", GRPCIO_VERSIONS)
def test_vulnerable_grpcio_versions_on_every_listed_line_are_refused_and_patched_ones_pass(tmp_path, version):
    expected = grpcio_affected(version)
    out = replay(tmp_path, with_grpcio_queries(), version)
    assert out["reasons"] == []
    assert {m["id"] for m in out["matches"]} == expected
    if not expected:
        assert out["verdict"] == "clear" and out["matches"] == []
    elif "GHSA-2xm2-c8xx-p9vh" in expected:
        assert out["verdict"] == "refused"  # critical blocks
    else:
        assert out["verdict"] == "owner-decision"  # only the medium advisory still applies, never clear


def test_grpcio_versions_between_lines_of_the_two_advisories_match_only_the_still_affected_one(tmp_path):
    only_medium = replay(tmp_path, with_grpcio_queries(), "1.84.1")
    assert {m["id"] for m in only_medium["matches"]} == {"GHSA-7jmm-cgrp-9cvh"}  # fixed for 2xm2 in 1.84.1, not for 7jmm
    exact = replay(tmp_path, with_grpcio_queries(), "1.85.0")
    assert {m["id"] for m in exact["matches"]} == {"GHSA-7jmm-cgrp-9cvh"} and exact["matches"][0]["first_patched"] == "1.85.1"


@pytest.mark.parametrize("dropped", [("GHSA-2xm2-c8xx-p9vh", "1.84.1"), ("GHSA-7jmm-cgrp-9cvh", "1.85.1"), ("GHSA-7jmm-cgrp-9cvh", "1.78.2")])
def test_a_snapshot_missing_a_published_grpcio_line_cannot_clear_that_line(tmp_path, dropped):
    data = with_grpcio_queries()
    ident, patched = dropped
    data["advisories"] = [a for a in data["advisories"] if not (a["id"] == ident and a["first_patched"] == patched)]
    out = replay(tmp_path, data, "1.84.0")
    assert (out["verdict"], out["reasons"]) == ("refused", ["query-range-disagreement"])


@pytest.mark.parametrize("floor,gap_ids", [
    ("grpcio>=1.85.1", set()),
    ("grpcio>=1.85.0", {"GHSA-7jmm-cgrp-9cvh"}),  # the exact 1.85.0 line is still allowed
    ("grpcio>=1.84.1", {"GHSA-7jmm-cgrp-9cvh"}),  # 1.84.1 is fixed for 2xm2 but not for 7jmm
    ("grpcio>=1.80.1", {"GHSA-2xm2-c8xx-p9vh", "GHSA-7jmm-cgrp-9cvh"}),  # later lines are still affected
])
def test_declared_grpcio_floor_must_exclude_every_published_line(tmp_path, floor, gap_ids):
    base = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    deps = [d for d in base if advisories.normalise(d.split(">")[0].split("=")[0]) != "grpcio"] + [floor]
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'x'\ndependencies = " + json.dumps(deps) + "\n")
    status, out = cli("floors", "--snapshot", str(SNAPSHOT), "--pyproject", str(pyproject), "--lock", str(ROOT / "uv.lock"))
    grpcio_gaps = {m["id"] for m in out["matches"] if m["package"] == "grpcio"}
    assert grpcio_gaps == gap_ids
    assert (status == 0) == (not out["matches"])


def test_snapshot_ranges_stay_pairwise_disjoint_per_advisory():
    loaded = advisories.load_snapshot(snapshot_data(), advisories.parse_time(replay_now(), "t"), advisories.datetime.timedelta(hours=24))
    by_id: dict[tuple[str, str], list] = {}
    for (ident, package, _), record in loaded["advisories"].items():
        by_id.setdefault((ident, package), []).append(record)
    assert max(len(group) for group in by_id.values()) == 7
    for group in by_id.values():
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                assert advisories.empty_range(first["clauses"] + second["clauses"])
