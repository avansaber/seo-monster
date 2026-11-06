"""Pre-implementation simulations for rule replay and GA4 attribution."""

from __future__ import annotations

import json

import pytest

from seomonster_workbench.cli import build_parser, run
from seomonster_workbench.engine import Workbench
from seomonster_workbench.portable import build_export


def _project(wb, fixtures_dir):
    wb.create_project("replay", "Replay", {"claim": "Approved source material"}, ["https://example.test"])
    result = wb.ingest("replay", fixtures_dir / "gsc_baseline.json")
    return result["snapshot_id"]


def test_simulation_rule_replay_is_deterministic_and_non_mutating(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "replay.db") as wb:
        snapshot_id = _project(wb, fixtures_dir)
        before = build_export(wb.store, "replay", "2026-09-12T12:00:00Z")
        first = wb.preview_rule_replay("replay", snapshot_id)
        second = wb.preview_rule_replay("replay", snapshot_id)
        assert first == second
        assert first["state"] == "identical"
        assert first["external_execution_available"] is False
        assert build_export(wb.store, "replay", "2026-09-12T12:00:00Z") == before


def test_simulation_rule_replay_detects_same_id_tampering(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "tamper.db") as wb:
        snapshot_id = _project(wb, fixtures_dir)
        finding_id = wb.store.rows("findings", "replay")[0]["id"]
        wb.store.connection.execute("UPDATE findings SET calculation=? WHERE id=?", ("tampered", finding_id))
        wb.store.connection.commit()
        result = wb.preview_rule_replay("replay", snapshot_id)
        assert result["state"] == "different"
        assert result["collections"]["findings"]["changed"] == [finding_id]


def test_simulation_rule_replay_rejects_cross_project_snapshot(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "cross.db") as wb:
        snapshot_id = _project(wb, fixtures_dir)
        wb.create_project("other", "Other", {}, ["https://example.test"])
        with pytest.raises(ValueError, match="different project"):
            wb.preview_rule_replay("other", snapshot_id)


def test_simulation_rule_replay_reports_dependent_records(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "dependencies.db") as wb:
        snapshot_id = _project(wb, fixtures_dir)
        recommendation_id = wb.board("replay")[0]["recommendation"]["id"]
        wb.draft_plan("replay", recommendation_id, [], "Restore separately applied change.")
        preview = wb.preview_rule_replay("replay", snapshot_id)
        assert preview["dependencies"]["action_plans"]
        assert preview["dependencies"]["review_required_if_migrating"] is False
        finding_id = wb.store.rows("findings", "replay")[0]["id"]
        wb.store.connection.execute("UPDATE findings SET calculation=? WHERE id=?", ("tampered", finding_id))
        wb.store.connection.commit()
        changed = wb.preview_rule_replay("replay", snapshot_id)
        assert changed["dependencies"]["review_required_if_migrating"] is True


def test_simulation_rule_replay_ignores_human_state_but_detects_provenance_drift(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "human-state.db") as wb:
        snapshot_id = _project(wb, fixtures_dir)
        recommendation_id = wb.board("replay")[0]["recommendation"]["id"]
        plan = wb.draft_plan("replay", recommendation_id, [], "Restore separately applied change.")
        wb.approve_plan(plan["id"], "Operator approval")
        assert wb.preview_rule_replay("replay", snapshot_id)["state"] == "identical"
        row = wb.store.one("recommendations", recommendation_id)
        provenance = wb.store.decode(row)["provenance"]
        provenance["calculation"] = "tampered"
        wb.store.connection.execute(
            "UPDATE recommendations SET provenance_json=? WHERE id=?", (json.dumps(provenance), recommendation_id)
        )
        wb.store.connection.commit()
        changed = wb.preview_rule_replay("replay", snapshot_id)
        assert changed["collections"]["recommendations"]["changed"] == [recommendation_id]


def test_simulation_rule_replay_rejects_corrupted_raw_snapshot(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "corrupt.db") as wb:
        snapshot_id = _project(wb, fixtures_dir)
        wb.store.connection.execute("UPDATE snapshots SET raw_json='{}' WHERE id=?", (snapshot_id,))
        wb.store.connection.commit()
        with pytest.raises(ValueError, match="stored snapshot"):
            wb.preview_rule_replay("replay", snapshot_id)


def test_simulation_rule_version_drift_is_previewed_without_mutation(tmp_path, fixtures_dir, monkeypatch):
    import seomonster_workbench.replay as replay_module
    import seomonster_workbench.rules as rules_module

    with Workbench(tmp_path / "version.db") as wb:
        snapshot_id = _project(wb, fixtures_dir)
        recommendation_id = wb.board("replay")[0]["recommendation"]["id"]
        plan = wb.draft_plan("replay", recommendation_id, [], "Restore separately applied change.")
        before = build_export(wb.store, "replay", "2026-09-12T12:00:00Z")
        monkeypatch.setattr(rules_module, "RULESET_VERSION", "2099-01-01.1")
        monkeypatch.setattr(replay_module, "RULESET_VERSION", "2099-01-01.1")
        result = wb.preview_rule_replay("replay", snapshot_id)
        assert result["state"] == "different"
        assert result["collections"]["recommendations"]["added"]
        assert result["collections"]["recommendations"]["removed"]
        assert result["dependencies"]["action_plans"] == [plan["id"]]
        assert result["dependencies"]["review_required_if_migrating"] is True
        assert result["semantics"]["mutation"] == "not_available"
        assert build_export(wb.store, "replay", "2026-09-12T12:00:00Z") == before


