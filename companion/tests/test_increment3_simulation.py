"""Adversarial simulations for the append-only deployment-observation increment."""

from __future__ import annotations

import copy
import json

import pytest
from jsonschema import ValidationError, validate

from seomonster_workbench.backup import (
    BACKUP_FORMAT_VERSION,
    LEGACY_BACKUP_FORMAT_VERSION,
    apply_restore,
    build_backup,
    validate_backup,
)
from seomonster_workbench.change_ledger import (
    CHANGE_FORMAT_VERSION,
    MAX_OBSERVATION_BYTES,
    build_change_export,
    load_observation,
    validate_change_export,
)
from seomonster_workbench.cli import build_parser, run
from seomonster_workbench.engine import Workbench
from seomonster_workbench.store import SCHEMA_VERSION
from seomonster_workbench.util import digest


def _setup(wb, fixtures_dir, approve_at=None):
    wb.create_project("changes", "Changes", {"claim": "Approved source material"}, ["https://example.test"])
    wb.ingest("changes", fixtures_dir / "gsc_baseline.json")
    rec = next(item["recommendation"]["id"] for item in wb.board("changes")
               if item["finding"]["kind"] == "striking_distance")
    plan = wb.draft_plan("changes", rec, [], "Restore the prior synthetic page revision.", "2026-08-02T00:00:00Z")
    if approve_at:
        wb.approve_plan(plan["id"], "Approved for separate execution.", approve_at)
    return plan


def _observation(fixtures_dir):
    return json.loads((fixtures_dir / "deployment_observation.json").read_text())


def _rehash_record(item):
    item["record_hash"] = digest({key: value for key, value in item.items() if key not in {"id", "record_hash"}})


def test_simulation_observation_is_append_only_and_never_execution(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "ledger.db") as wb:
        plan = _setup(wb, fixtures_dir)
        recorded = wb.record_deployment_observation(
            "changes", plan["id"], _observation(fixtures_dir), "2026-08-04T00:00:00Z"
        )
        again = wb.record_deployment_observation(
            "changes", plan["id"], _observation(fixtures_dir), "2026-08-05T00:00:00Z"
        )
        assert again == recorded
        assert recorded["external_execution_available"] is False
        assert recorded["approval_snapshot"]["state"] == "no_preceding_approval_timestamp"
        assert wb.store.one("action_plans", plan["id"])["state"] == "draft"
        assert len(wb.store.rows("deployment_observations", "changes")) == 1


def test_simulation_prior_approval_is_derived_by_observation_time(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "approval.db") as wb:
        plan = _setup(wb, fixtures_dir, "2026-08-03T00:00:00Z")
        observed = wb.record_deployment_observation(
            "changes", plan["id"], _observation(fixtures_dir), "2026-08-04T00:00:00Z"
        )
        assert observed["approval_snapshot"]["state"] == "approval_timestamp_precedes_observation"
        assert len(observed["approval_snapshot"]["approval_decision_ids"]) == 1


def test_simulation_later_backdated_approval_does_not_rewrite_frozen_history(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "history.db") as wb:
        plan = _setup(wb, fixtures_dir)
        observed = wb.record_deployment_observation(
            "changes", plan["id"], _observation(fixtures_dir), "2026-08-04T00:00:00Z"
        )
        # Entered after import, but the legacy caller asserts an earlier timestamp.
        wb.approve_plan(plan["id"], "Later record with backdated asserted time.", "2026-08-01T00:00:00Z")
        frozen = wb.inspect_deployment_observation("changes", observed["id"])
        assert frozen["plan_snapshot"]["state"] == "draft"
        assert frozen["approval_snapshot"]["state"] == "no_preceding_approval_timestamp"
        assert frozen["plan_snapshot_hash"] == digest(frozen["plan_snapshot"])
        assert frozen["approval_snapshot_hash"] == digest(frozen["approval_snapshot"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["source"].update(reference="/Users/example/private/receipt.json"),
        lambda value: value["source"].update(authorization="Bearer synthetic-example-token"),
        lambda value: value.update(verification_state="executed"),
        lambda value: value["operations"][0].update(external_write=True),
    ],
)
def test_simulation_observation_rejects_unsafe_or_ambiguous_input(tmp_path, fixtures_dir, mutation):
    with Workbench(tmp_path / "reject.db") as wb:
        plan = _setup(wb, fixtures_dir)
        value = _observation(fixtures_dir)
        mutation(value)
        with pytest.raises(ValueError):
            wb.record_deployment_observation("changes", plan["id"], value, "2026-08-04T00:00:00Z")
        assert wb.store.rows("deployment_observations", "changes") == []


