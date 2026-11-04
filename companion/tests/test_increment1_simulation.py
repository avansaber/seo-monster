"""Pre-implementation simulations for the portable restore increment."""

from __future__ import annotations

import copy
import json

import pytest
from jsonschema import validate

from seomonster_workbench.backup import (
    BACKUP_FORMAT_VERSION,
    apply_restore,
    build_backup,
    load_backup,
    preview_restore,
    validate_backup,
    validate_project_export,
)
from seomonster_workbench.cli import build_parser, run
from seomonster_workbench.engine import Workbench
from seomonster_workbench.portable import build_export


def _project_export(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "source.db") as wb:
        wb.create_project(
            "portable", "Portable project", {"approved_claims": ["Approved source material."]},
            ["https://example.test", "https://docs.example.test"], "Portable portfolio",
            "2026-09-12T00:00:00Z",
        )
        wb.ingest("portable", fixtures_dir / "gsc_baseline.json")
        return build_export(wb.store, "portable", "2026-09-12T01:00:00Z")


def _backup(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "source-full.db") as wb:
        wb.create_project(
            "portable", "Portable project", {"approved_claims": ["Approved source material."]},
            ["https://example.test", "https://docs.example.test"], "Portable portfolio",
            "2026-09-12T00:00:00Z",
        )
        wb.ingest("portable", fixtures_dir / "gsc_baseline.json")
        wb.ingest("portable", fixtures_dir / "gsc_portfolio_site_b.json")
        conflict = wb.ownership_board("portable")[0]["conflict"]
        assignment = wb.record_ownership_assignment(
            "portable", "query", "evidence first seo", "https://example.test/evidence", "primary",
            "Primary explanation", "Operator-declared routing.", conflict["evidence_ids"],
            created_at="2026-09-12T00:10:00Z",
        )
        decision = wb.record_ownership_decision(
            "portable", conflict["id"], "accept", None, {}, "Intentional overlap.",
            decided_at="2026-09-12T00:20:00Z",
        )
        wb.draft_ownership_plan(
            "portable", decision["id"], "Restore independently applied changes.",
            created_at="2026-09-12T00:30:00Z",
        )
        assert assignment["source"] == "human_declared"
        return build_backup(wb.store, "portable", "2026-09-12T01:00:00Z")


def test_simulation_project_export_is_self_contained_for_restore(tmp_path, fixtures_dir):
    payload = _backup(tmp_path, fixtures_dir)
    assert payload["format_version"] == BACKUP_FORMAT_VERSION
    assert payload["portfolio"] == {
        "id": payload["project_export"]["project"]["portfolio_id"],
        "name": "Portable portfolio",
        "created_at": "2026-09-12T00:00:00Z",
    }
    assert payload["project_export"]["format_version"] == "seomonster-workbench-export/v1"
    assert payload["ownership_export"]["format_version"] == "seomonster-workbench-ownership/v1"
    schema = json.loads((fixtures_dir.parent.parent / "schemas" / "backup-v2.schema.json").read_text())
    validate(payload, schema)


def test_simulation_project_export_rejects_dangling_references(tmp_path, fixtures_dir):
    payload = copy.deepcopy(_project_export(tmp_path, fixtures_dir))
    payload["recommendations"][0]["finding_id"] = "finding_missing"
    with pytest.raises(ValueError, match="reference"):
        validate_project_export(payload)


def test_simulation_project_export_rejects_version_skew(tmp_path, fixtures_dir):
    payload = copy.deepcopy(_project_export(tmp_path, fixtures_dir))
    payload["format_version"] = "seomonster-workbench-export/v999"
    with pytest.raises(ValueError, match="version"):
        validate_project_export(payload)