def test_simulation_rule_replay_preserves_unavailable_ga4_signal(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "unavailable.db") as wb:
        wb.create_project("replay", "Replay", {}, ["https://example.test"])
        snapshot_id = wb.ingest("replay", fixtures_dir / "ga4_cold_start.json")["snapshot_id"]
        result = wb.preview_rule_replay("replay", snapshot_id)
        assert result["state"] == "identical"
        evidence = wb.store.decode(wb.store.rows("evidence", "replay")[0])
        assert evidence["availability"] == "unavailable"


def test_simulation_rule_replay_cli_is_preview_only(tmp_path, fixtures_dir):
    database = tmp_path / "cli.db"
    with Workbench(database) as wb:
        snapshot_id = _project(wb, fixtures_dir)
    result = run(build_parser().parse_args([
        "--db", str(database), "replay-preview", "--project", "replay", "--snapshot", snapshot_id,
    ]))
    assert result["state"] == "identical"
    assert result["semantics"]["mutation"] == "not_available"
    assert result["external_execution_available"] is False


def test_simulation_rule_replay_rejects_missing_snapshot(tmp_path):
    with Workbench(tmp_path / "missing.db") as wb:
        wb.create_project("replay", "Replay", {}, ["https://example.test"])
        with pytest.raises(ValueError, match="unknown stored snapshot"):
            wb.preview_rule_replay("replay", "snap_missing")


def test_simulation_rule_replay_rejects_invalid_human_state(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "invalid-state.db") as wb:
        snapshot_id = _project(wb, fixtures_dir)
        recommendation_id = wb.board("replay")[0]["recommendation"]["id"]
        wb.store.connection.execute(
            "UPDATE recommendations SET state='executed' WHERE id=?", (recommendation_id,)
        )
        wb.store.connection.commit()
        with pytest.raises(ValueError, match="workflow state"):
            wb.preview_rule_replay("replay", snapshot_id)


def test_simulation_rule_replay_rejects_malformed_stored_provenance(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "invalid-provenance.db") as wb:
        snapshot_id = _project(wb, fixtures_dir)
        recommendation_id = wb.board("replay")[0]["recommendation"]["id"]
        wb.store.connection.execute(
            "UPDATE recommendations SET provenance_json='[]' WHERE id=?", (recommendation_id,)
        )
        wb.store.connection.commit()
        with pytest.raises(ValueError, match="stored analytical provenance"):
            wb.preview_rule_replay("replay", snapshot_id)


def test_simulation_rule_replay_reports_all_ownership_dependents(tmp_path, fixtures_dir, monkeypatch):
    import seomonster_workbench.replay as replay_module
    import seomonster_workbench.rules as rules_module

    with Workbench(tmp_path / "ownership.db") as wb:
        wb.create_project(
            "replay", "Replay", {}, ["https://example.test", "https://docs.example.test"]
        )
        snapshot_id = wb.ingest("replay", fixtures_dir / "gsc_baseline.json")["snapshot_id"]
        wb.ingest("replay", fixtures_dir / "gsc_portfolio_site_b.json")
        conflict = wb.ownership_board("replay")[0]["conflict"]
        assignment = wb.record_ownership_assignment(
            "replay", "query", "evidence first seo", "https://example.test/evidence", "primary",
            "Primary explanation", "Operator-declared routing.", conflict["evidence_ids"],
        )
        decision = wb.record_ownership_decision(
            "replay", conflict["id"], "accept", None, {}, "Intentional overlap."
        )
        plan = wb.draft_ownership_plan(
            "replay", decision["id"], "Restore independently applied changes."
        )
        monkeypatch.setattr(rules_module, "RULESET_VERSION", "2099-01-01.1")
        monkeypatch.setattr(replay_module, "RULESET_VERSION", "2099-01-01.1")
        dependencies = wb.preview_rule_replay("replay", snapshot_id)["dependencies"]
        assert dependencies["ownership_assignments"] == [assignment["id"]]
        assert dependencies["ownership_conflicts"] == [conflict["id"]]
        assert dependencies["ownership_decisions"] == [decision["id"]]
        assert dependencies["ownership_action_plans"] == [plan["id"]]


def test_simulation_replay_preview_does_not_create_missing_database(tmp_path):
    database = tmp_path / "absent.db"
    args = build_parser().parse_args([
        "--db", str(database), "replay-preview", "--project", "replay", "--snapshot", "snap_missing",
    ])
    with pytest.raises(ValueError, match="existing database"):
        run(args)
    assert database.exists() is False
