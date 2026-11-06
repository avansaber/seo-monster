"""Dependency advisory regressions: locked versions, declared runtime floors and fail-closed replay.

The fixture holds public GitHub Advisory Database facts (CC BY 4.0) and exact-version
OSV query identifiers retrieved at a recorded time. It replays offline; it is not a
current clearance.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_dependency_advisories.py"
FIXTURES = ROOT / "tests" / "fixtures" / "dependency_advisories"
SNAPSHOT = FIXTURES / "snapshot-2026-11-06.json"
OLD_LOCK = FIXTURES / "inventory-0.10.0-lock.json"
REPLAY_NOW = "2026-11-06T19:00:00Z"

spec = importlib.util.spec_from_file_location("check_dependency_advisories", SCRIPT)
advisories = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = advisories
spec.loader.exec_module(advisories)


def snapshot_data() -> dict:
    return json.loads(SNAPSHOT.read_text())


def cli(*args: str) -> tuple[int, dict, bytes]:
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, check=False)
    assert result.returncode in (0, 1), result.stderr.decode()
    return result.returncode, json.loads(result.stdout), result.stdout


def write(tmp_path: Path, name: str, data: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def replay(tmp_path: Path, data: dict, inventory: dict, now: str = REPLAY_NOW) -> dict:
    return cli("inventory", "--snapshot", str(write(tmp_path, "snapshot.json", data)),
               "--environment", str(write(tmp_path, "inventory.json", inventory)), "--now", now)[1]


def env(*pairs: tuple[str, str]) -> dict:
    return {"kind": "environment", "packages": [{"name": n, "version": v} for n, v in pairs]}


def affected_packages() -> set[str]:
    return {advisories.normalise(a["package"]) for a in snapshot_data()["advisories"]}


# Replay of the published 0.10.0 lock: every known range is evaluated, not assumed.

def test_snapshot_replay_finds_every_known_range_in_the_0_10_0_lock():
    status, out, _ = cli("inventory", "--snapshot", str(SNAPSHOT), "--environment", str(OLD_LOCK), "--now", REPLAY_NOW)
    data = snapshot_data()
    assert status == 1 and out["verdict"] == "refused" and out["reasons"] == []
    assert {m["id"] for m in out["matches"]} == {a["id"] for a in data["advisories"]}
    assert len(out["matches"]) == len(data["advisories"]) == 43
    assert out["severity_counts"] == {"critical": 3, "high": 23, "medium": 13, "low": 4}
    assert {m["package"] for m in out["matches"]} == affected_packages() and len(affected_packages()) == 10
    unpatched = [m for m in out["matches"] if m["first_patched"] is None]
    assert [(m["package"], m["affected"]) for m in unpatched] == [("pyjwt", ">= 2.11.0, <= 2.13.0")]
    assert {s["name"]: s["license"] for s in data["sources"]}["GitHub Advisory Database"] == "CC BY 4.0"


def test_locked_versions_match_no_known_advisory_range():
    status, out, _ = cli("inventory", "--snapshot", str(SNAPSHOT), "--lock", str(ROOT / "uv.lock"), "--now", REPLAY_NOW)
    assert (status, out["verdict"], out["reasons"], out["matches"]) == (0, "clear", [], [])


def test_declared_runtime_floors_exclude_every_known_range():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    reachable = advisories.lock_closure(lock, advisories.project_requirements(pyproject, None), "seo-monster")
    assert affected_packages() <= reachable  # the check below is not vacuous
    status, out, _ = cli("floors", "--snapshot", str(SNAPSHOT), "--pyproject", str(ROOT / "pyproject.toml"),
                         "--lock", str(ROOT / "uv.lock"), "--now", REPLAY_NOW)
    assert (status, out["verdict"], out["matches"]) == (0, "clear", [])


def test_existing_vulnerable_installs_cannot_satisfy_the_published_requirements():
    # pip and uv keep an installed dependency that still satisfies the requirement, so each
    # vulnerable 0.10.0 locked version must be rejected by the declared metadata itself.
    declared = {advisories.normalise(r.name): r for r in map(
        Requirement, tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"])}
    old = {advisories.normalise(p["name"]): p["version"] for p in json.loads(OLD_LOCK.read_text())["packages"]}
    survivors = {name: old[name] for name in affected_packages()
                 if name not in declared or declared[name].specifier.contains(Version(old[name]), prereleases=True)}
    assert survivors == {}


def test_workbench_cli_stays_dependency_free_and_its_mcp_extra_carries_the_floors():
    pyproject = tomllib.loads((ROOT / "companion" / "pyproject.toml").read_text())
    assert pyproject["project"]["dependencies"] == []
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    reachable = advisories.lock_closure(lock, advisories.project_requirements(pyproject, "mcp"), "seo-monster")
    assert {"anyio", "cryptography", "pyjwt", "python-multipart", "starlette"} <= reachable
    status, out, _ = cli("floors", "--snapshot", str(SNAPSHOT), "--pyproject", str(ROOT / "companion" / "pyproject.toml"),
                         "--extra", "mcp", "--lock", str(ROOT / "uv.lock"), "--now", REPLAY_NOW)
    assert (status, out["verdict"], out["matches"]) == (0, "clear", [])


# Semantics of ranges, names and nullable fields.

def test_package_name_variants_normalise_to_one_project():
    data = snapshot_data()
    raw = {a["package"] for a in data["advisories"] if advisories.normalise(a["package"]) == "pyjwt"}
    assert {"PyJWT", "pyjwt"} <= raw  # the source itself mixes spellings
    for spelling in ("PyJWT", "pyJWT", "PYJWT"):
        out = advisories.evaluate_inventory(advisories.load_snapshot(data, advisories.parse_time(REPLAY_NOW, "x"),
                                                                     advisories.datetime.timedelta(hours=24)),
                                            advisories.inventory_pairs(env((spelling, "2.13.0"))))
        assert len(out["matches"]) == 15 and out["reasons"] == []
    out = advisories.evaluate_inventory(advisories.load_snapshot(data, advisories.parse_time(REPLAY_NOW, "x"),
                                                                 advisories.datetime.timedelta(hours=24)),
                                        advisories.inventory_pairs(env(("Python_Multipart", "0.0.29"))))
    assert len(out["matches"]) == 4


def test_github_single_equals_range_matches_only_that_version():
    clauses = advisories.parse_range("= 2.13.0")
    assert advisories.in_range(Version("2.13.0"), clauses)
    assert not advisories.in_range(Version("2.13.1"), clauses)
    assert not advisories.in_range(Version("2.12.9"), clauses)
    assert advisories.floor_excludes(Requirement("pyjwt>=2.13.1"), clauses)
    assert not advisories.floor_excludes(Requirement("pyjwt>=2.13.0"), clauses)


def test_null_first_patched_is_preserved_and_never_treated_as_a_fix():
    clauses = advisories.parse_range(">= 2.11.0, <= 2.13.0")
    assert advisories.floor_excludes(Requirement("pyjwt>=2.13.1"), clauses)
    assert not advisories.floor_excludes(Requirement("pyjwt>=2.13.0"), clauses)
    assert not advisories.floor_excludes(Requirement("pyjwt>=2.0"), advisories.parse_range(">= 1.0"))  # no upper bound
    assert not advisories.floor_excludes(Requirement("pyjwt>=2.15.0; python_version < '3.13'"), clauses)


def test_only_lower_severity_matches_need_an_owner_decision_not_zero(tmp_path):
    data = snapshot_data()
    data["advisories"] = [a for a in data["advisories"] if a["severity"] in {"medium", "low"}]
    kept = {a["id"] for a in data["advisories"]}
    for query in data["queries"]:
        query["advisories"] = [i for i in query["advisories"] if i in kept]
    out = replay(tmp_path, data, env(("anyio", "4.13.0")))
    assert out["verdict"] == "owner-decision" and out["matches"] and out["reasons"] == []
    assert out["severity_counts"]["critical"] == out["severity_counts"]["high"] == 0


def test_replay_is_deterministic_and_binds_its_inputs():
    args = ("inventory", "--snapshot", str(SNAPSHOT), "--environment", str(OLD_LOCK), "--now", REPLAY_NOW)
    first, second = cli(*args), cli(*args)
    assert first[2] == second[2]
    assert first[1]["inputs"]["snapshot"] == hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
    assert first[1]["evaluated_at"] == REPLAY_NOW and first[1]["rule_version"] == 1


# Unavailable, stale, future-dated, malformed and contradictory inputs refuse clearance.

def mutate(change):
    def apply():
        data = snapshot_data()
        change(data)
        return data
    return apply


def first_with(data: dict, package: str) -> dict:
    return next(a for a in data["advisories"] if advisories.normalise(a["package"]) == package)


SNAPSHOT_REFUSALS = {
    "unavailable": (mutate(lambda d: d.update(status="unavailable")), "advisory-source-unavailable"),
    "status missing": (mutate(lambda d: d.pop("status")), "advisory-source-unavailable"),
    "wrong format": (mutate(lambda d: d.update(format="other/1")), "invalid-snapshot-format"),
    "newer rules": (mutate(lambda d: d.update(rule_version=2)), "unsupported-rule-version"),
    "date only": (mutate(lambda d: d.update(retrieved_at="2026-11-06")), "invalid-retrieved-at"),
    "no attribution": (mutate(lambda d: d.update(sources=[])), "missing-source-attribution"),
    "unknown operator": (mutate(lambda d: first_with(d, "anyio").update(affected="!= 4.14.2")), "invalid-affected-range"),
    "bad range version": (mutate(lambda d: first_with(d, "anyio").update(affected="< four")), "invalid-affected-range"),
    "empty range": (mutate(lambda d: first_with(d, "anyio").update(affected=" ")), "invalid-affected-range"),
    "unknown severity": (mutate(lambda d: first_with(d, "anyio").update(severity="moderate")), "invalid-severity"),
    "patched field missing": (mutate(lambda d: first_with(d, "anyio").pop("first_patched")), "missing-first-patched-field"),
    "patched inside range": (mutate(lambda d: first_with(d, "anyio").update(first_patched="4.13.0")),
                             "inconsistent-first-patched"),
    "unknown query id": (mutate(lambda d: d["queries"][0]["advisories"].append("GHSA-2222-3333-4444")),
                         "unknown-query-advisory"),
    "conflicting queries by alias": (
        mutate(lambda d: d["queries"].append({"package": "PyJWT", "version": "2.13.0", "advisories": []})),
        "conflicting-query-results"),
    "no queries": (mutate(lambda d: d.update(queries=[])), "invalid-queries"),
}


@pytest.mark.parametrize("case", sorted(SNAPSHOT_REFUSALS))
def test_invalid_or_unavailable_snapshot_refuses_clearance(tmp_path, case):
    build, reason = SNAPSHOT_REFUSALS[case]
    out = replay(tmp_path, build(), env(("anyio", "4.14.2")))
    assert out["verdict"] == "refused" and out["reasons"] == [reason]
    assert out["evaluation"] == "unavailable" and out["matches"] is None and out["severity_counts"] is None


@pytest.mark.parametrize(("now", "reason"), [("2026-11-07T18:30:00Z", "stale-snapshot"),
                                             ("2026-11-06T17:00:00Z", "future-dated-snapshot"),
                                             ("tomorrow", "invalid-now")])
def test_stale_or_future_dated_snapshot_refuses_clearance(tmp_path, now, reason):
    out = replay(tmp_path, snapshot_data(), env(("anyio", "4.14.2")), now=now)
    assert (out["verdict"], out["reasons"]) == ("refused", [reason])
    assert replay(tmp_path, snapshot_data(), env(("anyio", "4.14.2")))["verdict"] == "clear"


INVENTORY_REFUSALS = {
    "empty": ({"kind": "environment", "packages": []}, "empty-inventory"),
    "unknown kind": ({"kind": "guess", "packages": [{"name": "anyio", "version": "4.14.2"}]}, "invalid-inventory"),
    "contradictory alias versions": (env(("pyjwt", "2.15.0"), ("PyJWT", "2.13.0")), "contradictory-inventory"),
    "missing version": ({"kind": "environment", "packages": [{"name": "anyio"}]}, "invalid-inventory-entry"),
    "invalid version": (env(("anyio", "latest")), "invalid-inventory-version"),
    "invalid name": (env(("../anyio", "4.14.2")), "invalid-package-name"),
    "not queried": (env(("anyio", "4.14.2"), ("left-pad", "1.0.0")), "uncovered-inventory-package:left-pad"),
    "queried version absent": (env(("anyio", "4.15.0"),), "uncovered-inventory-package:anyio"),
}


@pytest.mark.parametrize("case", sorted(INVENTORY_REFUSALS))
def test_missing_contradictory_or_uncovered_inventory_refuses_clearance(tmp_path, case):
    inventory, reason = INVENTORY_REFUSALS[case]
    out = replay(tmp_path, snapshot_data(), inventory)
    assert out["verdict"] == "refused" and reason in out["reasons"]


def test_source_disagreement_refuses_instead_of_choosing_one_answer(tmp_path):
    data = snapshot_data()
    query = next(q for q in data["queries"] if q["package"] == "anyio" and q["version"] == "4.13.0")
    query["advisories"] = query["advisories"][:1]
    out = replay(tmp_path, data, env(("anyio", "4.13.0")))
    assert out["verdict"] == "refused" and out["reasons"] == ["query-range-disagreement"]


def test_lock_inventory_allows_marker_split_versions_but_environments_do_not(tmp_path):
    two = [{"name": "anyio", "version": "4.13.0"}, {"name": "anyio", "version": "4.14.2"}]
    assert len(advisories.inventory_pairs({"kind": "lock", "packages": two})) == 2
    with pytest.raises(advisories.Refusal, match="contradictory-inventory"):
        advisories.inventory_pairs({"kind": "environment", "packages": two})


@pytest.mark.parametrize("missing", ["snapshot", "lock"])
def test_unreadable_inputs_refuse_rather_than_report_zero(tmp_path, missing):
    snapshot = tmp_path / "absent.json" if missing == "snapshot" else SNAPSHOT
    lock = tmp_path / "absent.lock" if missing == "lock" else ROOT / "uv.lock"
    status, out, _ = cli("inventory", "--snapshot", str(snapshot), "--lock", str(lock), "--now", REPLAY_NOW)
    assert (status, out["verdict"], out["reasons"]) == (1, "refused", [f"unavailable-input:{missing}"])
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert cli("inventory", "--snapshot", str(bad), "--lock", str(lock), "--now", REPLAY_NOW)[1]["reasons"] == [
        "invalid-snapshot-json"]


def test_requirement_missing_from_the_lock_refuses_floor_clearance(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\ndependencies = ["not-locked-anywhere>=1"]\n')
    status, out, _ = cli("floors", "--snapshot", str(SNAPSHOT), "--pyproject", str(pyproject),
                         "--lock", str(ROOT / "uv.lock"), "--now", REPLAY_NOW)
    assert (status, out["reasons"]) == (1, ["requirement-not-in-lock:not-locked-anywhere"])


def test_floor_gap_reports_each_unexcluded_range(tmp_path):
    data = copy.deepcopy(tomllib.loads((ROOT / "pyproject.toml").read_text()))
    deps = [d for d in data["project"]["dependencies"] if advisories.normalise(Requirement(d).name) != "pyjwt"]
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'x'\ndependencies = " + json.dumps(deps + ["pyjwt>=2.13.0"]) + "\n")
    status, out, _ = cli("floors", "--snapshot", str(SNAPSHOT), "--pyproject", str(pyproject),
                         "--lock", str(ROOT / "uv.lock"), "--now", REPLAY_NOW)
    assert status == 1 and {m["package"] for m in out["matches"]} == {"pyjwt"}
    assert {m["id"] for m in out["matches"]} == {a["id"] for a in snapshot_data()["advisories"]
                                                 if advisories.normalise(a["package"]) == "pyjwt"
                                                 and advisories.in_range(Version("2.13.0"),
                                                                         advisories.parse_range(a["affected"]))}


# Independent review probes: each once cleared, downgraded or crashed.

def test_contradictory_affected_interval_refuses(tmp_path):
    for empty in (">= 3, < 2", "> 2.0, <= 2.0", "= 1.0, = 2.0", ">= 2.0, < 2.0"):
        data = snapshot_data()
        first_with(data, "anyio").update(affected=empty)
        out = replay(tmp_path, data, env(("anyio", "4.14.2")))
        assert (out["verdict"], out["reasons"]) == ("refused", ["empty-affected-range"]), empty


def test_duplicate_advisory_record_cannot_downgrade_severity(tmp_path):
    data = snapshot_data()
    critical = next(a for a in data["advisories"] if a["severity"] == "critical")
    data["advisories"].append(dict(critical, severity="low"))
    out = replay(tmp_path, data, env(("anyio", "4.14.2")))
    assert (out["verdict"], out["reasons"]) == ("refused", ["conflicting-advisory-records"])
    data = snapshot_data()
    data["advisories"].append(dict(critical))  # an identical repeat is not a conflict
    assert replay(tmp_path, data, env(("anyio", "4.14.2")))["verdict"] == "clear"


@pytest.mark.parametrize("member", [{"id": "GHSA-2222-3333-4444"}, ["GHSA-2222-3333-4444"], 7, None])
def test_non_string_query_member_refuses_without_traceback(tmp_path, member):
    data = snapshot_data()
    data["queries"][0]["advisories"].append(member)
    out = replay(tmp_path, data, env(("anyio", "4.14.2")))
    assert (out["verdict"], out["reasons"]) == ("refused", ["invalid-queries"])


@pytest.mark.parametrize("entry", [
    {"name": "pyjwt", "version": "2.13.0", "source": "registry"},
    {"name": "pyjwt", "source": {"registry": "https://pypi.org/simple"}},
    {"name": "pyjwt", "version": "2.13.0"},
    {"name": "pyjwt", "version": "2.13.0", "source": {"git": "https://example.invalid/pyjwt.git"}},
    "pyjwt==2.13.0",
])
def test_malformed_or_unverifiable_lock_entry_refuses_instead_of_being_dropped(entry):
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    lock["package"].append(entry)
    with pytest.raises(advisories.Refusal) as refusal:
        advisories.inventory_pairs(advisories.lock_inventory(lock, "seo-monster"))
    assert str(refusal.value) in {"invalid-lock-entry", "unverifiable-lock-source"}
    assert "pyjwt" not in str(refusal.value)


@pytest.mark.parametrize("text", ["", "[project]\nname = 'x'\n", "[project]\nname = 'x'\ndependencies = []\n",
                                  "[project]\nname = 'x'\ndependencies = [7]\n",
                                  "[project]\nname = 'x'\ndependencies = 'mcp'\n",
                                  "[project]\nname = 'x'\ndependencies = ['not a requirement ===']\n"])
def test_empty_or_invalid_project_metadata_refuses_floor_clearance(tmp_path, text):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(text)
    status, out, raw = cli("floors", "--snapshot", str(SNAPSHOT), "--pyproject", str(pyproject),
                           "--lock", str(ROOT / "uv.lock"), "--now", REPLAY_NOW)
    assert status == 1 and out["verdict"] == "refused"
    assert out["reasons"] in (["invalid-project-metadata"], ["empty-requirements"])
    assert b"not a requirement" not in raw


def test_unavailable_evaluation_reports_unknown_counts_not_zero(tmp_path):
    data = snapshot_data()
    data["status"] = "unavailable"
    out = replay(tmp_path, data, env(("anyio", "4.14.2")))
    assert out["evaluation"] == "unavailable" and out["severity_counts"] is None and out["matches"] is None
    data = snapshot_data()
    out = replay(tmp_path, data, env(("anyio", "4.13.0"), ("left-pad", "1.0.0")))
    assert out["evaluation"] == "partial" and out["verdict"] == "refused"
    out = replay(tmp_path, data, env(("anyio", "4.14.2")))
    assert out["evaluation"] == "complete" and out["severity_counts"] == dict.fromkeys(advisories.SEVERITIES, 0)


def test_provenance_binds_the_actual_inventory_and_policy(tmp_path, monkeypatch):
    def installed(pairs):
        monkeypatch.setattr(advisories, "installed_inventory", lambda: env(*pairs))
        return advisories.run(["inventory", "--snapshot", str(SNAPSHOT), "--installed", "--now", REPLAY_NOW])[1]
    a, b = installed([("anyio", "4.14.2")]), installed([("anyio", "4.14.2"), ("certifi", "2026.10.18")])
    assert a["verdict"] == b["verdict"] == "clear"
    assert a["inputs"]["inventory"] != b["inputs"]["inventory"]
    assert a["inputs"]["inventory_count"] == 1
    assert a["policy"] == {"mode": "inventory", "inventory_source": "installed", "extra": None, "max_age_hours": 24.0,
                           "blocking_severities": ["critical", "high"]}
    floors = advisories.run(["floors", "--snapshot", str(SNAPSHOT), "--pyproject", str(ROOT / "companion/pyproject.toml"),
                             "--extra", "mcp", "--lock", str(ROOT / "uv.lock"), "--now", REPLAY_NOW,
                             "--max-age-hours", "2"])[1]
    assert floors["policy"]["mode"] == "floors" and floors["policy"]["extra"] == "mcp"
    assert floors["policy"]["max_age_hours"] == 2.0 and "pyproject" in floors["inputs"]


@pytest.mark.parametrize("hours", ["inf", "nan", "-1", "0", "1e400", "87601"])
def test_unbounded_or_invalid_max_age_refuses_without_traceback(hours):
    status, out = advisories.run(["inventory", "--snapshot", str(SNAPSHOT), "--lock", str(ROOT / "uv.lock"),
                                  "--now", REPLAY_NOW, "--max-age-hours", hours])
    assert (status, out["verdict"], out["reasons"]) == (1, "refused", ["invalid-max-age"])


@pytest.mark.parametrize("extra", ["test", "dev"])
def test_workbench_test_and_dev_extras_floor_what_they_reach_without_mcp(extra):
    # httpx and httpx2 reach anyio and certifi without MCP (resolver evidence for these extras);
    # httpx is not in the engine lock, so its reach is declared here rather than derived.
    pyproject = tomllib.loads((ROOT / "companion" / "pyproject.toml").read_text())
    requirements = advisories.project_requirements(pyproject, extra)
    names = {advisories.normalise(r.name) for r in requirements}
    assert "mcp" not in names
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {advisories.normalise(p["name"]) for p in lock["package"]}
    reachable = advisories.lock_closure(lock, [r for r in requirements if advisories.normalise(r.name) in locked],
                                         "seo-monster")
    reached = (reachable | ({"anyio", "certifi"} if "httpx" in names else set())) & affected_packages()
    assert {"anyio", "certifi"} <= reached
    declared = {advisories.normalise(r.name): r for r in requirements}
    data = snapshot_data()
    gaps = [a["id"] for a in data["advisories"] if advisories.normalise(a["package"]) in reached and not (
        advisories.normalise(a["package"]) in declared
        and advisories.floor_excludes(declared[advisories.normalise(a["package"])], advisories.parse_range(a["affected"])))]
    assert gaps == []


# Independent review 2: false clears in floors mode and for local lock sources.

def range_inconsistent_snapshot() -> dict:
    data = snapshot_data()
    query = next(q for q in data["queries"] if q["package"] == "anyio" and q["version"] == "4.14.2")
    query["advisories"] = [a["id"] for a in data["advisories"] if advisories.normalise(a["package"]) == "anyio"][:1]
    return data


@pytest.mark.parametrize("mode", ["inventory", "floors"])
def test_query_listing_an_advisory_whose_range_excludes_the_version_refuses_in_every_mode(tmp_path, mode):
    snapshot = write(tmp_path, "snapshot.json", range_inconsistent_snapshot())
    if mode == "inventory":
        args = ["--environment", str(write(tmp_path, "inventory.json", env(("anyio", "4.14.2"))))]
    else:
        args = ["--pyproject", str(ROOT / "pyproject.toml"), "--lock", str(ROOT / "uv.lock")]
    status, out, _ = cli(mode, "--snapshot", str(snapshot), *args, "--now", REPLAY_NOW)
    assert (status, out["verdict"], out["reasons"]) == (1, "refused", ["query-range-disagreement"])
    assert out["evaluation"] == "unavailable"


@pytest.mark.parametrize("source", [{"editable": "vendor/pyjwt"}, {"virtual": "."}, {"editable": "."},
                                    {"virtual": "../elsewhere"}])
def test_local_lock_source_is_exempt_only_for_the_lock_root_project(tmp_path, source):
    lock_text = (ROOT / "uv.lock").read_text()
    entry = "\n[[package]]\nname = \"vendored-thing\"\nversion = \"1.0.0\"\nsource = " + (
        "{ " + ", ".join(f'{k} = "{v}"' for k, v in source.items()) + " }") + "\n"
    (tmp_path / "uv.lock").write_text(lock_text + entry)
    (tmp_path / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
    for mode, extra in (("inventory", []), ("floors", ["--pyproject", str(ROOT / "pyproject.toml")])):
        status, out, _ = cli(mode, "--snapshot", str(SNAPSHOT), "--lock", str(tmp_path / "uv.lock"), *extra,
                             "--now", REPLAY_NOW)
        assert (status, out["verdict"], out["reasons"]) == (1, "refused", ["unverifiable-lock-source"]), mode


def test_lock_root_comes_from_the_project_beside_the_lock(tmp_path):
    (tmp_path / "uv.lock").write_text((ROOT / "uv.lock").read_text())
    status, out, _ = cli("inventory", "--snapshot", str(SNAPSHOT), "--lock", str(tmp_path / "uv.lock"), "--now", REPLAY_NOW)
    assert (status, out["reasons"]) == (1, ["unavailable-input:lock-project"])
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'someone-else'\n")
    status, out, _ = cli("inventory", "--snapshot", str(SNAPSHOT), "--lock", str(tmp_path / "uv.lock"), "--now", REPLAY_NOW)
    assert (status, out["reasons"]) == (1, ["unverifiable-lock-source"])
    (tmp_path / "pyproject.toml").write_text((ROOT / "pyproject.toml").read_text())
    status, out, _ = cli("inventory", "--snapshot", str(SNAPSHOT), "--lock", str(tmp_path / "uv.lock"), "--now", REPLAY_NOW)
    assert status == 0 and out["inputs"]["lock-project"] == hashlib.sha256((ROOT / "pyproject.toml").read_bytes()).hexdigest()


def test_the_advisory_helper_dependency_is_declared_for_development():
    dev = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["dev"]
    assert "packaging" in {advisories.normalise(Requirement(r).name) for r in dev}