def test_simulation_change_export_is_portable_and_tamper_evident(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "export.db") as wb:
        plan = _setup(wb, fixtures_dir, "2026-08-03T00:00:00Z")
        wb.record_deployment_observation("changes", plan["id"], _observation(fixtures_dir), "2026-08-04T00:00:00Z")
        payload = build_change_export(wb.store, "changes", "2026-09-12T00:00:00Z")
    assert payload["format_version"] == CHANGE_FORMAT_VERSION
    assert payload["external_execution_available"] is False
    schema = json.loads((fixtures_dir.parent.parent / "schemas" / "change-ledger-v1.schema.json").read_text())
    validate(payload, schema)
    schema_rejected = copy.deepcopy(payload)
    schema_rejected["deployment_observations"][0].pop("summary")
    schema_rejected["deployment_observations"][0]["unexpected"] = "field"
    with pytest.raises(ValidationError):
        validate(schema_rejected, schema)
    tampered = copy.deepcopy(payload)
    tampered["deployment_observations"][0]["plan_snapshot"]["rollback_notes"] = "tampered"
    _rehash_record(tampered["deployment_observations"][0])
    with pytest.raises(ValueError, match="plan snapshot"):
        validate_change_export(tampered)
    omitted = copy.deepcopy(payload)
    omitted["deployment_observations"][0]["approval_snapshot"]["approval_decision_ids"] = []
    omitted["deployment_observations"][0]["approval_snapshot"]["state"] = "no_preceding_approval_timestamp"
    _rehash_record(omitted["deployment_observations"][0])
    with pytest.raises(ValueError, match="approval snapshot hash"):
        validate_change_export(omitted)
    impossible = copy.deepcopy(payload)
    impossible["deployment_observations"][0]["recorded_at"] = "2026-08-01T00:00:00Z"
    with pytest.raises(ValueError, match="predates"):
        validate_change_export(impossible)
    unsafe_snapshot = copy.deepcopy(payload)
    snapshot = unsafe_snapshot["deployment_observations"][0]["plan_snapshot"]
    snapshot.update(state="executed", rollback_notes="changed", steps=[])
    unsafe_snapshot["deployment_observations"][0]["plan_snapshot_hash"] = digest(snapshot)
    _rehash_record(unsafe_snapshot["deployment_observations"][0])
    with pytest.raises(ValueError, match="plan snapshot"):
        validate_change_export(unsafe_snapshot)


def test_simulation_backup_v2_round_trips_ledger_and_accepts_v1(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "source.db") as wb:
        plan = _setup(wb, fixtures_dir, "2026-08-03T00:00:00Z")
        wb.record_deployment_observation("changes", plan["id"], _observation(fixtures_dir), "2026-08-04T00:00:00Z")
        current = build_backup(wb.store, "changes", "2026-09-12T00:00:00Z")
    assert current["format_version"] == BACKUP_FORMAT_VERSION
    assert current["storage_schema_version"] == SCHEMA_VERSION == 3
    backup_schema = json.loads((fixtures_dir.parent.parent / "schemas" / "backup-v2.schema.json").read_text())
    validate(current, backup_schema)
    loose = copy.deepcopy(current)
    loose["change_ledger_export"] = {}
    loose["component_hashes"]["change_ledger_export_canonical_sha256"] = digest({})
    with pytest.raises(ValidationError):
        validate(loose, backup_schema)
    with Workbench(tmp_path / "target.db") as wb:
        assert apply_restore(wb.store, current, "changes")["state"] == "restored"
        assert len(wb.store.rows("deployment_observations", "changes")) == 1

    legacy = copy.deepcopy(current)
    legacy["format_version"] = LEGACY_BACKUP_FORMAT_VERSION
    legacy["storage_schema_version"] = 2
    legacy["ownership_export"]["storage_schema_version"] = 2
    legacy.pop("change_ledger_export")
    legacy["component_hashes"].pop("change_ledger_export_canonical_sha256")
    legacy["component_hashes"]["ownership_export_canonical_sha256"] = digest(legacy["ownership_export"])
    validate_backup(legacy)
    with Workbench(tmp_path / "legacy-target.db") as wb:
        assert apply_restore(wb.store, legacy, "changes")["state"] == "restored"
        assert wb.store.rows("deployment_observations", "changes") == []


def test_simulation_cli_missing_database_has_no_side_effect(tmp_path, fixtures_dir):
    database = tmp_path / "missing.db"
    args = build_parser().parse_args([
        "--db", str(database), "deployment-record", "--project", "changes", "--plan", "plan_missing",
        "--file", str(fixtures_dir / "deployment_observation.json"),
    ])
    with pytest.raises(ValueError, match="existing database"):
        run(args)
    assert database.exists() is False


def test_simulation_observation_loader_is_bounded_and_rejects_duplicate_keys(tmp_path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"format_version":"a","format_version":"b"}')
    with pytest.raises(ValueError, match="duplicate"):
        load_observation(duplicate)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_OBSERVATION_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        load_observation(oversized)


def test_simulation_rule_replay_reports_deployment_dependents(tmp_path, fixtures_dir, monkeypatch):
    import seomonster_workbench.replay as replay_module
    import seomonster_workbench.rules as rules_module

    with Workbench(tmp_path / "replay.db") as wb:
        plan = _setup(wb, fixtures_dir)
        observation = wb.record_deployment_observation(
            "changes", plan["id"], _observation(fixtures_dir), "2026-08-04T00:00:00Z"
        )
        snapshot_id = wb.store.rows("snapshots", "changes")[0]["id"]
        monkeypatch.setattr(rules_module, "RULESET_VERSION", "2099-01-01.1")
        monkeypatch.setattr(replay_module, "RULESET_VERSION", "2099-01-01.1")
        result = wb.preview_rule_replay("changes", snapshot_id)
        assert result["dependencies"]["deployment_observations"] == [observation["id"]]
        assert result["dependencies"]["review_required_if_migrating"] is True


