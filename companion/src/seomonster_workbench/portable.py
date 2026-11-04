"""Portable export builder and privacy guard."""

from __future__ import annotations

from typing import Any
import re

from .store import SCHEMA_VERSION, Store
from .util import timestamp as parse_timestamp


FORMAT_VERSION = "seomonster-workbench-export/v1"
EXPORT_SCHEMA_VERSION = 1
OWNERSHIP_FORMAT_VERSION = "seomonster-workbench-ownership/v1"
SECRET_KEYS = {"api_key", "token", "password", "credentials", "authorization", "cookie"}
PRIVATE_PATH_MARKERS = ("/users/", "\\users\\", "/home/", "/private/", "/var/", "file://")
SECRET_PATTERNS = (r"\bBearer\s+\S+", r"\bsk-[A-Za-z0-9_-]{16,}", r"https?://[^\s/@]+:[^\s/@]+@")


def build_export(store: Store, project_id: str, exported_at: str) -> dict[str, Any]:
    projects = store.rows("projects", project_id)
    if not projects:
        raise ValueError(f"unknown project: {project_id}")
    tables = [
        "sites",
        "snapshots",
        "evidence",
        "findings",
        "recommendations",
        "decisions",
        "action_plans",
        "experiments",
    ]
    return {
        "format_version": FORMAT_VERSION,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": exported_at,
        "project": store.decode(projects[0]),
        **{table: [store.decode(row) for row in store.rows(table, project_id)] for table in tables},
        "semantics": {
            "approved_business_context": "preserved source material; no automated truth score",
            "analytical_provenance": "applies only to workbench observations, calculations, and recommendations",
            "causality": "experiment comparisons are observational unless an external design establishes causation",
            "execution": "not available in format v1",
        },
    }