def test_simulation_restore_has_separate_preview_and_atomic_apply(tmp_path, fixtures_dir):
    payload = _backup(tmp_path, fixtures_dir)
    with Workbench(tmp_path / "target.db") as wb:
        before = wb.preview_restore(payload, "portable")
        assert before["state"] == "create" and before["applicable"] is True
        assert wb.store.has_project("portable") is False
        restored = wb.restore_backup(payload, "portable")
        assert restored["state"] == "restored"
        assert wb.preview_restore(payload, "portable")["state"] == "noop"
        assert wb.restore_backup(payload, "portable")["state"] == "noop"
        rebuilt = build_backup(wb.store, "portable", "2026-09-12T01:00:00Z")
        assert rebuilt == payload


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["project_export"]["recommendations"][0].update(finding_id="missing"), "reference"),
        (lambda value: value["project_export"].update(format_version="future/v2"), "version"),
        (lambda value: value["ownership_export"]["sites"][0].update(name="Different"), "disagree"),
        (lambda value: value["component_hashes"].update(project_export_canonical_sha256="0" * 64), "hash"),
        (lambda value: value["project_export"]["project"].update(truth_score=1), "invalid"),
        (lambda value: value["project_export"]["project"]["business_context"].update(api_key="sensitive"), "secret"),
    ],
)
def test_simulation_backup_rejects_tampering_before_restore(tmp_path, fixtures_dir, mutate, message):
    payload = copy.deepcopy(_backup(tmp_path, fixtures_dir))
    mutate(payload)
    with pytest.raises(ValueError, match=message):
        validate_backup(payload)


def test_simulation_conflicting_existing_project_is_reported_without_mutation(tmp_path, fixtures_dir):
    payload = _backup(tmp_path, fixtures_dir)
    with Workbench(tmp_path / "conflict.db") as wb:
        wb.create_project(
            "portable", "Different project", {"approved_claims": ["Different approved material."]},
            ["https://example.test"], "Different portfolio", "2026-09-12T00:00:00Z",
        )
        report = preview_restore(wb.store, payload, "portable")
        assert report["state"] == "conflict" and report["applicable"] is False
        assert report["conflicts"]
        with pytest.raises(ValueError, match="not applied"):
            apply_restore(wb.store, payload, "portable")
        project = wb.store.rows("projects", "portable")[0]
        assert json.loads(project["business_context_json"])["approved_claims"] == ["Different approved material."]


def test_simulation_mid_restore_failure_rolls_back_every_record(tmp_path, fixtures_dir, monkeypatch):
    payload = _backup(tmp_path, fixtures_dir)
    with Workbench(tmp_path / "rollback.db") as wb:
        original = wb.store.restore_records

        def fail_on_evidence(table, records):
            if table == "evidence":
                raise RuntimeError("deliberate restore failure")
            return original(table, records)

        monkeypatch.setattr(wb.store, "restore_records", fail_on_evidence)
        with pytest.raises(RuntimeError, match="deliberate"):
            wb.restore_backup(payload, "portable")
        assert wb.store.has_project("portable") is False
        assert wb.store.record_by_id("portfolios", payload["portfolio"]["id"]) is None


def test_simulation_cli_preview_and_restore_keep_states_separate(tmp_path, fixtures_dir):
    payload = _backup(tmp_path, fixtures_dir)
    backup_file = tmp_path / "backup.json"
    backup_file.write_text(json.dumps(payload))
    database = tmp_path / "cli.db"
    parser = build_parser()
    preview = run(parser.parse_args([
        "--db", str(database), "restore-preview", "--file", str(backup_file), "--project", "portable",
    ]))
    assert preview["state"] == "create"
    with Workbench(database) as wb:
        assert wb.store.has_project("portable") is False
    restored = run(parser.parse_args([
        "--db", str(database), "restore", "--file", str(backup_file), "--project", "portable",
    ]))
    assert restored["state"] == "restored" and restored["external_execution_available"] is False


@pytest.mark.parametrize(
    "raw",
    [
        b'{"format_version":"one","format_version":"two"}',
        b"\xff\xfe\x00",
        (b"[" * 2_000) + (b"]" * 2_000),
    ],
)
def test_simulation_backup_loader_rejects_ambiguous_or_malformed_json(tmp_path, raw):
    source = tmp_path / "ambiguous.json"
    source.write_bytes(raw)
    with pytest.raises(ValueError, match="backup"):
        load_backup(source)