def test_simulation_observation_rejects_cross_project_plan(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "cross.db") as wb:
        plan = _setup(wb, fixtures_dir)
        wb.create_project("other", "Other", {}, ["https://example.test"])
        with pytest.raises(ValueError, match="another project"):
            wb.record_deployment_observation(
                "other", plan["id"], _observation(fixtures_dir), "2026-08-04T00:00:00Z"
            )
        assert wb.store.rows("deployment_observations", "other") == []


def test_simulation_cli_records_inspects_and_exports_without_overwrite(tmp_path, fixtures_dir):
    database = tmp_path / "cli.db"
    with Workbench(database) as wb:
        plan = _setup(wb, fixtures_dir)
    parser = build_parser()
    recorded = run(parser.parse_args([
        "--db", str(database), "deployment-record", "--project", "changes", "--plan", plan["id"],
        "--file", str(fixtures_dir / "deployment_observation.json"), "--at", "2026-08-04T00:00:00Z",
    ]))
    inspected = run(parser.parse_args([
        "--db", str(database), "deployment-inspect", "--project", "changes",
        "--observation", recorded["id"],
    ]))
    assert inspected == recorded
    output = tmp_path / "change-ledger.json"
    receipt = run(parser.parse_args([
        "--db", str(database), "change-export", "--project", "changes", "--output", str(output),
        "--at", "2026-09-12T00:00:00Z",
    ]))
    assert receipt["deployment_observations"] == 1 and receipt["external_execution_available"] is False
    with pytest.raises(FileExistsError):
        run(parser.parse_args([
            "--db", str(database), "change-export", "--project", "changes", "--output", str(output),
            "--at", "2026-09-12T00:00:00Z",
        ]))


def test_simulation_inspection_and_repeat_import_reject_tampered_local_row(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "tampered.db") as wb:
        plan = _setup(wb, fixtures_dir)
        observation = _observation(fixtures_dir)
        recorded = wb.record_deployment_observation(
            "changes", plan["id"], observation, "2026-08-04T00:00:00Z"
        )
        wb.store.connection.execute(
            "UPDATE deployment_observations SET summary='tampered' WHERE id=?", (recorded["id"],)
        )
        wb.store.connection.commit()
        with pytest.raises(ValueError, match="record hash"):
            wb.inspect_deployment_observation("changes", recorded["id"])
        with pytest.raises(ValueError, match="record hash"):
            wb.record_deployment_observation(
                "changes", plan["id"], observation, "2026-08-05T00:00:00Z"
            )


def test_simulation_same_source_observation_can_be_linked_to_two_plans(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "two-plans.db") as wb:
        first = _setup(wb, fixtures_dir)
        other_rec = next(item["recommendation"]["id"] for item in wb.board("changes")
                         if item["recommendation"]["id"] != first["recommendation_id"])
        second = wb.draft_plan("changes", other_rec, [], "Restore the other synthetic revision.")
        source = _observation(fixtures_dir)
        one = wb.record_deployment_observation("changes", first["id"], source, "2026-08-04T00:00:00Z")
        two = wb.record_deployment_observation("changes", second["id"], source, "2026-08-04T00:00:00Z")
        assert one["id"] != two["id"]
        assert one["observation_hash"] == two["observation_hash"]


def test_simulation_replay_reports_decisions_for_transitive_dependent_plan(tmp_path, fixtures_dir, monkeypatch):
    import seomonster_workbench.replay as replay_module
    import seomonster_workbench.rules as rules_module

    with Workbench(tmp_path / "transitive.db") as wb:
        first = _setup(wb, fixtures_dir)
        baseline_snapshot = next(row["id"] for row in wb.store.rows("snapshots", "changes")
                                 if row["tool"] == "gsc_search_analytics")
        wb.ingest("changes", fixtures_dir / "content_decay.json")
        second_rec = next(item["recommendation"]["id"] for item in wb.board("changes")
                          if item["finding"]["kind"] == "content_decay_signal")
        second = wb.draft_plan(
            "changes", second_rec, [first["id"]], "Restore the dependent synthetic revision."
        )
        second_decisions = {
            row["id"] for row in wb.store.rows("decisions", "changes") if row["recommendation_id"] == second_rec
        }
        monkeypatch.setattr(rules_module, "RULESET_VERSION", "2099-01-01.1")
        monkeypatch.setattr(replay_module, "RULESET_VERSION", "2099-01-01.1")
        dependencies = wb.preview_rule_replay("changes", baseline_snapshot)["dependencies"]
        assert second["id"] in dependencies["action_plans"]
        assert second_decisions <= set(dependencies["decisions"])
