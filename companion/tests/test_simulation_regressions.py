import json

import pytest

from seomonster_workbench.adapter import load_saved_result
from seomonster_workbench.engine import Workbench


def setup_project(wb, fixtures, project="a"):
    wb.create_project(project, project, {"claim": "Approved business claim"}, ["https://example.test"])
    wb.ingest(project, fixtures / "gsc_baseline.json")
    rec = wb.board(project)[0]["recommendation"]
    ev = wb.inspect_recommendation(rec["id"])["exact_inputs"][0]
    return rec, ev


def test_replay_preserves_human_decision(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "db") as wb:
        rec, _ = setup_project(wb, fixtures_dir)
        plan = wb.draft_plan("a", rec["id"])
        wb.approve_plan(plan["id"], "Human reviewed this")
        wb.ingest("a", fixtures_dir / "gsc_baseline.json")
        assert wb.inspect_recommendation(rec["id"])["recommendation"]["state"] == "approved"


def test_experiment_rejects_cross_project_recommendation(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "db") as wb:
        _, ev = setup_project(wb, fixtures_dir, "a")
        rec, _ = setup_project(wb, fixtures_dir, "b")
        with pytest.raises(ValueError, match="project"):
            wb.start_experiment("a", rec["id"], ev["id"], "Hypothesis", {"start": "2026-08-03", "end": "2026-08-31"})


def test_missing_freshness_is_unknown(tmp_path, fixtures_dir):
    bundle = json.loads((fixtures_dir / "gsc_baseline.json").read_text())
    del bundle["freshness"]
    path = tmp_path / "missing.json"
    path.write_text(json.dumps(bundle))
    assert load_saved_result(path, "a").freshness == "unknown"


def test_contradictory_tabular_data_is_not_observed(tmp_path, fixtures_dir):
    bundle = json.loads((fixtures_dir / "gsc_baseline.json").read_text())
    bundle["provider"]["status"] = "contradictory"
    path = tmp_path / "contradictory.json"
    path.write_text(json.dumps(bundle))
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("a", "a", {}, ["https://example.test"])
        wb.ingest("a", path)
        assert all(row["availability"] == "contradictory" for row in wb.store.rows("evidence", "a"))
        assert wb.board("a") == []


def test_project_reload_cannot_silently_replace_approved_context(tmp_path):
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("a", "A", {"claim": "Approved"}, ["https://example.test"])
        with pytest.raises(ValueError, match="context|exists"):
            wb.create_project("a", "A", {"claim": "Rewritten"}, ["https://example.test"])
        assert wb.store.decode(wb.store.rows("projects", "a")[0])["business_context"] == {"claim": "Approved"}


def test_experiment_cannot_compare_baseline_to_itself(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "db") as wb:
        rec, ev = setup_project(wb, fixtures_dir)
        experiment = wb.start_experiment(
            "a", rec["id"], ev["id"], "Hypothesis", {"start": "2026-08-03", "end": "2026-08-31"}
        )
        with pytest.raises(ValueError, match="later"):
            wb.compare_experiment(experiment["id"], ev["id"])


def test_generated_provenance_regression_rejected_before_commit(tmp_path, fixtures_dir, monkeypatch):
    import seomonster_workbench.engine as engine
    original = engine.analyze

    def broken(*args):
        findings, recs = original(*args)
        provenance = json.loads(recs[0]["provenance_json"])
        del provenance["calculation"]
        recs[0]["provenance_json"] = json.dumps(provenance)
        return findings, recs

    monkeypatch.setattr(engine, "analyze", broken)
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("a", "A", {}, ["https://example.test"])
        with pytest.raises(ValueError, match="provenance"):
            wb.ingest("a", fixtures_dir / "gsc_baseline.json")
        assert wb.store.rows("snapshots", "a") == []


def test_plan_revision_is_not_silently_discarded(tmp_path, fixtures_dir):
    with Workbench(tmp_path / "db") as wb:
        rec, _ = setup_project(wb, fixtures_dir)
        wb.draft_plan("a", rec["id"])
        with pytest.raises(ValueError, match="different"):
            wb.draft_plan("a", rec["id"], rollback_notes="New notes")


def test_failed_approval_rolls_back_every_record(tmp_path, fixtures_dir, monkeypatch):
    with Workbench(tmp_path / "db") as wb:
        rec, _ = setup_project(wb, fixtures_dir)
        plan = wb.draft_plan("a", rec["id"])
        original = wb.store.update

        def broken(table, *args):
            if table == "recommendations":
                raise ValueError("injected failure")
            return original(table, *args)

        monkeypatch.setattr(wb.store, "update", broken)
        with pytest.raises(ValueError, match="injected"):
            wb.approve_plan(plan["id"], "Human")
        assert wb.store.one("action_plans", plan["id"])["state"] == "draft"
        assert len(wb.store.rows("decisions", "a")) == 1


