"""Read-only deterministic replay and analytical drift reporting."""

from __future__ import annotations

from typing import Any

from .adapter import adapt_saved_result
from .provenance import validate_derivation
from .rules import RULESET_VERSION, analyze, normalize
from .scope import validate_affected_pages, validate_capture_scope
from .store import Store
from .util import canonical_json, digest


COLLECTIONS = ("evidence", "findings", "recommendations")


def _analytical_projection(collection: str, record: dict[str, Any]) -> dict[str, Any]:
    projected = dict(record)
    if collection == "recommendations":
        # Human workflow state is not generated analytical output.
        projected.pop("state", None)
    return projected


def _collection_diff(
    collection: str, stored: list[dict[str, Any]], preview: list[dict[str, Any]],
) -> dict[str, Any]:
    stored_by_id = {item["id"]: _analytical_projection(collection, item) for item in stored}
    preview_by_id = {item["id"]: _analytical_projection(collection, item) for item in preview}
    shared = set(stored_by_id) & set(preview_by_id)
    return {
        "stored_count": len(stored_by_id),
        "preview_count": len(preview_by_id),
        "stored_digest": digest([stored_by_id[key] for key in sorted(stored_by_id)]),
        "preview_digest": digest([preview_by_id[key] for key in sorted(preview_by_id)]),
        "added": sorted(set(preview_by_id) - set(stored_by_id)),
        "removed": sorted(set(stored_by_id) - set(preview_by_id)),
        "changed": sorted(key for key in shared if canonical_json(stored_by_id[key]) != canonical_json(preview_by_id[key])),
    }


def _stored_records(store: Store, project_id: str, snapshot_id: str) -> dict[str, list[dict[str, Any]]]:
    evidence = [store.decode(row) for row in store.rows("evidence", project_id) if row["snapshot_id"] == snapshot_id]
    findings = [store.decode(row) for row in store.rows("findings", project_id) if row["snapshot_id"] == snapshot_id]
    finding_ids = {item["id"] for item in findings}
    recommendations = [
        store.decode(row) for row in store.rows("recommendations", project_id) if row["finding_id"] in finding_ids
    ]
    if any(item.get("state") not in {"recommended", "approved"} for item in recommendations):
        raise ValueError("stored recommendation workflow state is invalid")
    return {"evidence": evidence, "findings": findings, "recommendations": recommendations}


