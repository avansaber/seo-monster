import copy
import json
import sqlite3

import pytest
from jsonschema import validate

from seomonster_workbench.cli import build_parser, run
from seomonster_workbench.engine import Workbench
from seomonster_workbench.ownership import normalize_key
from seomonster_workbench.portable import build_ownership_export, validate_ownership_export
from seomonster_workbench.store import MIGRATIONS, SCHEMA_VERSION


SITES = ["https://example.test", "https://docs.example.test"]


def setup_portfolio(workbench, fixtures):
    workbench.create_project("portfolio", "Portfolio", {"claim": "Approved"}, SITES)
    workbench.ingest("portfolio", fixtures / "gsc_baseline.json")
    return workbench.ingest("portfolio", fixtures / "gsc_portfolio_site_b.json")


def test_cross_site_overlap_is_evidence_not_inferred_ownership(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("portfolio", "Portfolio", {}, SITES)
        first = wb.ingest("portfolio", fixtures_dir / "gsc_baseline.json")
        assert set(first) == {"snapshot_id", "content_hash", "tool", "evidence", "findings", "recommendations"}
        assert wb.ownership_board("portfolio") == []
        second = wb.ingest("portfolio", fixtures_dir / "gsc_portfolio_site_b.json")
        assert set(second) == set(first)
        board = wb.ownership_board("portfolio")
        assert len(board) == 1
        conflict = board[0]["conflict"]
        assert conflict["key_type"] == "query"
        assert conflict["normalized_key"] == "evidence first seo"
        assert len(conflict["observed_sites"]) == 2
        assert len(conflict["observed_pages"]) == 3
        assert len(conflict["evidence_ids"]) == 3
        assert conflict["confidence"] == "high"
        assert "observed" in conflict["calculation"].lower()
        assert any("does not establish" in caveat for caveat in conflict["caveats"])
        assert board[0]["declared_assignments"] == []
        assert board[0]["resolution"] is None
        assert board[0]["state"] == "open"


def test_assignment_decision_plan_and_export_remain_non_executing(tmp_path, fixtures_dir):
    output = tmp_path / "export.json"
    with Workbench(tmp_path / "db") as wb:
        setup_portfolio(wb, fixtures_dir)
        conflict = wb.ownership_board("portfolio")[0]["conflict"]
        assignment = wb.record_ownership_assignment(
            "portfolio", "query", " Evidence   First SEO ", "https://example.test/evidence",
            "primary", "Primary product explanation", "Approved routing intent", created_at="2026-08-02T00:00:00Z",
        )
        assert assignment["source"] == "human_declared"
        assert assignment["active"] is True
        assert wb.ownership_board("portfolio")[0]["state"] == "open"
        pages = {item["url"] for item in conflict["observed_pages"]}
        roles = {
            "https://example.test/evidence": "product evidence",
            "https://example.test/guides/evidence": "implementation guide",
            "https://docs.example.test/evidence-guide": "reference documentation",
        }
        assert set(roles) == pages
        decision = wb.record_ownership_decision(
            "portfolio", conflict["id"], "differentiate", None, roles,
            "Distinct user intents are intentional.", decided_at="2026-08-03T00:00:00Z",
        )
        assert decision["external_execution_available"] is False
        assert decision["state"] == "recorded"
        assert decision["participant_pages"] == sorted(pages)
        assert decision["input_evidence_ids"] == conflict["evidence_ids"]
        assert decision["provenance"]["source"] == "local_operator_assertion"
        assert wb.ownership_board("portfolio")[0]["state"] == "differentiate"
        plan = wb.draft_ownership_plan(
            "portfolio", decision["id"], "Restore prior routing/content if separately applied changes regress.",
            created_at="2026-08-04T00:00:00Z",
        )
        assert plan["state"] == "draft"
        assert plan["external_execution_available"] is False
        assert all(step["external_write"] is False for step in plan["steps"])
        inspected = wb.inspect_ownership_conflict(conflict["id"])
        assert len(inspected["exact_inputs"]) == 3
        assert len(inspected["source_snapshots"]) == 2
        assert inspected["declared_assignments"][0]["id"] == assignment["id"]
        project_export = tmp_path / "project-export.json"
        wb.export_project("portfolio", project_export, "2026-09-09T00:00:00Z")
        project_payload = json.loads(project_export.read_text())
        assert not any(key.startswith("ownership_") for key in project_payload)
        assert project_payload["schema_version"] == 1
        project_schema = json.loads((fixtures_dir.parent.parent / "schemas" / "export-v1.schema.json").read_text())
        validate(project_payload, project_schema)
        wb.export_ownership("portfolio", output, "2026-09-09T00:00:00Z")
    exported = json.loads(output.read_text())
    assert len(exported["ownership_assignments"]) == 1
    assert len(exported["ownership_conflicts"]) == 1
    assert len(exported["ownership_decisions"]) == 1
    assert len(exported["ownership_action_plans"]) == 1
    assert exported["semantics"]["ownership"] == "observed visibility and human-declared ownership remain distinct"
    schema = json.loads((fixtures_dir.parent.parent / "schemas" / "ownership-v1.schema.json").read_text())
    validate(exported, schema)


def test_ownership_export_validator_enforces_provenance_and_write_gate(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "db") as wb:
        setup_portfolio(wb, fixtures_dir)
        conflict = wb.ownership_board("portfolio")[0]["conflict"]
        decision = wb.record_ownership_decision(
            "portfolio", conflict["id"], "accept", None, {}, "Recorded overlap."
        )
        wb.draft_ownership_plan("portfolio", decision["id"], "Restore independently applied changes.")
        payload = build_ownership_export(wb.store, "portfolio", "2026-09-09T00:00:00Z")

    original = copy.deepcopy(payload)
    payload["ownership_decisions"][0]["participant_pages"] = []
    with pytest.raises(ValueError, match="participant set"):
        validate_ownership_export(payload)
    payload = copy.deepcopy(original)
    payload["ownership_action_plans"][0]["steps"][0]["external_write"] = True
    with pytest.raises(ValueError, match="executable step"):
        validate_ownership_export(payload)
    payload = copy.deepcopy(original)
    payload["ownership_assignments"] = [{
        **payload["ownership_assignments"][0], "owner_site_id": "missing-site"
    }] if payload["ownership_assignments"] else [{
        "id": "assignment", "project_id": "portfolio", "key_type": "topic", "key_text": "Topic",
        "normalized_key": "topic", "owner_site_id": "missing-site", "owner_url": "https://example.test/",
        "role": "primary", "intent": "Intent", "rationale": "Rationale", "source": "human_declared",
        "basis_evidence_ids": [], "provenance": {"source": "human_declared"},
        "created_at": "2026-09-09T00:00:00Z", "supersedes_id": None,
    }]
    with pytest.raises(ValueError, match="assignment shape or references"):
        validate_ownership_export(payload)
    payload = copy.deepcopy(original)
    payload["evidence"][0]["snapshot_id"] = "missing-snapshot"
    with pytest.raises(ValueError, match="evidence snapshot"):
        validate_ownership_export(payload)
    payload = copy.deepcopy(original)
    payload["ownership_decisions"][0]["state"] = "invented"
    with pytest.raises(ValueError, match="state or actor"):
        validate_ownership_export(payload)
    payload = copy.deepcopy(original)
    payload["ownership_action_plans"][0]["dependencies"] = ["missing-plan"]
    with pytest.raises(ValueError, match="dependency references"):
        validate_ownership_export(payload)


def test_cli_runs_complete_ownership_operator_workflow(tmp_path, fixtures_dir):
    database = tmp_path / "cli.db"
    context = fixtures_dir / "business_context.json"
    parser = build_parser()

    def command(*args):
        return run(parser.parse_args(["--db", str(database), *args]))

    ready = command(
        "init", "--project", "portfolio", "--name", "Portfolio", "--business-context", str(context),
        "--site", SITES[0], "--site", SITES[1], "--at", "2026-08-01T00:00:00Z",
    )
    assert ready["external_execution_available"] is False
    command("ingest", "--project", "portfolio", "--file", str(fixtures_dir / "gsc_baseline.json"))
    command("ingest", "--project", "portfolio", "--file", str(fixtures_dir / "gsc_portfolio_site_b.json"))
    board = command("ownership-board", "--project", "portfolio")
    conflict = board[0]["conflict"]
    assignment = command(
        "ownership-assign", "--project", "portfolio", "--key-type", "query", "--key", conflict["key_text"],
        "--owner-url", "https://example.test/evidence", "--role", "primary", "--intent", "Product page",
        "--rationale", "Operator-declared routing", "--basis-evidence", conflict["evidence_ids"][0],
    )
    assert assignment["source"] == "human_declared"
    decision = command(
        "ownership-decide", "--project", "portfolio", "--conflict", conflict["id"], "--decision", "accept",
        "--note", "Operator accepts the current overlap.",
    )
    assert decision["state"] == "recorded" and decision["external_execution_available"] is False
    plan = command(
        "ownership-plan", "--project", "portfolio", "--decision", decision["id"],
        "--rollback-notes", "Restore independently applied content changes.",
    )
    assert plan["state"] == "draft" and all(step["external_write"] is False for step in plan["steps"])
    output = tmp_path / "ownership.json"
    receipt = command(
        "ownership-export", "--project", "portfolio", "--output", str(output),
        "--at", "2026-09-09T00:00:00Z",
    )
    assert receipt["format_version"] == "seomonster-workbench-ownership/v1"
    assert json.loads(output.read_text())["project"]["id"] == "portfolio"


def test_replay_is_deterministic_and_does_not_duplicate_conflicts(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "db") as wb:
        setup_portfolio(wb, fixtures_dir)
        first = wb.ownership_board("portfolio")[0]["conflict"]
        wb.ingest("portfolio", fixtures_dir / "gsc_portfolio_site_b.json")
        second = wb.ownership_board("portfolio")[0]["conflict"]
        assert first["id"] == second["id"]
        assert len(wb.store.rows("ownership_conflicts", "portfolio")) == 1


def test_stale_cross_site_signal_is_low_confidence_with_caveat(tmp_path, fixtures_dir):
    capture = json.loads((fixtures_dir / "gsc_portfolio_site_b.json").read_text())
    capture["freshness"] = "stale"
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("portfolio", "Portfolio", {}, SITES)
        wb.ingest("portfolio", fixtures_dir / "gsc_baseline.json")
        wb.ingest_capture("portfolio", capture)
        conflict = wb.ownership_board("portfolio")[0]["conflict"]
        assert conflict["confidence"] == "low"
        assert any("stale" in caveat for caveat in conflict["caveats"])


def test_incompatible_windows_and_same_site_overlap_do_not_create_cross_site_conflict(tmp_path, fixtures_dir):
    capture = json.loads((fixtures_dir / "gsc_portfolio_site_b.json").read_text())
    capture["arguments"]["start_date"] = "2026-06-01"
    capture["arguments"]["end_date"] = "2026-06-28"
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("portfolio", "Portfolio", {}, SITES)
        wb.ingest("portfolio", fixtures_dir / "gsc_baseline.json")
        assert wb.ownership_board("portfolio") == []


def test_resolved_response_windows_override_days_alias_for_comparability(tmp_path, fixtures_dir):
    first = json.loads((fixtures_dir / "gsc_baseline.json").read_text())
    second = json.loads((fixtures_dir / "gsc_portfolio_site_b.json").read_text())
    for capture, start, end in ((first, "2026-07-01", "2026-07-28"),
                                (second, "2026-08-01", "2026-08-28")):
        capture["arguments"] = {
            "site_url": capture["arguments"]["site_url"], "days": 28,
            "dimensions": ["query", "page"], "data_state": "final",
        }
        capture["result"]["data"].update({"start_date": start, "end_date": end,
                                             "dimensions": ["query", "page"], "data_state": "final"})
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("portfolio", "Portfolio", {}, SITES)
        wb.ingest_capture("portfolio", first)
        wb.ingest_capture("portfolio", second)
        assert wb.ownership_board("portfolio") == []


def test_newer_empty_snapshot_supersedes_old_visibility_without_becoming_zero(tmp_path, fixtures_dir):
    empty = json.loads((fixtures_dir / "gsc_portfolio_site_b.json").read_text())
    empty["captured_at"] = "2026-08-02T00:00:00Z"
    empty["result"]["data"]["rows"] = []
    empty["result"]["data"]["row_count"] = 0
    with Workbench(tmp_path / "db") as wb:
        setup_portfolio(wb, fixtures_dir)
        assert len(wb.ownership_board("portfolio")) == 1
        wb.ingest_capture("portfolio", empty)
        assert wb.ownership_board("portfolio") == []
        no_data = [wb.store.decode(row) for row in wb.store.rows("evidence", "portfolio")
                   if row["snapshot_id"] in {item["id"] for item in wb.store.rows("snapshots", "portfolio")}
                   and row["evidence_type"] == "no_data"]
        assert no_data and no_data[-1]["availability"] == "unavailable"
        assert no_data[-1]["value"].get("reason") == "empty_rows"


def test_url_prefix_empty_capture_retires_only_fully_covered_site_scopes(tmp_path, fixtures_dir):
    root = json.loads((fixtures_dir / "gsc_baseline.json").read_text())
    root["arguments"]["site_url"] = "https://example.test/"
    root["result"]["data"]["site_url"] = "https://example.test/"
    root["result"]["data"]["rows"] = [{
        "keys": ["evidence first seo", "https://example.test/blog/evidence"],
        "clicks": 4, "impressions": 100, "ctr": 0.04, "position": 16.0,
    }]
    other = json.loads((fixtures_dir / "gsc_portfolio_site_b.json").read_text())
    other["arguments"]["site_url"] = "https://other.test/"
    other["result"]["data"]["site_url"] = "https://other.test/"
    other["result"]["data"]["rows"] = [{
        "keys": ["evidence first seo", "https://other.test/evidence"],
        "clicks": 3, "impressions": 90, "ctr": 0.0333, "position": 19.0,
    }]
    narrow_empty = json.loads(json.dumps(root))
    narrow_empty["captured_at"] = "2026-08-02T00:00:00Z"
    narrow_empty["arguments"]["site_url"] = "https://example.test/docs"
    narrow_empty["result"]["data"].update({
        "site_url": "https://example.test/docs", "rows": [], "row_count": 0,
    })
    root_empty = json.loads(json.dumps(narrow_empty))
    root_empty["captured_at"] = "2026-08-03T00:00:00Z"
    root_empty["arguments"]["site_url"] = "https://example.test/"
    root_empty["result"]["data"]["site_url"] = "https://example.test/"

    with Workbench(tmp_path / "db") as wb:
        wb.create_project(
            "portfolio", "Portfolio", {},
            ["https://example.test", "https://example.test/docs", "https://other.test"],
        )
        wb.ingest_capture("portfolio", root)
        wb.ingest_capture("portfolio", other)
        assert len(wb.ownership_board("portfolio")) == 1
        wb.ingest_capture("portfolio", narrow_empty)
        assert len(wb.ownership_board("portfolio")) == 1
        wb.ingest_capture("portfolio", root_empty)
        assert wb.ownership_board("portfolio") == []


def test_normalization_merges_only_unicode_case_and_whitespace():
    assert normalize_key("  CAFÉ\tSEO  ") == normalize_key("cafe\u0301 seo")
    assert normalize_key("seo-tools") != normalize_key("seo tools")
    assert normalize_key("optimizer") != normalize_key("optimize")


def test_new_participant_reopens_exact_conflict_after_acceptance(tmp_path, fixtures_dir):
    third = json.loads((fixtures_dir / "gsc_portfolio_site_b.json").read_text())
    third["captured_at"] = "2026-08-01T00:10:00Z"
    third["arguments"]["site_url"] = "sc-domain:learn.example.test"
    third["result"]["data"]["site_url"] = "sc-domain:learn.example.test"
    third["result"]["data"]["rows"] = [{
        "keys": ["evidence first seo", "https://learn.example.test/evidence"],
        "clicks": 5, "impressions": 150, "ctr": 0.0333, "position": 18.0,
    }]
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("portfolio", "Portfolio", {}, [*SITES, "https://learn.example.test"])
        wb.ingest("portfolio", fixtures_dir / "gsc_baseline.json")
        wb.ingest("portfolio", fixtures_dir / "gsc_portfolio_site_b.json")
        old = wb.ownership_board("portfolio")[0]["conflict"]
        decision = wb.record_ownership_decision(
            "portfolio", old["id"], "accept", None, {}, "Intentional current overlap."
        )
        assert wb.ownership_board("portfolio")[0]["state"] == "accepted_overlap"
        wb.ingest_capture("portfolio", third)
        current = wb.ownership_board("portfolio")[0]
        assert current["conflict"]["id"] != old["id"]
        assert len(current["conflict"]["observed_pages"]) == 4
        assert current["state"] == "open" and current["resolution"] is None
        assert wb.store.one("ownership_decisions", decision["id"])
        assert wb.store.decode(wb.store.one("ownership_conflicts", old["id"]))["active"] is False
        with pytest.raises(ValueError, match="inactive"):
            wb.draft_ownership_plan("portfolio", decision["id"], "Do not act on stale participants.")


def test_missing_metrics_never_become_zero_and_partial_is_low_confidence(tmp_path, fixtures_dir):
    capture = json.loads((fixtures_dir / "gsc_portfolio_site_b.json").read_text())
    del capture["result"]["data"]["rows"][0]["impressions"]
    capture["provider"]["status"] = "degraded"
    capture["result"]["data"]["failed"] = [{"code": "PARTIAL", "message": "one page unavailable"}]
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("portfolio", "Portfolio", {}, SITES)
        wb.ingest("portfolio", fixtures_dir / "gsc_baseline.json")
        wb.ingest_capture("portfolio", capture)
        conflict = wb.ownership_board("portfolio")[0]["conflict"]
        assert conflict["impact"] == "unavailable"
        assert conflict["confidence"] == "low"
        page = next(item for item in conflict["observed_pages"] if item["site_url"] == "https://docs.example.test")
        assert page["impressions"] is None
        assert any("not zero" in caveat for caveat in conflict["caveats"])


def test_most_specific_site_scope_owns_declared_page(tmp_path):
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("p", "P", {}, ["https://example.test", "https://example.test/docs"])
        assignment = wb.record_ownership_assignment(
            "p", "topic", "API docs", "https://example.test/docs/api", "primary", "Reference", "Declared"
        )
        sites = {row["canonical_url"]: row["id"] for row in wb.store.rows("sites", "p")}
        assert assignment["owner_site_id"] == sites["https://example.test/docs"]


def test_assignment_scope_primary_supersession_and_decision_validation(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "db") as wb:
        setup_portfolio(wb, fixtures_dir)
        conflict = wb.ownership_board("portfolio")[0]["conflict"]
        first = wb.record_ownership_assignment(
            "portfolio", "query", "evidence first seo", "https://example.test/evidence", "primary", "Product", "Initial"
        )
        with pytest.raises(ValueError, match="supersede"):
            wb.record_ownership_assignment(
                "portfolio", "query", "evidence first seo", "https://docs.example.test/evidence-guide",
                "primary", "Docs", "Silent replacement"
            )
        replacement = wb.record_ownership_assignment(
            "portfolio", "query", "evidence first seo", "https://docs.example.test/evidence-guide",
            "primary", "Docs", "Explicit change", supersedes_id=first["id"]
        )
        assert replacement["active"] is True
        state = {item["id"]: item["active"] for item in wb.ownership_assignments("portfolio")}
        assert state == {first["id"]: False, replacement["id"]: True}
        with pytest.raises(ValueError, match="project site"):
            wb.record_ownership_assignment(
                "portfolio", "query", "evidence first seo", "https://outside.test/", "supporting", "Outside", "No"
            )
        with pytest.raises(ValueError, match="primary"):
            wb.record_ownership_decision("portfolio", conflict["id"], "route", None, {}, "Missing owner")
        with pytest.raises(ValueError, match="every observed page"):
            wb.record_ownership_decision(
                "portfolio", conflict["id"], "differentiate", None,
                {"https://example.test/evidence": "one"}, "Incomplete"
            )


def test_decisions_and_plans_require_explicit_current_supersession(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "db") as wb:
        setup_portfolio(wb, fixtures_dir)
        conflict = wb.ownership_board("portfolio")[0]["conflict"]
        first = wb.record_ownership_decision(
            "portfolio", conflict["id"], "accept", None, {}, "Accept current overlap."
        )
        with pytest.raises(ValueError, match="explicitly supersede"):
            wb.record_ownership_decision(
                "portfolio", conflict["id"], "consolidate", "https://example.test/evidence", {},
                "Replace without an explicit chain."
            )
        second = wb.record_ownership_decision(
            "portfolio", conflict["id"], "consolidate", "https://example.test/evidence", {},
            "Explicit replacement.", supersedes_id=first["id"],
        )
        assert wb.ownership_board("portfolio")[0]["resolution"]["id"] == second["id"]
        with pytest.raises(ValueError, match="sole current decision"):
            wb.draft_ownership_plan("portfolio", first["id"], "Do not plan a superseded decision.")
        assert wb.draft_ownership_plan(
            "portfolio", second["id"], "Restore independently applied consolidation."
        )["state"] == "draft"


def test_pending_decision_revisions_are_explicit(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "db") as wb:
        setup_portfolio(wb, fixtures_dir)
        conflict = wb.ownership_board("portfolio")[0]["conflict"]
        first = wb.draft_ownership_decision(
            "portfolio", conflict["id"], "accept", None, {}, "First draft."
        )
        with pytest.raises(ValueError, match="explicitly supersede"):
            wb.draft_ownership_decision(
                "portfolio", conflict["id"], "consolidate", "https://example.test/evidence", {}, "Second draft."
            )
        second = wb.draft_ownership_decision(
            "portfolio", conflict["id"], "consolidate", "https://example.test/evidence", {}, "Second draft.",
            supersedes_id=first["id"],
        )
        board = wb.ownership_board("portfolio")[0]
        assert board["state"] == "pending" and board["pending_decision"]["id"] == second["id"]
        assert board["resolution_candidates"] == []


def test_legacy_ambiguous_decisions_are_exposed_not_silently_selected(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "db") as wb:
        setup_portfolio(wb, fixtures_dir)
        conflict = wb.ownership_board("portfolio")[0]["conflict"]
        first = wb.record_ownership_decision(
            "portfolio", conflict["id"], "accept", None, {}, "First legacy branch."
        )
        duplicate = wb.store.one("ownership_decisions", first["id"])
        duplicate["id"] = "ownership_decision_legacy_parallel"
        duplicate["decision"] = "consolidate"
        duplicate["primary_url"] = "https://example.test/evidence"
        duplicate["note"] = "Parallel legacy branch."
        wb.store.insert("ownership_decisions", duplicate)
        board = wb.ownership_board("portfolio")[0]
        assert board["state"] == "ambiguous_resolution"
        assert board["resolution"] is None and board["pending_decision"] is None
        assert len(board["resolution_candidates"]) == 2


def test_v1_database_migrates_forward_without_changing_existing_rows(tmp_path):
    path = tmp_path / "v1.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    connection.executescript(MIGRATIONS[0])
    connection.execute("INSERT INTO schema_migrations VALUES (1, '2026-09-09T00:00:00Z')")
    connection.execute("INSERT INTO portfolios VALUES ('p', 'P', '2026-09-09T00:00:00Z')")
    connection.execute("INSERT INTO projects VALUES ('x', 'p', 'X', '2026-09-09T00:00:00Z', '{}', 'approved_source_material_no_automated_truth_score')")
    connection.commit()
    connection.close()
    with Workbench(path) as wb:
        assert wb.store.has_project("x")
        assert [row["version"] for row in wb.store.connection.execute("SELECT version FROM schema_migrations")] == [1, 2, 3]
        assert SCHEMA_VERSION == 3