def test_export_does_not_overwrite_existing_file(tmp_path):
    path = tmp_path / "export.json"
    path.write_text("user-owned")
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("a", "A", {}, ["https://example.test"])
        with pytest.raises(FileExistsError):
            wb.export_project("a", path)
    assert path.read_text() == "user-owned"


@pytest.mark.parametrize("payload", [
    {"CLOUDFLARE_API_TOKEN": "sensitive"}, {"accessToken": "sensitive"},
    {"note": "Bearer sensitive-credential"}, {"url": "https://user:pass@example.test"},
])
def test_portable_secret_guards(payload):
    from seomonster_workbench.portable import validate_portable_export
    with pytest.raises(ValueError, match="secret"):
        validate_portable_export(payload)


def test_real_engine_gsc_and_ga4_shapes(tmp_path, fixtures_dir):
    bundle = json.loads((fixtures_dir / "gsc_baseline.json").read_text())
    bundle["tool"] = "gsc_compare_periods"
    bundle["result"]["data"] = {
        "dimensions": ["page"], "rows": [{"keys": ["https://example.test/guide"],
        "prior": {"clicks": 100}, "current": {"clicks": 50}, "delta_clicks": -50}]}
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("a", "A", {}, ["https://example.test"])
        wb.ingest_capture("a", bundle)
        assert wb.board("a")[0]["finding"]["kind"] == "content_decay_signal"
        bundle["tool"] = "ga4_run_report"
        bundle["result"]["data"] = {"dimensions": ["pagePath"], "metrics": ["sessions"],
                                   "rows": [{"dimensions": ["/guide"], "metrics": [42]}]}
        wb.ingest_capture("a", bundle)
        ga4 = [wb.store.decode(row) for row in wb.store.rows("evidence", "a")
               if row["evidence_type"] == "analytics_performance"][0]
        assert ga4["value"]["dimensions"] == {"pagePath": "/guide"}
        assert ga4["value"]["normalized_metrics"] == {"sessions": 42}


@pytest.mark.parametrize("target", ["arguments", "result", "page"])
def test_capture_cannot_contaminate_project_with_outside_site(tmp_path, fixtures_dir, target):
    bundle = json.loads((fixtures_dir / "gsc_baseline.json").read_text())
    if target == "arguments":
        bundle["arguments"]["site_url"] = "sc-domain:outside.test"
    elif target == "result":
        bundle["result"]["data"]["site_url"] = "sc-domain:outside.test"
    else:
        bundle["result"]["data"]["rows"][0]["keys"][1] = "https://outside.test/evidence"
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("a", "A", {}, ["https://example.test"])
        with pytest.raises(ValueError, match="scope|site|property"):
            wb.ingest_capture("a", bundle)
        assert wb.store.rows("snapshots", "a") == []


def test_export_checksum_matches_file_bytes(tmp_path):
    import hashlib
    path = tmp_path / "export.json"
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("a", "A", {}, [])
        result = wb.export_project("a", path)
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_matching_outcome_skips_newer_incompatible_measurement(tmp_path, fixtures_dir):
    bundle = json.loads((fixtures_dir / "gsc_outcome.json").read_text())
    with Workbench(tmp_path / "db") as wb:
        _, ev = setup_project(wb, fixtures_dir)
        wb.ingest_capture("a", bundle)
        valid = wb.find_matching_outcome("a", ev["id"])
        bundle["captured_at"] = "2026-09-02T00:00:00Z"
        bundle["arguments"]["search_type"] = "image"
        wb.ingest_capture("a", bundle)
        assert wb.find_matching_outcome("a", ev["id"])["id"] == valid["id"]


def test_apex_domain_property_accepts_configured_www_pages(tmp_path, fixtures_dir):
    bundle = json.loads((fixtures_dir / "gsc_baseline.json").read_text())
    for row in bundle["result"]["data"]["rows"]:
        row["keys"][1] = row["keys"][1].replace("https://example.test/", "https://www.example.test/")
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("www", "WWW", {}, ["https://www.example.test"])
        wb.ingest_capture("www", bundle)
        assert len(wb.board("www")) == 3


@pytest.mark.parametrize("outside", ["https://shop.example.test/evidence", "https://www.example.test.evil.test/evidence"])
def test_apex_domain_property_does_not_admit_unconfigured_hosts(tmp_path, fixtures_dir, outside):
    bundle = json.loads((fixtures_dir / "gsc_baseline.json").read_text())
    for row in bundle["result"]["data"]["rows"]:
        row["keys"][1] = row["keys"][1].replace("https://example.test/", "https://www.example.test/")
    bundle["result"]["data"]["rows"][0]["keys"][1] = outside
    with Workbench(tmp_path / "db") as wb:
        wb.create_project("www", "WWW", {}, ["https://www.example.test"])
        with pytest.raises(ValueError, match="page.*outside"):
            wb.ingest_capture("www", bundle)
        assert wb.store.rows("snapshots", "www") == []
