import json

from seomonster_workbench.engine import Workbench
from seomonster_workbench.evaluation import run_harness
from seomonster_workbench.policy import CapabilityPolicy


def make_project(workbench, fixtures_dir, project_id="project"):
    context = json.loads((fixtures_dir / "business_context.json").read_text())
    workbench.create_project(
        project_id,
        "Test project",
        context,
        ["https://example.test"],
        created_at="2026-08-01T00:00:00Z",
    )
    return context


def test_vertical_slice_is_replayable_and_provenance_complete(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "workbench.db") as workbench:
        make_project(workbench, fixtures_dir)
        first = workbench.ingest("project", fixtures_dir / "gsc_baseline.json")
        ids_before = [item["recommendation"]["id"] for item in workbench.board("project")]
        second = workbench.ingest("project", fixtures_dir / "gsc_baseline.json")
        ids_after = [item["recommendation"]["id"] for item in workbench.board("project")]

        assert first["snapshot_id"] == second["snapshot_id"]
        assert ids_before == ids_after
        assert first["findings"] == 3
        assert len(ids_before) == 3

        for item in workbench.board("project"):
            inspected = workbench.inspect_recommendation(item["recommendation"]["id"])
            provenance = inspected["recommendation"]["provenance"]
            assert provenance["input_evidence_ids"]
            assert provenance["timestamp"] == "2026-08-01T00:00:00Z"
            assert provenance["rule_id"]
            assert provenance["rule_version"]
            assert provenance["calculation"]
            assert "caveats" in provenance
            assert inspected["exact_inputs"]
            assert inspected["source_snapshots"][0]["raw"]["result"] == adapted_envelope(fixtures_dir)


def adapted_envelope(fixtures_dir):
    return json.loads((fixtures_dir / "gsc_baseline.json").read_text())["result"]


def test_unavailable_and_stale_signals_are_explicit_not_zero(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "workbench.db") as workbench:
        make_project(workbench, fixtures_dir)
        workbench.ingest("project", fixtures_dir / "provider_unavailable.json")
        workbench.ingest("project", fixtures_dir / "ga4_cold_start.json")
        workbench.ingest("project", fixtures_dir / "ai_citation_stale.json")
        evidence = [workbench.store.decode(row) for row in workbench.store.rows("evidence", "project")]

        unavailable = [row for row in evidence if row["availability"] == "unavailable"]
        assert len(unavailable) == 2
        assert all(row["value"] != 0 for row in unavailable)
        stale = next(row for row in evidence if row["evidence_type"] == "ai_citation_track_result")
        assert stale["provenance"]["freshness"] == "stale"
        assert any("stale" in caveat for caveat in stale["provenance"]["caveats"])


def test_partial_failure_reduces_confidence_and_preserves_failure(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "workbench.db") as workbench:
        make_project(workbench, fixtures_dir)
        workbench.ingest("project", fixtures_dir / "partial_failure.json")
        board = workbench.board("project")
        assert board[0]["finding"]["confidence"] == "low"
        evidence = [workbench.store.decode(row) for row in workbench.store.rows("evidence", "project")]
        assert any(row["evidence_type"] == "partial_provider_error" for row in evidence)


def test_plan_approval_and_execution_are_separate(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "workbench.db") as workbench:
        make_project(workbench, fixtures_dir)
        workbench.ingest("project", fixtures_dir / "gsc_baseline.json")
        recommendation = workbench.board("project")[0]["recommendation"]
        plan = workbench.draft_plan("project", recommendation["id"], created_at="2026-08-02T00:00:00Z")
        assert plan["state"] == "draft"
        assert plan["external_execution_available"] is False
        assert all(step["external_write"] is False for step in plan["steps"])

        approved = workbench.approve_plan(plan["id"], "Approved for later execution elsewhere", "2026-08-03T00:00:00Z")
        assert approved["state"] == "approved"
        assert approved["external_execution_available"] is False
        assert not workbench.policy.decide("approved", "external_write", "approved").allowed


def test_experiment_compares_outcome_without_causal_claim(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "workbench.db") as workbench:
        make_project(workbench, fixtures_dir)
        workbench.ingest("project", fixtures_dir / "gsc_baseline.json")
        item = next(
            item
            for item in workbench.board("project")
            if "https://example.test/evidence" in item["finding"]["affected_pages"]
            and item["finding"]["kind"] == "striking_distance"
        )
        inspection = workbench.inspect_recommendation(item["recommendation"]["id"])
        baseline = inspection["exact_inputs"][0]
        experiment = workbench.start_experiment(
            "project",
            item["recommendation"]["id"],
            baseline["id"],
            "Performance may improve after a separately approved change.",
            {"start": "2026-08-03", "end": "2026-08-31"},
            "2026-08-02T00:00:00Z",
        )
        workbench.ingest("project", fixtures_dir / "gsc_outcome.json")
        outcome = workbench.find_matching_outcome("project", baseline["id"])
        compared = workbench.compare_experiment(experiment["id"], outcome["id"])

        assert compared["comparison"]["deltas"]["clicks"] == 19
        assert compared["comparison"]["deltas"]["position"] == -1.5
        assert "causation is not established" in compared["comparison"]["interpretation"]
        assert compared["causal_claim"].startswith("none")


def test_export_preserves_approved_context_and_contains_no_private_paths(tmp_path, fixtures_dir):
    export_path = tmp_path / "export.json"
    with Workbench(tmp_path / "workbench.db") as workbench:
        context = make_project(workbench, fixtures_dir)
        workbench.ingest("project", fixtures_dir / "gsc_baseline.json")
        workbench.export_project("project", export_path, "2026-09-09T00:00:00Z")
    payload = json.loads(export_path.read_text())
    assert payload["project"]["business_context"] == context
    assert payload["project"]["claim_policy"] == "approved_source_material_no_automated_truth_score"
    assert '"truth_score":' not in export_path.read_text()
    assert "/Users/" not in export_path.read_text()
    assert payload["snapshots"][0]["source_locator"] == "gsc_baseline.json"


def test_safety_policy_denies_external_writes_in_all_local_modes():
    policy = CapabilityPolicy()
    for mode in ("evaluation", "preview", "recommendation"):
        assert not policy.decide(mode, "external_write", "pending").allowed
    assert not policy.decide("approved", "external_write", "approved").allowed


def test_offline_harness_covers_all_scenarios(fixtures_dir):
    report = run_harness(fixtures_dir)
    assert report["status"] == "passed", report
    assert len(report["scenarios"]) == 9
    assert all(report["safety"].values())


def test_migrations_are_idempotent(tmp_path, fixtures_dir):
    database = tmp_path / "workbench.db"
    with Workbench(database) as workbench:
        make_project(workbench, fixtures_dir)
    with Workbench(database) as reopened:
        assert reopened.store.has_project("project")
        versions = [row["version"] for row in reopened.store.connection.execute("SELECT version FROM schema_migrations")]
        assert versions == [1, 2, 3]
