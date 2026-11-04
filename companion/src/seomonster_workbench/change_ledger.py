"""Append-only local observations of changes executed outside the workbench."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .portable import validate_portable_export
from .store import SCHEMA_VERSION, Store
from .util import canonical_json, digest, stable_id, timestamp


OBSERVATION_FORMAT_VERSION = "seomonster-deployment-observation/v1"
CHANGE_FORMAT_VERSION = "seomonster-workbench-change-ledger/v1"
VERIFICATION_STATES = {"reported", "artifact_observed", "postcondition_verified"}
SOURCE_KINDS = {"operator_attestation", "deployment_receipt", "version_control"}
MAX_OPERATIONS = 100
MAX_OBSERVATION_BYTES = 1_000_000


def _record_projection(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in {"id", "record_hash"}}


def _nonempty(value: Any, label: str, limit: int = 2_000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"deployment observation {label} is invalid")
    return value


def _time(value: Any, label: str) -> str:
    value = _nonempty(value, label, 64)
    try:
        timestamp(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"deployment observation {label} is invalid") from exc
    return value


def validate_observation_input(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "format_version", "observed_at", "environment", "verification_state", "source",
        "summary", "operations", "rollback_reference", "caveats",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("deployment observation input contract is invalid")
    if value["format_version"] != OBSERVATION_FORMAT_VERSION:
        raise ValueError("deployment observation format version is unsupported")
    _time(value["observed_at"], "timestamp")
    _nonempty(value["environment"], "environment", 100)
    if value["verification_state"] not in VERIFICATION_STATES:
        raise ValueError("deployment observation verification state is invalid")
    source = value["source"]
    if not isinstance(source, dict) or set(source) != {"kind", "reference", "artifact_sha256"}:
        raise ValueError("deployment observation source is invalid")
    if source["kind"] not in SOURCE_KINDS:
        raise ValueError("deployment observation source kind is invalid")
    _nonempty(source["reference"], "source reference", 500)
    artifact = source["artifact_sha256"]
    if artifact is not None and (not isinstance(artifact, str) or len(artifact) != 64
                                 or any(char not in "0123456789abcdef" for char in artifact)):
        raise ValueError("deployment observation artifact digest is invalid")
    _nonempty(value["summary"], "summary")
    _nonempty(value["rollback_reference"], "rollback reference")
    operations = value["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
        raise ValueError("deployment observation operations are invalid")
    for operation in operations:
        if not isinstance(operation, dict) or set(operation) != {"kind", "target", "description"}:
            raise ValueError("deployment observation operation is invalid")
        for key, limit in (("kind", 100), ("target", 1_000), ("description", 2_000)):
            _nonempty(operation[key], f"operation {key}", limit)
    caveats = value["caveats"]
    if (not isinstance(caveats, list) or len(caveats) > 100
            or any(not isinstance(item, str) or not item.strip() or len(item) > 2_000 for item in caveats)):
        raise ValueError("deployment observation caveats are invalid")
    validate_portable_export(value)
    return value


def load_observation(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    with source.open("rb") as stream:
        raw = stream.read(MAX_OBSERVATION_BYTES + 1)
    if len(raw) > MAX_OBSERVATION_BYTES:
        raise ValueError("deployment observation exceeds size limit")
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"deployment observation contains a duplicate object key: {key}")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("deployment observation is not valid JSON") from exc
    return validate_observation_input(value)


def _approval_snapshot(store: Store, plan: dict[str, Any], observed_at: str) -> dict[str, Any]:
    decisions = [store.decode(row) for row in store.rows("decisions", plan["project_id"])]
    prior = sorted(
        item["id"] for item in decisions
        if item["recommendation_id"] == plan["recommendation_id"]
        and item["state"] == "approved" and timestamp(item["decided_at"]) <= timestamp(observed_at)
    )
    return {
        "state": "approval_timestamp_precedes_observation" if prior else "no_preceding_approval_timestamp",
        "approval_decision_ids": prior,
        "derived_at_import": True,
    }


def record_deployment_observation(
    store: Store, project_id: str, plan_id: str, value: dict[str, Any], recorded_at: str,
) -> dict[str, Any]:
    validate_observation_input(value)
    _time(recorded_at, "recorded timestamp")
    if timestamp(recorded_at) < timestamp(value["observed_at"]):
        raise ValueError("deployment observation cannot be recorded before it was observed")
    plan_row = store.one("action_plans", plan_id)
    if not plan_row or plan_row["project_id"] != project_id:
        raise ValueError("deployment observation plan is missing or belongs to another project")
    plan = store.decode(plan_row)
    approval = _approval_snapshot(store, plan, value["observed_at"])
    caveats = list(value["caveats"])
    observation_hash = digest(value)
    identifier = stable_id("deployment", project_id, plan_id, observation_hash)
    existing = store.one("deployment_observations", identifier)
    if existing:
        return inspect_deployment_observation(store, project_id, identifier)
    row = {
        "id": identifier,
        "project_id": project_id,
        "plan_id": plan_id,
        "observed_at": value["observed_at"],
        "recorded_at": recorded_at,
        "environment": value["environment"],
        "verification_state": value["verification_state"],
        "source_json": canonical_json(value["source"]),
        "summary": value["summary"],
        "operations_json": canonical_json(value["operations"]),
        "rollback_reference": value["rollback_reference"],
        "caveats_json": canonical_json(caveats),
        "plan_snapshot_json": canonical_json(plan),
        "plan_snapshot_hash": digest(plan),
        "approval_snapshot_json": canonical_json(approval),
        "approval_snapshot_hash": digest(approval),
        "observation_hash": observation_hash,
        "external_execution_available": 0,
    }
    row["record_hash"] = digest(_record_projection(store.decode(row)))
    store.insert("deployment_observations", row)
    return inspect_deployment_observation(store, project_id, identifier)


def inspect_deployment_observation(store: Store, project_id: str, identifier: str) -> dict[str, Any]:
    row = store.one("deployment_observations", identifier)
    if not row or row["project_id"] != project_id:
        raise ValueError("unknown deployment observation")
    payload = build_change_export(store, project_id, row["recorded_at"])
    return next(item for item in payload["deployment_observations"] if item["id"] == identifier)


def build_change_export(store: Store, project_id: str, exported_at: str) -> dict[str, Any]:
    projects = store.rows("projects", project_id)
    if not projects:
        raise ValueError("unknown project")
    observations = [store.decode(row) for row in store.rows("deployment_observations", project_id)]
    plan_ids = {item["plan_id"] for item in observations}
    all_plans = {row["id"]: store.decode(row) for row in store.rows("action_plans", project_id)}
    pending = list(plan_ids)
    while pending:
        identifier = pending.pop()
        plan = all_plans.get(identifier)
        if not plan:
            raise ValueError("deployment observation references a missing plan")
        for dependency in plan["dependencies"]:
            if dependency not in plan_ids:
                plan_ids.add(dependency)
                pending.append(dependency)
    plans = [all_plans[identifier] for identifier in sorted(plan_ids)]
    recommendation_ids = {item["recommendation_id"] for item in plans}
    decisions = [store.decode(row) for row in store.rows("decisions", project_id)
                 if row["recommendation_id"] in recommendation_ids]
    payload = {
        "format_version": CHANGE_FORMAT_VERSION,
        "storage_schema_version": SCHEMA_VERSION,
        "exported_at": exported_at,
        "project": store.decode(projects[0]),
        "action_plans": plans,
        "decisions": decisions,
        "deployment_observations": observations,
        "external_execution_available": False,
        "semantics": {
            "observation": "records evidence about changes performed outside the workbench; it does not execute them",
            "verification": "verification state is an operator assertion preserved with its source metadata",
            "approval": "alignment compares asserted decision timestamps with the observation timestamp; chronology is not attested",
            "causality": "a deployment observation does not establish an SEO outcome or causal effect",
        },
    }
    validate_change_export(payload)
    return payload


def validate_change_export(payload: dict[str, Any]) -> None:
    required = {
        "format_version", "storage_schema_version", "exported_at", "project", "action_plans", "decisions",
        "deployment_observations", "external_execution_available", "semantics",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("change ledger export contract is invalid")
    if (payload["format_version"] != CHANGE_FORMAT_VERSION or payload["storage_schema_version"] < 3
            or payload["external_execution_available"] is not False):
        raise ValueError("change ledger version or execution policy is invalid")
    _time(payload["exported_at"], "export timestamp")
    project = payload["project"]
    if (not isinstance(project, dict) or set(project) != {
            "id", "portfolio_id", "name", "created_at", "business_context", "claim_policy"
        } or not isinstance(project.get("id"), str)
            or project.get("claim_policy") != "approved_source_material_no_automated_truth_score"):
        raise ValueError("change ledger project is invalid")
    if set(payload["semantics"]) != {"observation", "verification", "approval", "causality"}:
        raise ValueError("change ledger semantics are invalid")
    for key in ("action_plans", "decisions", "deployment_observations"):
        if not isinstance(payload[key], list) or any(not isinstance(item, dict) for item in payload[key]):
            raise ValueError("change ledger collections are invalid")
    def index(items: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
        ids = [item.get("id") for item in items]
        if any(not isinstance(item, str) or not item for item in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"change ledger {label} identity is invalid")
        return dict(zip(ids, items))
    plans = index(payload["action_plans"], "plan")
    decisions = index(payload["decisions"], "decision")
    observations = index(payload["deployment_observations"], "observation")
    project_id = project["id"]
    if any(item.get("project_id") != project_id for item in [*plans.values(), *decisions.values(), *observations.values()]):
        raise ValueError("change ledger contains a cross-project record")
    plan_fields = {
        "id", "project_id", "recommendation_id", "state", "created_at", "dependencies", "steps",
        "rollback_notes", "external_execution_available",
    }
    def validate_plan(plan: dict[str, Any], label: str) -> None:
        if (set(plan) != plan_fields or plan.get("state") not in {"draft", "approved"}
                or plan.get("external_execution_available") is not False
                or not isinstance(plan.get("steps"), list) or not plan["steps"]
                or any(not isinstance(step, dict) or step.get("external_write") is not False for step in plan["steps"])):
            raise ValueError(f"change ledger {label} shape or execution policy is invalid")
        _time(plan.get("created_at"), f"{label} timestamp")
        _nonempty(plan.get("rollback_notes"), f"{label} rollback notes")
        step_ids = [step.get("id") for step in plan["steps"]]
        if (any(not isinstance(identifier, str) or not identifier for identifier in step_ids)
                or len(step_ids) != len(set(step_ids))):
            raise ValueError(f"change ledger {label} step identity is invalid")
        for step in plan["steps"]:
            if (set(step) != {"id", "kind", "mode", "description", "dependencies", "external_write"}
                    or step.get("mode") not in {"preview", "recommendation"}
                    or not isinstance(step.get("dependencies"), list)
                    or not set(step["dependencies"]) <= set(step_ids) or step["id"] in step["dependencies"]):
                raise ValueError(f"change ledger {label} step contract is invalid")
        dependencies = plan.get("dependencies")
        if (not isinstance(dependencies, list) or len(dependencies) != len(set(dependencies))
                or plan["id"] in dependencies or not set(dependencies) <= set(plans)):
            raise ValueError(f"change ledger {label} dependencies are invalid")
    for plan in plans.values():
        validate_plan(plan, "plan")
    decision_fields = {"id", "project_id", "recommendation_id", "state", "decided_at", "note"}
    for decision in decisions.values():
        if set(decision) != decision_fields or decision.get("state") not in {"pending", "approved"}:
            raise ValueError("change ledger decision shape is invalid")
        _time(decision.get("decided_at"), "decision timestamp")
    for item in observations.values():
        expected_fields = {
            "id", "project_id", "plan_id", "observed_at", "recorded_at", "environment", "verification_state",
            "source", "summary", "operations", "rollback_reference", "caveats", "plan_snapshot",
            "plan_snapshot_hash", "approval_snapshot", "approval_snapshot_hash", "observation_hash", "record_hash",
            "external_execution_available",
        }
        if set(item) != expected_fields or item.get("external_execution_available") is not False:
            raise ValueError("change ledger observation shape or execution policy is invalid")
        _time(item.get("observed_at"), "timestamp")
        _time(item.get("recorded_at"), "recorded timestamp")
        if timestamp(item["recorded_at"]) < timestamp(item["observed_at"]):
            raise ValueError("change ledger record predates its observation")
        if item.get("record_hash") != digest(_record_projection(item)):
            raise ValueError("change ledger record hash is invalid")
        if item.get("verification_state") not in VERIFICATION_STATES:
            raise ValueError("change ledger verification state is invalid")
        plan = plans.get(item.get("plan_id"))
        snapshot = item.get("plan_snapshot")
        if not plan or not isinstance(snapshot, dict) or snapshot.get("id") != item["plan_id"]:
            raise ValueError("change ledger plan reference is invalid")
        validate_plan(snapshot, "plan snapshot")
        immutable_fields = plan_fields - {"state"}
        if (any(snapshot.get(key) != plan.get(key) for key in immutable_fields)
                or (snapshot["state"], plan["state"]) not in {
                    ("draft", "draft"), ("draft", "approved"), ("approved", "approved")
                }):
            raise ValueError("change ledger plan snapshot disagrees with its current plan")
        if item.get("plan_snapshot_hash") != digest(snapshot):
            raise ValueError("change ledger plan snapshot hash is invalid")
        approval = item.get("approval_snapshot")
        if not isinstance(approval, dict) or set(approval) != {
            "state", "approval_decision_ids", "derived_at_import"
        } or approval["state"] not in {
            "approval_timestamp_precedes_observation", "no_preceding_approval_timestamp"
        }:
            raise ValueError("change ledger approval snapshot is invalid")
        if item.get("approval_snapshot_hash") != digest(approval):
            raise ValueError("change ledger approval snapshot hash is invalid")
        approval_ids = approval["approval_decision_ids"]
        if (not isinstance(approval_ids, list) or any(identifier not in decisions for identifier in approval_ids)
                or approval["derived_at_import"] is not True
                or (approval["state"] == "approval_timestamp_precedes_observation") != bool(approval_ids)):
            raise ValueError("change ledger approval references are invalid")
        for identifier in approval_ids:
            decision = decisions[identifier]
            if (decision.get("state") != "approved"
                    or decision.get("recommendation_id") != snapshot.get("recommendation_id")
                    or timestamp(decision.get("decided_at")) > timestamp(item["observed_at"])):
                raise ValueError("change ledger prior approval is invalid")
        source_input = {
            "format_version": OBSERVATION_FORMAT_VERSION,
            "observed_at": item["observed_at"], "environment": item["environment"],
            "verification_state": item["verification_state"], "source": item["source"],
            "summary": item["summary"], "operations": item["operations"],
            "rollback_reference": item["rollback_reference"], "caveats": item["caveats"],
        }
        validate_observation_input(source_input)
        if (item.get("observation_hash") != digest(source_input)
                or item.get("id") != stable_id("deployment", project_id, item["plan_id"], item["observation_hash"])):
            raise ValueError("change ledger observation hash is invalid")
    validate_portable_export(payload)