def validate_portable_export(payload: dict[str, Any]) -> None:
    violations: list[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower() == "truth_score":
                    violations.append(f"automated truth score at {path}.{key}")
                normalized = re.sub(r"(?<!^)(?=[A-Z][a-z])", "_", key).lower().replace("-", "_")
                secret_key = normalized in SECRET_KEYS or any(
                    normalized.endswith("_" + suffix) for suffix in SECRET_KEYS | {"secret", "private_key"}
                ) or normalized in {"secret", "private_key", "access_token", "refresh_token"}
                if secret_key and child not in (None, "", [], {}):
                    violations.append(f"secret-like field at {path}.{key}")
                walk(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str):
            if any(marker in value.lower() for marker in PRIVATE_PATH_MARKERS):
                violations.append(f"private path at {path}")
            if any(re.search(pattern, value, re.IGNORECASE) for pattern in SECRET_PATTERNS):
                violations.append(f"secret-like value at {path}")

    walk(payload, "$")
    if violations:
        raise ValueError("portable export rejected: " + "; ".join(violations[:5]))


def build_ownership_export(store: Store, project_id: str, exported_at: str) -> dict[str, Any]:
    projects = store.rows("projects", project_id)
    if not projects:
        raise ValueError("unknown project")
    assignments = [store.decode(row) for row in store.rows("ownership_assignments", project_id)]
    conflicts = [store.decode(row) for row in store.rows("ownership_conflicts", project_id)]
    decisions = [store.decode(row) for row in store.rows("ownership_decisions", project_id)]
    plans = [store.decode(row) for row in store.rows("ownership_action_plans", project_id)]
    evidence_ids = {item for conflict in conflicts for item in conflict["evidence_ids"]}
    evidence_ids.update(item for assignment in assignments for item in assignment["basis_evidence_ids"])
    evidence_by_id = {row["id"]: store.decode(row) for row in store.rows("evidence", project_id)}
    missing = evidence_ids - set(evidence_by_id)
    if missing:
        raise ValueError("ownership export references missing evidence")
    evidence = [evidence_by_id[item] for item in sorted(evidence_ids)]
    snapshot_ids = {item["snapshot_id"] for item in evidence}
    snapshot_by_id = {row["id"]: store.decode(row) for row in store.rows("snapshots", project_id)}
    if snapshot_ids - set(snapshot_by_id):
        raise ValueError("ownership export references missing snapshots")
    payload = {
        "format_version": OWNERSHIP_FORMAT_VERSION,
        "storage_schema_version": SCHEMA_VERSION,
        "exported_at": exported_at,
        "project": store.decode(projects[0]),
        "sites": [store.decode(row) for row in store.rows("sites", project_id)],
        "ownership_assignments": assignments,
        "ownership_conflicts": conflicts,
        "ownership_decisions": decisions,
        "ownership_action_plans": plans,
        "evidence": evidence,
        "snapshots": [snapshot_by_id[item] for item in sorted(snapshot_ids)],
        "external_execution_available": False,
        "semantics": {
            "ownership": "observed visibility and human-declared ownership remain distinct",
            "resolution": "accept means accepted overlap; decisions cover one exact participant/evidence set",
            "execution": "not available; plans are local drafts",
            "topics": "declared only; no similarity, synonym, stemming, or intent inference",
        },
    }
    validate_ownership_export(payload)
    return payload


def validate_ownership_export(payload: dict[str, Any]) -> None:
    required = {
        "format_version", "storage_schema_version", "exported_at", "project", "sites",
        "ownership_assignments", "ownership_conflicts", "ownership_decisions", "ownership_action_plans",
        "evidence", "snapshots", "external_execution_available", "semantics",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("ownership export has an invalid top-level contract")
    if payload["format_version"] != OWNERSHIP_FORMAT_VERSION or payload["external_execution_available"] is not False:
        raise ValueError("ownership export version or execution policy is invalid")
    if not isinstance(payload["storage_schema_version"], int) or payload["storage_schema_version"] < 2:
        raise ValueError("ownership export storage schema is invalid")
    exported_at = payload["exported_at"]
    if not isinstance(exported_at, str):
        raise ValueError("ownership export timestamp is invalid")
    try:
        parse_timestamp(exported_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("ownership export timestamp is invalid") from exc
    for key in required - {"format_version", "storage_schema_version", "exported_at",
                           "external_execution_available", "project", "semantics"}:
        if not isinstance(payload[key], list):
            raise ValueError("ownership export collections must be lists")
    if not isinstance(payload["project"], dict) or not isinstance(payload["semantics"], dict):
        raise ValueError("ownership export project and semantics must be objects")
    semantic_keys = {"ownership", "resolution", "execution", "topics"}
    if set(payload["semantics"]) != semantic_keys or not all(
        isinstance(payload["semantics"][key], str) and payload["semantics"][key]
        for key in semantic_keys
    ):
        raise ValueError("ownership export semantics are invalid")

    project_id = payload["project"].get("id")
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("ownership export project identity is invalid")
    collections = {
        key: value for key, value in payload.items()
        if key in required - {"format_version", "storage_schema_version", "exported_at",
                              "external_execution_available", "project", "semantics"}
    }
    if any(not isinstance(item, dict) for values in collections.values() for item in values):
        raise ValueError("ownership export records must be objects")
    if any(item.get("project_id") != project_id for key, values in collections.items()
           if key not in {"sites"} for item in values):
        raise ValueError("ownership export contains a cross-project record")

    def index(records: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
        identifiers = [item.get("id") for item in records]
        if any(not isinstance(item, str) or not item for item in identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError(f"ownership export {label} identity is invalid")
        return dict(zip(identifiers, records))

    sites = index(payload["sites"], "site")
    snapshots = index(payload["snapshots"], "snapshot")
    evidence = index(payload["evidence"], "evidence")
    assignments = index(payload["ownership_assignments"], "assignment")
    conflicts = index(payload["ownership_conflicts"], "conflict")
    decisions = index(payload["ownership_decisions"], "decision")
    plans = index(payload["ownership_action_plans"], "action-plan")
    portfolio_id = payload["project"].get("portfolio_id")
    if any(site.get("portfolio_id") != portfolio_id for site in sites.values()):
        raise ValueError("ownership export site is outside the project portfolio")
    for snapshot in snapshots.values():
        if not isinstance(snapshot.get("captured_at"), str):
            raise ValueError("ownership export snapshot timestamp is invalid")
        parse_timestamp(snapshot["captured_at"])
    for item in evidence.values():
        if item.get("snapshot_id") not in snapshots or item.get("availability") not in {
            "observed", "unavailable", "contradictory"
        }:
            raise ValueError("ownership evidence snapshot or availability is invalid")
        if not isinstance(item.get("observed_at"), str):
            raise ValueError("ownership evidence timestamp is invalid")
        parse_timestamp(item["observed_at"])
    for assignment in assignments.values():
        if (assignment.get("key_type") not in {"query", "topic"}
                or assignment.get("role") not in {"primary", "supporting", "intent_variant", "accepted_overlap"}
                or assignment.get("source") != "human_declared"
                or assignment.get("owner_site_id") not in sites
                or not isinstance(assignment.get("basis_evidence_ids"), list)
                or not set(assignment["basis_evidence_ids"]) <= set(evidence)):
            raise ValueError("ownership assignment shape or references are invalid")
        supersedes_id = assignment.get("supersedes_id")
        if supersedes_id is not None:
            previous = assignments.get(supersedes_id)
            if not previous or (previous.get("key_type"), previous.get("normalized_key")) != (
                assignment.get("key_type"), assignment.get("normalized_key")
            ):
                raise ValueError("ownership assignment supersession is invalid")
        provenance = assignment.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("source") != "human_declared":
            raise ValueError("ownership assignment provenance is invalid")
    for conflict in conflicts.values():
        evidence_ids = conflict.get("evidence_ids")
        pages = conflict.get("observed_pages")
        if (conflict.get("key_type") != "query" or conflict.get("impact") not in {
            "high", "medium", "low", "unavailable"
        } or conflict.get("confidence") not in {"high", "medium", "low", "unavailable"}
                or not isinstance(conflict.get("active"), bool)):
            raise ValueError("ownership conflict shape is invalid")
        if (not isinstance(evidence_ids, list) or not evidence_ids or len(set(evidence_ids)) != len(evidence_ids)
                or not set(evidence_ids) <= set(evidence)):
            raise ValueError("ownership conflict evidence references are invalid")
        if (not isinstance(pages, list) or any(not isinstance(item, dict) for item in pages)
                or len({item.get("site_id") for item in pages}) < 2):
            raise ValueError("ownership conflict must contain a cross-site participant set")
        for page in pages:
            if page.get("site_id") not in sites or page.get("evidence_id") not in evidence:
                raise ValueError("ownership conflict page references are invalid")
        expected_sites = sorted({sites[page["site_id"]].get("canonical_url") for page in pages})
        if conflict.get("observed_sites") != expected_sites:
            raise ValueError("ownership conflict observed sites are invalid")
    for decision in decisions.values():
        conflict = conflicts.get(decision.get("conflict_id"))
        if not conflict:
            raise ValueError("ownership decision conflict reference is invalid")
        if (decision.get("decision") not in {"accept", "differentiate", "consolidate", "route"}
                or decision.get("state") not in {"pending", "recorded"}
                or decision.get("actor_assertion") not in {"agent_draft", "local_operator_assertion"}
                or (decision["state"] == "pending") != (decision["actor_assertion"] == "agent_draft")):
            raise ValueError("ownership decision state or actor assertion is invalid")
        expected_pages = sorted(item["url"] for item in conflict["observed_pages"])
        if decision.get("participant_pages") != expected_pages:
            raise ValueError("ownership decision participant set is invalid")
        if decision.get("input_evidence_ids") != conflict["evidence_ids"]:
            raise ValueError("ownership decision evidence set is invalid")
        if decision.get("external_execution_available") is not False:
            raise ValueError("ownership decision execution policy is invalid")
        supersedes_id = decision.get("supersedes_id")
        if supersedes_id is not None:
            previous = decisions.get(supersedes_id)
            if not previous or previous.get("conflict_id") != decision.get("conflict_id"):
                raise ValueError("ownership decision supersession is invalid")
        roles = decision.get("page_roles")
        if not isinstance(roles, dict) or not all(page in expected_pages and isinstance(role, str) and role
                                                  for page, role in roles.items()):
            raise ValueError("ownership decision page roles are invalid")
        if decision["decision"] == "differentiate" and (
            set(roles) != set(expected_pages) or len({role.casefold() for role in roles.values()}) != len(expected_pages)
        ):
            raise ValueError("ownership differentiate decision is invalid")
        if decision["decision"] == "route" and set(roles) != set(expected_pages):
            raise ValueError("ownership route decision is invalid")
        if decision["decision"] in {"consolidate", "route"} and decision.get("primary_url") not in expected_pages:
            raise ValueError("ownership decision primary page is invalid")
        if decision["decision"] in {"accept", "differentiate"} and decision.get("primary_url") is not None:
            raise ValueError("ownership decision unexpectedly selects a primary page")
        provenance = decision.get("provenance")
        if (not isinstance(provenance, dict) or provenance.get("source") != decision["actor_assertion"]
                or provenance.get("input_evidence_ids") != decision["input_evidence_ids"]):
            raise ValueError("ownership decision provenance is invalid")
    superseded_decisions = {item["supersedes_id"] for item in decisions.values() if item.get("supersedes_id")}
    for conflict_id in conflicts:
        current = [item for item in decisions.values()
                   if item["conflict_id"] == conflict_id and item["id"] not in superseded_decisions]
        if len(current) > 1:
            raise ValueError("ownership export has ambiguous current decisions")
    for plan in plans.values():
        decision = decisions.get(plan.get("decision_id"))
        if not decision or plan.get("conflict_id") != decision.get("conflict_id"):
            raise ValueError("ownership action-plan references are invalid")
        if plan.get("state") != "draft" or plan.get("external_execution_available") is not False:
            raise ValueError("ownership action-plan execution policy is invalid")
        if any(step.get("external_write") is not False for step in plan.get("steps", [])):
            raise ValueError("ownership action-plan contains an executable step")
        dependencies = plan.get("dependencies")
        if (not isinstance(dependencies, list) or plan["id"] in dependencies
                or not set(dependencies) <= set(plans)):
            raise ValueError("ownership action-plan dependency references are invalid")
    validate_portable_export(payload)