def _dependencies(store: Store, project_id: str, stored: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    evidence_ids = {item["id"] for item in stored["evidence"]}
    recommendation_ids = {item["id"] for item in stored["recommendations"]}
    all_plans = [store.decode(row) for row in store.rows("action_plans", project_id)]
    plan_ids = {item["id"] for item in all_plans if item["recommendation_id"] in recommendation_ids}
    changed = True
    while changed:
        changed = False
        for item in all_plans:
            if item["id"] not in plan_ids and set(item["dependencies"]) & plan_ids:
                plan_ids.add(item["id"])
                changed = True
    plans = sorted(plan_ids)
    affected_recommendations = recommendation_ids | {
        item["recommendation_id"] for item in all_plans if item["id"] in plan_ids
    }
    decisions = [
        row["id"] for row in store.rows("decisions", project_id)
        if row["recommendation_id"] in affected_recommendations
    ]
    experiments = []
    for row in store.rows("experiments", project_id):
        decoded = store.decode(row)
        if (decoded.get("recommendation_id") in affected_recommendations
                or decoded.get("baseline_evidence_id") in evidence_ids
                or decoded.get("outcome_evidence_id") in evidence_ids):
            experiments.append(decoded["id"])
    ownership_assignments = []
    for row in store.rows("ownership_assignments", project_id):
        decoded = store.decode(row)
        if set(decoded["basis_evidence_ids"]) & evidence_ids:
            ownership_assignments.append(decoded["id"])
    ownership_conflicts = []
    for row in store.rows("ownership_conflicts", project_id):
        decoded = store.decode(row)
        if set(decoded["evidence_ids"]) & evidence_ids:
            ownership_conflicts.append(decoded["id"])
    conflict_ids = set(ownership_conflicts)
    ownership_decisions = []
    for row in store.rows("ownership_decisions", project_id):
        decoded = store.decode(row)
        if decoded["conflict_id"] in conflict_ids or set(decoded["input_evidence_ids"]) & evidence_ids:
            ownership_decisions.append(decoded["id"])
    ownership_decision_ids = set(ownership_decisions)
    all_ownership_plans = [store.decode(row) for row in store.rows("ownership_action_plans", project_id)]
    ownership_plan_ids = {
        item["id"] for item in all_ownership_plans
        if item["conflict_id"] in conflict_ids or item["decision_id"] in ownership_decision_ids
    }
    changed = True
    while changed:
        changed = False
        for item in all_ownership_plans:
            if item["id"] not in ownership_plan_ids and set(item["dependencies"]) & ownership_plan_ids:
                ownership_plan_ids.add(item["id"])
                changed = True
    ownership_action_plans = sorted(ownership_plan_ids)
    deployment_observations = [
        row["id"] for row in store.rows("deployment_observations", project_id) if row["plan_id"] in plan_ids
    ]
    return {
        "decisions": sorted(decisions), "action_plans": sorted(plans), "experiments": sorted(experiments),
        "ownership_assignments": sorted(ownership_assignments),
        "ownership_conflicts": sorted(ownership_conflicts),
        "ownership_decisions": sorted(ownership_decisions),
        "ownership_action_plans": ownership_action_plans,
        "deployment_observations": sorted(deployment_observations),
    }


def _stored_versions(stored: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    versions: dict[str, set[str]] = {name: set() for name in COLLECTIONS}
    for item in stored["evidence"]:
        provenance = item.get("provenance")
        if not isinstance(provenance, dict) or not isinstance(provenance.get("transform_version"), str):
            raise ValueError("stored analytical provenance is invalid")
        versions["evidence"].add(provenance["transform_version"])
    for item in stored["findings"]:
        if not isinstance(item.get("rule_version"), str) or not item["rule_version"]:
            raise ValueError("stored analytical rule version is invalid")
        versions["findings"].add(item["rule_version"])
    for item in stored["recommendations"]:
        provenance = item.get("provenance")
        if not isinstance(provenance, dict) or not isinstance(provenance.get("rule_version"), str):
            raise ValueError("stored analytical provenance is invalid")
        versions["recommendations"].add(provenance["rule_version"])
    return {name: sorted(values) for name, values in versions.items()}


def preview_rule_replay(store: Store, project_id: str, snapshot_id: str) -> dict[str, Any]:
    """Re-run the installed rules over one stored raw snapshot without mutation."""
    row = store.one("snapshots", snapshot_id)
    if not row:
        raise ValueError("unknown stored snapshot")
    if row["project_id"] != project_id:
        raise ValueError("stored snapshot belongs to a different project")
    try:
        snapshot = store.decode(row)
        result = adapt_saved_result(snapshot["raw"], project_id)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("stored snapshot raw capture is invalid") from exc
    expected = {
        "id": result.snapshot_id, "tool": result.tool, "captured_at": result.captured_at,
        "provider": result.provider, "arguments": result.arguments, "envelope": result.envelope,
        "raw": result.raw, "content_hash": result.content_hash, "adapter_version": result.raw["adapter_version"],
        "freshness": result.freshness, "caveats": list(result.caveats),
    }
    if any(snapshot.get(key) != value for key, value in expected.items()):
        raise ValueError("stored snapshot does not match its raw capture")

    sites = [row["canonical_url"] for row in store.rows("sites", project_id)]
    validate_capture_scope(result, sites)
    generated_evidence = normalize(result)
    generated_findings, generated_recommendations = analyze(result, generated_evidence)
    validate_derivation(result, generated_evidence, generated_findings, generated_recommendations)
    validate_affected_pages(generated_findings, sites)
    preview = {
        "evidence": [store.decode(item) for item in generated_evidence],
        "findings": [store.decode(item) for item in generated_findings],
        "recommendations": [store.decode(item) for item in generated_recommendations],
    }
    stored = _stored_records(store, project_id, snapshot_id)
    collections = {name: _collection_diff(name, stored[name], preview[name]) for name in COLLECTIONS}
    state = "identical" if all(
        not report[key] for report in collections.values() for key in ("added", "removed", "changed")
    ) else "different"
    dependencies = _dependencies(store, project_id, stored)
    stored_versions = _stored_versions(stored)
    return {
        "project_id": project_id,
        "snapshot_id": snapshot_id,
        "state": state,
        "installed_rule_version": RULESET_VERSION,
        "stored_rule_versions": stored_versions,
        "collections": collections,
        "dependencies": {
            **dependencies,
            "review_required_if_migrating": state == "different",
        },
        "semantics": {
            "mode": "preview_only",
            "historical_ruleset_execution": "not_available",
            "mutation": "not_available",
            "approval": "not_changed",
            "external_execution": "not_available",
        },
        "external_execution_available": False,
    }
