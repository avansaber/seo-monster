"""Self-contained local backup, validation, preview, and transactional restore."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .portable import (
    FORMAT_VERSION as PROJECT_FORMAT_VERSION,
    build_export,
    build_ownership_export,
    validate_ownership_export,
    validate_portable_export,
)
from .change_ledger import build_change_export, validate_change_export
from .store import SCHEMA_VERSION, Store
from .util import canonical_json, digest, stable_id, timestamp


LEGACY_BACKUP_FORMAT_VERSION = "seomonster-workbench-backup/v1"
BACKUP_FORMAT_VERSION = "seomonster-workbench-backup/v2"
MAX_BACKUP_BYTES = 20_000_000
PROJECT_COLLECTIONS = (
    "sites", "snapshots", "evidence", "findings", "recommendations", "decisions", "action_plans", "experiments",
)
PROJECT_TABLES = PROJECT_COLLECTIONS[1:]
OWNERSHIP_TABLE_MAP = {
    "ownership_assignments": "ownership_assignments",
    "ownership_conflicts": "ownership_conflicts",
    "ownership_decisions": "ownership_decisions",
    "ownership_action_plans": "ownership_action_plans",
}
CHANGE_TABLE_MAP = {"deployment_observations": "deployment_observations"}
RESTORE_POLICY = {
    "identity": "preserve_project_and_record_ids",
    "existing_project": "exact_noop_or_conflict",
    "merge": "not_available",
    "external_execution": "not_available",
}
PROJECT_RECORD_FIELDS = {
    "sites": {"id", "portfolio_id", "canonical_url", "name"},
    "snapshots": {"id", "project_id", "tool", "captured_at", "provider", "arguments", "envelope", "raw",
                  "content_hash", "source_locator", "adapter_version", "freshness", "caveats"},
    "evidence": {"id", "project_id", "snapshot_id", "evidence_type", "subject", "observed_at", "value",
                 "availability", "provenance"},
    "findings": {"id", "project_id", "snapshot_id", "kind", "title", "affected_pages", "impact", "confidence",
                 "effort", "caveats", "evidence_ids", "rule_id", "rule_version", "calculation", "fingerprint"},
    "recommendations": {"id", "project_id", "finding_id", "title", "rationale", "state", "provenance"},
    "decisions": {"id", "project_id", "recommendation_id", "state", "decided_at", "note"},
    "action_plans": {"id", "project_id", "recommendation_id", "state", "created_at", "dependencies", "steps",
                     "rollback_notes", "external_execution_available"},
    "experiments": {"id", "project_id", "recommendation_id", "hypothesis", "state", "baseline_evidence_id",
                    "outcome_evidence_id", "observation_window", "comparison", "causal_claim", "created_at"},
}
OWNERSHIP_RECORD_FIELDS = {
    "ownership_assignments": {"id", "project_id", "key_type", "key_text", "normalized_key", "owner_site_id",
                              "owner_url", "role", "intent", "rationale", "source", "basis_evidence_ids",
                              "provenance", "created_at", "supersedes_id"},
    "ownership_conflicts": {"id", "project_id", "key_type", "key_text", "normalized_key", "evidence_ids",
                            "observed_pages", "observed_sites", "measurement_scope", "impact", "confidence",
                            "caveats", "rule_id", "rule_version", "calculation", "detected_at", "active"},
    "ownership_decisions": {"id", "project_id", "conflict_id", "decision", "state", "participant_pages",
                            "primary_url", "page_roles", "note", "decided_at", "actor_assertion",
                            "input_evidence_ids", "provenance", "supersedes_id", "external_execution_available"},
    "ownership_action_plans": {"id", "project_id", "conflict_id", "decision_id", "state", "created_at",
                               "dependencies", "steps", "rollback_notes", "external_execution_available"},
}


def _parse_time(value: Any, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{label} timestamp is invalid")
    try:
        timestamp(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{label} timestamp is invalid") from exc


def _index(records: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError(f"project export {label} must be a list of objects")
    identifiers = [item.get("id") for item in records]
    if any(not isinstance(item, str) or not item for item in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError(f"project export {label} identity is invalid")
    return dict(zip(identifiers, records))


def validate_project_export(payload: dict[str, Any]) -> None:
    """Validate the complete v1 graph; privacy validation alone is intentionally generic."""
    required = {
        "format_version", "schema_version", "exported_at", "project", *PROJECT_COLLECTIONS, "semantics",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("project export has an invalid top-level contract")
    if payload["format_version"] != PROJECT_FORMAT_VERSION or payload["schema_version"] != 1:
        raise ValueError("project export version is unsupported")
    _parse_time(payload["exported_at"], "project export")
    project = payload["project"]
    semantics = payload["semantics"]
    if not isinstance(project, dict) or not isinstance(semantics, dict):
        raise ValueError("project export project and semantics must be objects")
    project_fields = {"id", "portfolio_id", "name", "created_at", "business_context", "claim_policy"}
    if set(project) != project_fields or not all(
        isinstance(project.get(key), str) and project[key] for key in ("id", "portfolio_id", "name", "created_at")
    ):
        raise ValueError("project export project identity is invalid")
    _parse_time(project["created_at"], "project")
    if not isinstance(project["business_context"], dict) or project["claim_policy"] != (
        "approved_source_material_no_automated_truth_score"
    ):
        raise ValueError("project export approved context policy is invalid")
    semantic_keys = {"approved_business_context", "analytical_provenance", "causality", "execution"}
    if set(semantics) != semantic_keys or not all(isinstance(semantics[key], str) and semantics[key] for key in semantic_keys):
        raise ValueError("project export semantics are invalid")

    indexed = {name: _index(payload[name], name) for name in PROJECT_COLLECTIONS}
    for name, fields in PROJECT_RECORD_FIELDS.items():
        if any(set(item) != fields for item in indexed[name].values()):
            raise ValueError(f"project export {name} record shape is invalid")
    sites, snapshots, evidence = indexed["sites"], indexed["snapshots"], indexed["evidence"]
    findings, recommendations = indexed["findings"], indexed["recommendations"]
    decisions, plans, experiments = indexed["decisions"], indexed["action_plans"], indexed["experiments"]
    project_id, portfolio_id = project["id"], project["portfolio_id"]
    if any(site.get("portfolio_id") != portfolio_id for site in sites.values()):
        raise ValueError("project export site portfolio reference is invalid")
    if any(item.get("project_id") != project_id for name in PROJECT_TABLES for item in indexed[name].values()):
        raise ValueError("project export contains a cross-project record")

    for snapshot in snapshots.values():
        _parse_time(snapshot.get("captured_at"), "snapshot")
        raw = snapshot.get("raw")
        if (not isinstance(raw, dict) or snapshot.get("content_hash") != digest(raw)
                or snapshot.get("id") != stable_id("snap", project_id, snapshot["content_hash"])
                or snapshot.get("adapter_version") != raw.get("adapter_version")):
            raise ValueError("project export snapshot hash or identity is invalid")
        locator = snapshot.get("source_locator")
        if not isinstance(locator, str) or not locator or Path(locator).name != locator or "\\" in locator:
            raise ValueError("project export snapshot source locator is invalid")
    for item in evidence.values():
        if item.get("snapshot_id") not in snapshots or item.get("availability") not in {
            "observed", "unavailable", "contradictory",
        }:
            raise ValueError("project export evidence reference or availability is invalid")
        _parse_time(item.get("observed_at"), "evidence")
        provenance = item.get("provenance")
        snapshot = snapshots[item["snapshot_id"]]
        if (not isinstance(provenance, dict) or provenance.get("source_snapshot_id") != item["snapshot_id"]
                or provenance.get("captured_at") != snapshot["captured_at"]
                or provenance.get("arguments_hash") != digest(snapshot.get("arguments"))
                or provenance.get("envelope_hash") != digest(snapshot.get("envelope"))
                or not provenance.get("transform_version") or not isinstance(provenance.get("caveats"), list)):
            raise ValueError("project export evidence provenance is invalid")
    for finding in findings.values():
        evidence_ids = finding.get("evidence_ids")
        if (finding.get("snapshot_id") not in snapshots or not isinstance(evidence_ids, list) or not evidence_ids
                or len(set(evidence_ids)) != len(evidence_ids) or not set(evidence_ids) <= set(evidence)
                or not finding.get("rule_id") or not finding.get("rule_version") or not finding.get("calculation")):
            raise ValueError("project export finding reference or provenance is invalid")
    for recommendation in recommendations.values():
        finding = findings.get(recommendation.get("finding_id"))
        provenance = recommendation.get("provenance")
        if not finding:
            raise ValueError("project export recommendation reference is invalid")
        expected = {
            "input_evidence_ids": finding["evidence_ids"], "source_snapshot_id": finding["snapshot_id"],
            "rule_id": finding["rule_id"], "rule_version": finding["rule_version"],
            "calculation": finding["calculation"], "caveats": finding.get("caveats"),
        }
        if (recommendation.get("state") not in {"recommended", "approved"} or not isinstance(provenance, dict)
                or any(provenance.get(key) != value for key, value in expected.items())):
            raise ValueError("project export recommendation provenance is invalid")
        _parse_time(provenance.get("timestamp"), "recommendation provenance")
    for decision in decisions.values():
        if decision.get("recommendation_id") not in recommendations or decision.get("state") not in {
            "pending", "approved",
        }:
            raise ValueError("project export decision reference or state is invalid")
        _parse_time(decision.get("decided_at"), "decision")
    for plan in plans.values():
        dependencies = plan.get("dependencies")
        steps = plan.get("steps")
        if (plan.get("recommendation_id") not in recommendations or plan.get("state") not in {"draft", "approved"}
                or plan.get("external_execution_available") is not False
                or not isinstance(dependencies, list) or len(set(dependencies)) != len(dependencies)
                or not set(dependencies) <= set(plans) or plan["id"] in dependencies
                or not isinstance(steps, list) or not steps
                or any(not isinstance(step, dict) or step.get("external_write") is not False for step in steps)):
            raise ValueError("project export action-plan reference or execution policy is invalid")
        _parse_time(plan.get("created_at"), "action plan")
    for experiment in experiments.values():
        baseline_id, outcome_id = experiment.get("baseline_evidence_id"), experiment.get("outcome_evidence_id")
        state = experiment.get("state")
        if (experiment.get("recommendation_id") not in recommendations or baseline_id not in evidence
                or (outcome_id is not None and outcome_id not in evidence)
                or state not in {"baseline_recorded", "outcome_observed"}
                or (state == "baseline_recorded" and (outcome_id is not None or experiment.get("comparison") is not None))
                or (state == "outcome_observed" and (outcome_id is None or not isinstance(experiment.get("comparison"), dict)))
                or not isinstance(experiment.get("causal_claim"), str)
                or not experiment["causal_claim"].startswith("none")):
            raise ValueError("project export experiment reference, state, or causality is invalid")
        _parse_time(experiment.get("created_at"), "experiment")
    validate_portable_export(payload)


def build_backup(store: Store, project_id: str, exported_at: str) -> dict[str, Any]:
    project_export = build_export(store, project_id, exported_at)
    ownership_export = build_ownership_export(store, project_id, exported_at)
    change_ledger_export = build_change_export(store, project_id, exported_at)
    payload = {
        "format_version": BACKUP_FORMAT_VERSION,
        "storage_schema_version": SCHEMA_VERSION,
        "exported_at": exported_at,
        "portfolio": store.portfolio_for_project(project_id),
        "project_export": project_export,
        "ownership_export": ownership_export,
        "change_ledger_export": change_ledger_export,
        "component_hashes": {
            "project_export_canonical_sha256": digest(project_export),
            "ownership_export_canonical_sha256": digest(ownership_export),
            "change_ledger_export_canonical_sha256": digest(change_ledger_export),
        },
        "restore_policy": dict(RESTORE_POLICY),
        "external_execution_available": False,
    }
    validate_backup(payload)
    return payload


def validate_backup(payload: dict[str, Any]) -> None:
    try:
        if len(canonical_json(payload).encode("utf-8")) > MAX_BACKUP_BYTES:
            raise ValueError("backup exceeds size limit")
    except (TypeError, RecursionError) as exc:
        raise ValueError("backup must be bounded finite JSON") from exc
    legacy_required = {
        "format_version", "storage_schema_version", "exported_at", "portfolio", "project_export",
        "ownership_export", "component_hashes", "restore_policy", "external_execution_available",
    }
    current_required = legacy_required | {"change_ledger_export"}
    if not isinstance(payload, dict):
        raise ValueError("backup has an invalid top-level contract")
    legacy = payload.get("format_version") == LEGACY_BACKUP_FORMAT_VERSION
    expected_fields = legacy_required if legacy else current_required
    if set(payload) != expected_fields:
        raise ValueError("backup has an invalid top-level contract")
    expected_schema = 2 if legacy else SCHEMA_VERSION
    if (payload["format_version"] not in {LEGACY_BACKUP_FORMAT_VERSION, BACKUP_FORMAT_VERSION}
            or payload["storage_schema_version"] != expected_schema
            or payload["external_execution_available"] is not False):
        raise ValueError("backup version, storage schema, or execution policy is unsupported")
    _parse_time(payload["exported_at"], "backup")
    validate_project_export(payload["project_export"])
    validate_ownership_export(payload["ownership_export"])
    if not legacy:
        validate_change_export(payload["change_ledger_export"])
    project = payload["project_export"]["project"]
    ownership = payload["ownership_export"]
    change = payload.get("change_ledger_export")
    for name, fields in OWNERSHIP_RECORD_FIELDS.items():
        if any(set(item) != fields for item in ownership[name]):
            raise ValueError(f"backup {name} record shape is invalid")
    portfolio = payload["portfolio"]
    if (not isinstance(portfolio, dict) or set(portfolio) != {"id", "name", "created_at"}
            or portfolio.get("id") != project["portfolio_id"]
            or not isinstance(portfolio.get("name"), str) or not portfolio["name"]):
        raise ValueError("backup portfolio identity is invalid")
    _parse_time(portfolio.get("created_at"), "portfolio")
    if ownership["project"] != project or ownership["sites"] != payload["project_export"]["sites"]:
        raise ValueError("backup project and ownership components disagree")
    if change is not None and change["project"] != project:
        raise ValueError("backup project and change-ledger components disagree")
    if change is not None:
        project_plans = {item["id"]: item for item in payload["project_export"]["action_plans"]}
        project_decisions = {item["id"]: item for item in payload["project_export"]["decisions"]}
        if any(project_plans.get(item["id"]) != item for item in change["action_plans"]):
            raise ValueError("backup change-ledger plan disagrees with project component")
        if any(project_decisions.get(item["id"]) != item for item in change["decisions"]):
            raise ValueError("backup change-ledger decision disagrees with project component")
        change_recommendations = {item["recommendation_id"] for item in change["action_plans"]}
        expected_decisions = {
            identifier for identifier, item in project_decisions.items()
            if item["recommendation_id"] in change_recommendations
        }
        if {item["id"] for item in change["decisions"]} != expected_decisions:
            raise ValueError("backup change-ledger decision history is incomplete")
    project_snapshots = {item["id"]: item for item in payload["project_export"]["snapshots"]}
    project_evidence = {item["id"]: item for item in payload["project_export"]["evidence"]}
    if any(project_snapshots.get(item["id"]) != item for item in ownership["snapshots"]):
        raise ValueError("backup ownership snapshot disagrees with project evidence")
    if any(project_evidence.get(item["id"]) != item for item in ownership["evidence"]):
        raise ValueError("backup ownership evidence disagrees with project evidence")
    hashes = payload["component_hashes"]
    expected_hashes = {
        "project_export_canonical_sha256": digest(payload["project_export"]),
        "ownership_export_canonical_sha256": digest(ownership),
    }
    if change is not None:
        expected_hashes["change_ledger_export_canonical_sha256"] = digest(change)
    if hashes != expected_hashes:
        raise ValueError("backup component hash is invalid")
    if payload["restore_policy"] != RESTORE_POLICY:
        raise ValueError("backup restore policy is unsupported")
    validate_portable_export(payload)


def load_backup(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with source.open("rb") as stream:
        raw = stream.read(MAX_BACKUP_BYTES + 1)
    if len(raw) > MAX_BACKUP_BYTES:
        raise ValueError("backup exceeds size limit")
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"backup contains a duplicate object key: {key}")
            result[key] = value
        return result

    try:
        payload = json.loads(raw, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("backup is not valid JSON") from exc
    validate_backup(payload)
    return payload


def _restore_records(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    project = payload["project_export"]
    ownership = payload["ownership_export"]
    records = {
        "portfolios": [payload["portfolio"]], "sites": project["sites"], "projects": [project["project"]],
        **{table: project[table] for table in PROJECT_TABLES},
        **{table: ownership[key] for key, table in OWNERSHIP_TABLE_MAP.items()},
        **({table: payload["change_ledger_export"][key] for key, table in CHANGE_TABLE_MAP.items()}
           if "change_ledger_export" in payload else {table: [] for table in CHANGE_TABLE_MAP.values()}),
    }
    return records


def preview_restore(store: Store, payload: dict[str, Any], expected_project_id: str | None = None) -> dict[str, Any]:
    validate_backup(payload)
    project_id = payload["project_export"]["project"]["id"]
    if expected_project_id is not None and project_id != expected_project_id:
        raise ValueError("backup project does not match the configured project")
    project_exists = store.has_project(project_id)
    records = _restore_records(payload)
    create_counts: dict[str, int] = {}
    unchanged_counts: dict[str, int] = {}
    conflicts: list[dict[str, str]] = []
    for table, items in records.items():
        created = unchanged = 0
        for item in items:
            current = store.record_by_id(table, item["id"])
            if current is None:
                created += 1
                if project_exists:
                    conflicts.append({"table": table, "id": item["id"], "reason": "missing_from_existing_project"})
            elif store.decode(current) == item:
                unchanged += 1
            else:
                conflicts.append({"table": table, "id": item["id"], "reason": "different_existing_record"})
        create_counts[table], unchanged_counts[table] = created, unchanged
    expected_sites = {item["id"] for item in payload["project_export"]["sites"]}
    current_sites = {item["site_id"] for item in store.rows("project_sites", project_id)} if project_exists else set()
    if project_exists and current_sites != expected_sites:
        conflicts.append({"table": "project_sites", "id": project_id, "reason": "different_existing_scope"})
    if project_exists:
        for table in (*PROJECT_TABLES, *OWNERSHIP_TABLE_MAP.values(), *CHANGE_TABLE_MAP.values()):
            expected = {item["id"] for item in records[table]}
            current = {item["id"] for item in store.rows(table, project_id)}
            for extra in sorted(current - expected):
                conflicts.append({"table": table, "id": extra, "reason": "extra_existing_record"})
    conflicts.sort(key=lambda item: (item["table"], item["id"], item["reason"]))
    state = "conflict" if conflicts else "noop" if project_exists else "create"
    return {
        "project_id": project_id, "state": state, "applicable": not conflicts,
        "create_counts": create_counts, "unchanged_counts": unchanged_counts, "conflicts": conflicts,
        "restore_policy": dict(RESTORE_POLICY), "external_execution_available": False,
    }


def apply_restore(store: Store, payload: dict[str, Any], expected_project_id: str | None = None) -> dict[str, Any]:
    report = preview_restore(store, payload, expected_project_id)
    if not report["applicable"]:
        raise ValueError("backup conflicts with existing local records; restore was not applied")
    if report["state"] == "noop":
        return report
    records = _restore_records(payload)
    project_id = report["project_id"]
    with store.transaction():
        for table in (
            "portfolios", "sites", "projects", "snapshots", "evidence", "findings", "recommendations",
            "decisions", "action_plans", "experiments", "ownership_assignments", "ownership_conflicts",
            "ownership_decisions", "ownership_action_plans", "deployment_observations",
        ):
            store.restore_records(table, records[table])
        store.restore_project_sites(project_id, [item["id"] for item in payload["project_export"]["sites"]])
        result = preview_restore(store, payload, expected_project_id)
        if result["state"] != "noop":
            raise ValueError("backup restore verification failed")
    return {**result, "state": "restored"}
