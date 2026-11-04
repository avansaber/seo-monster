"""Offline fixture harness for provenance, uncertainty, replay, and safety."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .engine import Workbench
from .policy import CapabilityPolicy


def run_harness(fixtures_dir: str | Path) -> dict[str, Any]:
    fixtures = Path(fixtures_dir)
    manifest = json.loads((fixtures / "scenario_manifest.json").read_text(encoding="utf-8"))
    scenario_results: list[dict[str, Any]] = []
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="seomonster-workbench-eval-") as temp_dir:
        for index, scenario in enumerate(manifest["scenarios"]):
            database = Path(temp_dir) / f"scenario-{index}.db"
            project_id = f"eval-{index}"
            with Workbench(database) as workbench:
                workbench.create_project(
                    project_id,
                    scenario["name"],
                    {"approved_claims": ["Fixture business context is approved source material."]},
                    ["https://example.test"],
                    created_at="2026-09-09T00:00:00Z",
                )
                first = workbench.ingest(project_id, fixtures / scenario["fixture"])
                before_ids = [entry["recommendation"]["id"] for entry in workbench.board(project_id)]
                second = workbench.ingest(project_id, fixtures / scenario["fixture"])
                after_ids = [entry["recommendation"]["id"] for entry in workbench.board(project_id)]
                checks = {
                    "minimum_evidence": first["evidence"] >= scenario["expect"].get("minimum_evidence", 0),
                    "minimum_findings": first["findings"] >= scenario["expect"].get("minimum_findings", 0),
                    "deterministic_replay": first["snapshot_id"] == second["snapshot_id"] and before_ids == after_ids,
                }
                evidence_rows = [workbench.store.decode(row) for row in workbench.store.rows("evidence", project_id)]
                required_availability = scenario["expect"].get("availability")
                if required_availability:
                    checks["availability"] = required_availability in {row["availability"] for row in evidence_rows}
                if scenario["expect"].get("untrusted_content"):
                    checks["untrusted_content_inert"] = any(
                        "inert data" in caveat
                        for row in evidence_rows
                        for caveat in row["provenance"].get("caveats", [])
                    )
                for board_item in workbench.board(project_id):
                    provenance = board_item["recommendation"]["provenance"]
                    complete = all(
                        provenance.get(key) not in (None, "", [])
                        for key in ("input_evidence_ids", "timestamp", "rule_id", "rule_version", "calculation")
                    ) and "caveats" in provenance
                    checks.setdefault("recommendation_provenance", True)
                    checks["recommendation_provenance"] = checks["recommendation_provenance"] and complete
                if not workbench.board(project_id):
                    checks["recommendation_provenance"] = True
                passed = all(checks.values())
                if not passed:
                    failures.append(scenario["name"])
                scenario_results.append({"scenario": scenario["name"], "passed": passed, "checks": checks})

        policy = CapabilityPolicy()
        safety_checks = {
            mode: not policy.decide(mode, "external_write", "pending").allowed
            for mode in ("evaluation", "preview", "recommendation")
        }
        safety_checks["approved_without_executor"] = not policy.decide("approved", "external_write", "approved").allowed
        if not all(safety_checks.values()):
            failures.append("write-gating")

    return {
        "harness_version": "1",
        "fixture_manifest_version": manifest["version"],
        "status": "passed" if not failures else "failed",
        "scenarios": scenario_results,
        "safety": safety_checks,
        "failures": failures,
    }
