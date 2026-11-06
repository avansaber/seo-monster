"""Closed-loop workbench orchestration."""

from __future__ import annotations

import hashlib
import json
from functools import wraps
from datetime import UTC, datetime, date
from pathlib import Path
from typing import Any

from .adapter import AdaptedResult, adapt_saved_result, load_saved_result
from .policy import CapabilityPolicy
from .ownership import detect_cross_site_conflicts, normalize_key
from .provenance import validate_derivation
from .rules import analyze, normalize
from .scope import canonical_page_url, resolve_project_site, validate_capture_scope, validate_affected_pages
from .store import SCHEMA_VERSION, Store
from .util import canonical_json, stable_id, timestamp, finite_number


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic(method):
    """Keep each multi-record local operation all-or-nothing, including CLI calls."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.store.transaction():
            return method(self, *args, **kwargs)
    return wrapped


class Workbench:
    def __init__(self, database: str | Path):
        self.store = Store(database)
        self.policy = CapabilityPolicy()

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "Workbench":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def create_project(
        self,
        project_id: str,
        name: str,
        business_context: dict[str, Any],
        sites: list[str],
        portfolio_name: str = "Default portfolio",
        created_at: str | None = None,
    ) -> None:
        self.store.create_project(project_id, name, business_context, sites, portfolio_name, created_at or utc_now())

    def ingest(self, project_id: str, path: str | Path) -> dict[str, Any]:
        return self._ingest(load_saved_result(path, project_id))

    def ingest_capture(self, project_id: str, capture: dict[str, Any]) -> dict[str, Any]:
        return self._ingest(adapt_saved_result(capture, project_id))

    def preview_rule_replay(self, project_id: str, snapshot_id: str) -> dict[str, Any]:
        from .replay import preview_rule_replay

        return preview_rule_replay(self.store, project_id, snapshot_id)

    @atomic
    def record_deployment_observation(
        self, project_id: str, plan_id: str, observation: dict[str, Any], recorded_at: str | None = None,
    ) -> dict[str, Any]:
        from .change_ledger import record_deployment_observation

        return record_deployment_observation(self.store, project_id, plan_id, observation, recorded_at or utc_now())

    def inspect_deployment_observation(self, project_id: str, identifier: str) -> dict[str, Any]:
        from .change_ledger import inspect_deployment_observation

        return inspect_deployment_observation(self.store, project_id, identifier)

    def export_change_ledger(self, project_id: str, output: str | Path, exported_at: str | None = None) -> dict[str, Any]:
        from .change_ledger import build_change_export

        payload = build_change_export(self.store, project_id, exported_at or utc_now())
        serialized = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with Path(output).open("xb") as stream:
            stream.write(serialized)
        return {
            "path": str(output), "format_version": payload["format_version"],
            "deployment_observations": len(payload["deployment_observations"]),
            "sha256": hashlib.sha256(serialized).hexdigest(),
            "external_execution_available": False,
        }

    def _ingest(self, result: AdaptedResult) -> dict[str, Any]:
        project_id = result.project_id
        if not self.store.has_project(project_id):
            raise ValueError(f"unknown project: {project_id}")
        sites = [row["canonical_url"] for row in self.store.rows("sites", project_id)]
        validate_capture_scope(result, sites)
        evidence = normalize(result)
        findings, recommendations = analyze(result, evidence)
        validate_derivation(result, evidence, findings, recommendations)
        validate_affected_pages(findings, sites)
        with self.store.transaction():
            self.store.save_snapshot(result)
            self.store.upsert_rows("evidence", evidence)
            self.store.upsert_rows("findings", findings)
            self.store.upsert_rows("recommendations", recommendations)
            self.refresh_ownership_conflicts(project_id)
        return {
            "snapshot_id": result.snapshot_id,
            "content_hash": result.content_hash,
            "tool": result.tool,
            "evidence": len(evidence),
            "findings": len(findings),
            "recommendations": len(recommendations),
        }

    def board(self, project_id: str) -> list[dict[str, Any]]:
        findings = {row["id"]: self.store.decode(row) for row in self.store.rows("findings", project_id)}
        board = []
        for recommendation in self.store.rows("recommendations", project_id):
            rec = self.store.decode(recommendation)
            finding = findings[rec["finding_id"]]
            board.append({"recommendation": rec, "finding": finding})
        rank = {"high": 0, "medium": 1, "low": 2, "unavailable": 3}
        board.sort(
            key=lambda item: (
                rank.get(item["finding"]["impact"], 9),
                rank.get(item["finding"]["confidence"], 9),
                item["recommendation"]["id"],
            )
        )
        return board

    def inspect_recommendation(self, recommendation_id: str) -> dict[str, Any]:
        rec_row = self.store.one("recommendations", recommendation_id)
        if not rec_row:
            raise ValueError(f"unknown recommendation: {recommendation_id}")
        rec = self.store.decode(rec_row)
        finding_row = self.store.one("findings", rec["finding_id"])
        if not finding_row:
            raise ValueError("recommendation references a missing finding")
        finding = self.store.decode(finding_row)
        evidence = []
        snapshots: dict[str, dict[str, Any]] = {}
        for evidence_id in finding["evidence_ids"]:
            row = self.store.one("evidence", evidence_id)
            if not row:
                raise ValueError("recommendation references missing evidence")
            decoded = self.store.decode(row)
            evidence.append(decoded)
            snapshot_id = decoded["snapshot_id"]
            if snapshot_id not in snapshots:
                snapshot = self.store.one("snapshots", snapshot_id)
                if snapshot:
                    snapshots[snapshot_id] = self.store.decode(snapshot)
                else:
                    raise ValueError("evidence references a missing snapshot")
        return {
            "recommendation": rec,
            "finding": finding,
            "exact_inputs": evidence,
            "source_snapshots": list(snapshots.values()),
            "calculation": finding["calculation"],
            "rule": {"id": finding["rule_id"], "version": finding["rule_version"]},
        }

    @atomic
    def draft_plan(
        self,
        project_id: str,
        recommendation_id: str,
        dependencies: list[str] | None = None,
        rollback_notes: str = "Restore the prior content or routing configuration after independent verification.",
        created_at: str | None = None,
    ) -> dict[str, Any]:
        inspection = self.inspect_recommendation(recommendation_id)
        recommendation = inspection["recommendation"]
        if recommendation["project_id"] != project_id:
            raise ValueError("recommendation belongs to a different project")
        for dependency in dependencies or []:
            dependency_row = self.store.one("action_plans", dependency)
            if not dependency_row or dependency_row["project_id"] != project_id:
                raise ValueError("plan dependency must exist in the same project")
        plan_id = stable_id("plan", project_id, recommendation_id)
        existing_plan = self.store.one("action_plans", plan_id)
        if existing_plan:
            existing = self.store.decode(existing_plan)
            if existing["dependencies"] != (dependencies or []) or existing["rollback_notes"] != rollback_notes:
                raise ValueError("plan exists with different dependencies or rollback notes; revisions are not supported")
            return existing
        steps = [
            {
                "id": "inspect-evidence",
                "kind": "local_review",
                "mode": "preview",
                "description": "Review the cited inputs, calculation, timestamp, and caveats.",
                "dependencies": [],
                "external_write": False,
            },
            {
                "id": "draft-change",
                "kind": "change_draft",
                "mode": "recommendation",
                "description": recommendation["title"],
                "dependencies": ["inspect-evidence"],
                "external_write": False,
            },
            {
                "id": "request-approval",
                "kind": "approval_gate",
                "mode": "preview",
                "description": "Request an explicit human decision in a system that can authenticate the approver.",
                "dependencies": ["draft-change"],
                "external_write": False,
            },
        ]
        row = {
            "id": plan_id,
            "project_id": project_id,
            "recommendation_id": recommendation_id,
            "state": "draft",
            "created_at": created_at or utc_now(),
            "dependencies_json": canonical_json(dependencies or []),
            "steps_json": canonical_json(steps),
            "rollback_notes": rollback_notes,
            "external_execution_available": 0,
        }
        self.store.insert("action_plans", row)
        decision_id = stable_id("decision", plan_id, "pending")
        self.store.insert(
            "decisions",
            {
                "id": decision_id,
                "project_id": project_id,
                "recommendation_id": recommendation_id,
                "state": "pending",
                "decided_at": row["created_at"],
                "note": "Action plan drafted; no execution was requested or performed.",
            },
        )
        return self.store.decode(row)

    @atomic
    def approve_plan(self, plan_id: str, note: str, decided_at: str | None = None) -> dict[str, Any]:
        plan_row = self.store.one("action_plans", plan_id)
        if not plan_row:
            raise ValueError(f"unknown action plan: {plan_id}")
        plan = self.store.decode(plan_row)
        at = decided_at or utc_now()
        decision_id = stable_id("decision", plan_id, "approved", at, note)
        self.store.insert(
            "decisions",
            {
                "id": decision_id,
                "project_id": plan["project_id"],
                "recommendation_id": plan["recommendation_id"],
                "state": "approved",
                "decided_at": at,
                "note": note,
            },
        )
        self.store.update("action_plans", plan_id, {"state": "approved"})
        self.store.update("recommendations", plan["recommendation_id"], {"state": "approved"})
        updated = self.store.one("action_plans", plan_id)
        return self.store.decode(updated or plan_row)

    @atomic
    def start_experiment(
        self,
        project_id: str,
        recommendation_id: str,
        baseline_evidence_id: str,
        hypothesis: str,
        observation_window: dict[str, str],
        created_at: str | None = None,
    ) -> dict[str, Any]:
        baseline = self.store.one("evidence", baseline_evidence_id)
        if not baseline or baseline["project_id"] != project_id:
            raise ValueError("baseline evidence is missing or belongs to another project")
        recommendation = self.store.one("recommendations", recommendation_id)
        if not recommendation or recommendation["project_id"] != project_id:
            raise ValueError("recommendation is missing or belongs to another project")
        if baseline_evidence_id not in self.store.decode(recommendation)["provenance"]["input_evidence_ids"]:
            raise ValueError("baseline must be cited by the recommendation")
        start, end = date.fromisoformat(observation_window["start"]), date.fromisoformat(observation_window["end"])
        if start > end:
            raise ValueError("observation window start must precede end")
        experiment_id = stable_id("experiment", project_id, recommendation_id, baseline_evidence_id, hypothesis, observation_window)
        existing_experiment = self.store.one("experiments", experiment_id)
        if existing_experiment:
            return self.store.decode(existing_experiment)
        row = {
            "id": experiment_id,
            "project_id": project_id,
            "recommendation_id": recommendation_id,
            "hypothesis": hypothesis,
            "state": "baseline_recorded",
            "baseline_evidence_id": baseline_evidence_id,
            "outcome_evidence_id": None,
            "observation_window_json": canonical_json(observation_window),
            "comparison_json": None,
            "causal_claim": "none; this ledger records temporal association only",
            "created_at": created_at or utc_now(),
        }
        self.store.insert("experiments", row)
        return self.store.decode(row)

    @atomic
    def compare_experiment(self, experiment_id: str, outcome_evidence_id: str) -> dict[str, Any]:
        experiment_row = self.store.one("experiments", experiment_id)
        outcome_row = self.store.one("evidence", outcome_evidence_id)
        if not experiment_row or not outcome_row:
            raise ValueError("experiment or outcome evidence not found")
        experiment = self.store.decode(experiment_row)
        if experiment["outcome_evidence_id"] not in (None, outcome_evidence_id):
            raise ValueError("experiment already records a different outcome; create a separate experiment")
        baseline_row = self.store.one("evidence", experiment["baseline_evidence_id"])
        if not baseline_row:
            raise ValueError("baseline evidence not found")
        baseline = self.store.decode(baseline_row)
        outcome = self.store.decode(outcome_row)
        if baseline["project_id"] != outcome["project_id"] or baseline["subject"] != outcome["subject"]:
            raise ValueError("outcome must describe the same project and subject as the baseline")
        if timestamp(outcome["observed_at"]) <= timestamp(baseline["observed_at"]):
            raise ValueError("outcome must be captured later than the baseline")
        if baseline["evidence_type"] != outcome["evidence_type"]:
            raise ValueError("outcome must use the same measurement type")
        bp, op = baseline["provenance"], outcome["provenance"]
        before_args, after_args = bp["arguments"], op["arguments"]
        if self._measurement_scope(baseline) != self._measurement_scope(outcome):
            raise ValueError("outcome must use the same tool, property, dimensions and filters")
        caveats = list(dict.fromkeys(bp["caveats"] + op["caveats"]))
        if "start_date" in before_args and "start_date" in after_args:
            if after_args["start_date"] <= before_args["end_date"]:
                raise ValueError("outcome measurement period must be later and non-overlapping")
            window = experiment["observation_window"]
            if after_args["start_date"] != window["start"] or after_args["end_date"] != window["end"]:
                raise ValueError("outcome measurement period must match the observation window")
            durations = [(date.fromisoformat(a["end_date"]) - date.fromisoformat(a["start_date"])).days + 1
                         for a in (before_args, after_args)]
            if durations[0] != durations[1]:
                caveats.append("Measurement windows differ in length; raw count deltas are not rate-normalized.")
        else:
            caveats.append("Measurement periods are unavailable; only capture chronology is established.")
        before = baseline.get("value") or {}
        after = outcome.get("value") or {}
        deltas: dict[str, float | None] = {}
        for metric in ("clicks", "impressions", "ctr", "position"):
            left = before.get(metric) if isinstance(before, dict) else None
            right = after.get(metric) if isinstance(after, dict) else None
            observed = baseline["availability"] == outcome["availability"] == "observed"
            deltas[metric] = right - left if observed and finite_number(left) and finite_number(right) else None
        comparison = {
            "baseline_observed_at": baseline["observed_at"],
            "outcome_observed_at": outcome["observed_at"],
            "subject": baseline["subject"],
            "deltas": deltas,
            "interpretation": "Observed after the recorded baseline; causation is not established.",
            "missing_metrics_remain_null": True,
            "caveats": caveats,
            "measurement_arguments": {"baseline": before_args, "outcome": after_args},
            "baseline_evidence_id": baseline["id"],
            "outcome_evidence_id": outcome["id"],
        }
        self.store.update(
            "experiments",
            experiment_id,
            {
                "state": "outcome_observed",
                "outcome_evidence_id": outcome_evidence_id,
                "comparison_json": canonical_json(comparison),
            },
        )
        updated = self.store.one("experiments", experiment_id)
        return self.store.decode(updated or experiment_row)

    def _measurement_scope(self, evidence: dict[str, Any]) -> dict[str, Any]:
        provenance = evidence["provenance"]
        snapshot = self.store.one("snapshots", evidence["snapshot_id"])
        if not snapshot:
            raise ValueError("measurement source snapshot missing")
        data = self.store.decode(snapshot)["envelope"]["data"]
        data = data if isinstance(data, dict) else {}
        return {
            "type": evidence["evidence_type"], "tool": provenance["source_tool"],
            "arguments": {k: v for k, v in provenance["arguments"].items() if k not in {"start_date", "end_date"}},
            "resolved_parameters": {k: data[k] for k in ("site_url", "property_id", "dimensions", "metrics",
                                                       "data_state", "search_type", "filters") if k in data},
        }

    def find_matching_outcome(self, project_id: str, baseline_evidence_id: str) -> dict[str, Any] | None:
        baseline_row = self.store.one("evidence", baseline_evidence_id)
        if not baseline_row or baseline_row["project_id"] != project_id:
            return None
        baseline = self.store.decode(baseline_row)
        rows = self.store.evidence_for_subject(project_id, baseline_row["subject"], exclude_id=baseline_evidence_id)
        later = []
        for row in rows:
            candidate = self.store.decode(row)
            if timestamp(row["observed_at"]) <= timestamp(baseline_row["observed_at"]):
                continue
            if self._measurement_scope(baseline) != self._measurement_scope(candidate):
                continue
            before, after = baseline["provenance"]["arguments"], candidate["provenance"]["arguments"]
            if before.get("end_date") and after.get("start_date") and after["start_date"] <= before["end_date"]:
                continue
            later.append(row)
        later.sort(key=lambda row: (timestamp(row["observed_at"]), row["id"]))
        return self.store.decode(later[-1]) if later else None

    def refresh_ownership_conflicts(self, project_id: str) -> int:
        if not self.store.has_project(project_id):
            raise ValueError("unknown project")
        conflicts = detect_cross_site_conflicts(self.store, project_id)
        self.store.replace_active_ownership_conflicts(project_id, conflicts)
        return len(conflicts)

    def ownership_assignments(self, project_id: str, *, active_only: bool = False) -> list[dict[str, Any]]:
        rows = [self.store.decode(row) for row in self.store.rows("ownership_assignments", project_id)]
        superseded = {row["supersedes_id"] for row in rows if row.get("supersedes_id")}
        for row in rows:
            row["active"] = row["id"] not in superseded
        rows.sort(key=lambda row: (timestamp(row["created_at"]), row["id"]))
        return [row for row in rows if row["active"]] if active_only else rows

    def _ownership_decision_history(
        self, project_id: str, conflict_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        history = [self.store.decode(row) for row in self.store.rows("ownership_decisions", project_id)
                   if row["conflict_id"] == conflict_id]
        superseded = {item["supersedes_id"] for item in history if item.get("supersedes_id")}
        current = [item for item in history if item["id"] not in superseded]
        history.sort(key=lambda item: (timestamp(item["decided_at"]), item["id"]))
        current.sort(key=lambda item: (timestamp(item["decided_at"]), item["id"]))
        return history, current

    def ownership_board(self, project_id: str) -> list[dict[str, Any]]:
        assignments = self.ownership_assignments(project_id, active_only=True)
        decisions = [self.store.decode(row) for row in self.store.rows("ownership_decisions", project_id)]
        plans = [self.store.decode(row) for row in self.store.rows("ownership_action_plans", project_id)]
        board = []
        for row in self.store.rows("ownership_conflicts", project_id):
            conflict = self.store.decode(row)
            if not conflict["active"]:
                continue
            matching_assignments = [item for item in assignments if item["key_type"] == conflict["key_type"]
                                    and item["normalized_key"] == conflict["normalized_key"]]
            matching_decisions = [item for item in decisions if item["conflict_id"] == conflict["id"]]
            superseded = {item["supersedes_id"] for item in matching_decisions if item.get("supersedes_id")}
            current = [item for item in matching_decisions if item["id"] not in superseded]
            current.sort(key=lambda item: (timestamp(item["decided_at"]), item["id"]))
            recorded = [item for item in current if item["state"] == "recorded"]
            pending = [item for item in current if item["state"] == "pending"]
            ambiguous = len(current) > 1
            resolution = recorded[0] if len(recorded) == 1 and not ambiguous else None
            pending_decision = pending[0] if len(pending) == 1 and not ambiguous else None
            board.append({
                "conflict": conflict,
                "declared_assignments": matching_assignments,
                "resolution": resolution,
                "pending_decision": pending_decision,
                "resolution_candidates": current if ambiguous else [],
                "plans": [item for item in plans if item["conflict_id"] == conflict["id"]],
                "state": ("ambiguous_resolution" if ambiguous
                          else "accepted_overlap" if resolution and resolution["decision"] == "accept"
                          else resolution["decision"] if resolution else "pending" if pending_decision else "open"),
            })
        rank = {"high": 0, "medium": 1, "low": 2, "unavailable": 3}
        board.sort(key=lambda item: (rank.get(item["conflict"]["impact"], 9),
                                    item["conflict"]["normalized_key"], item["conflict"]["id"]))
        return board

    def inspect_ownership_conflict(self, conflict_id: str) -> dict[str, Any]:
        row = self.store.one("ownership_conflicts", conflict_id)
        if not row:
            raise ValueError("unknown ownership conflict")
        conflict = self.store.decode(row)
        exact_inputs, snapshots = [], {}
        for evidence_id in conflict["evidence_ids"]:
            evidence = self.store.one("evidence", evidence_id)
            if not evidence or evidence["project_id"] != conflict["project_id"]:
                raise ValueError("ownership conflict references missing evidence")
            decoded = self.store.decode(evidence)
            exact_inputs.append(decoded)
            snapshot = self.store.one("snapshots", decoded["snapshot_id"])
            if not snapshot:
                raise ValueError("ownership evidence references a missing snapshot")
            snapshots[decoded["snapshot_id"]] = self.store.decode(snapshot)
        assignments = [item for item in self.ownership_assignments(conflict["project_id"], active_only=True)
                       if item["key_type"] == conflict["key_type"]
                       and item["normalized_key"] == conflict["normalized_key"]]
        decisions = [self.store.decode(item) for item in self.store.rows("ownership_decisions", conflict["project_id"])
                     if item["conflict_id"] == conflict_id]
        plans = [self.store.decode(item) for item in self.store.rows("ownership_action_plans", conflict["project_id"])
                 if item["conflict_id"] == conflict_id]
        return {
            "conflict": conflict, "exact_inputs": exact_inputs, "source_snapshots": list(snapshots.values()),
            "declared_assignments": assignments, "decisions": decisions, "plans": plans,
            "calculation": conflict["calculation"],
            "rule": {"id": conflict["rule_id"], "version": conflict["rule_version"]},
        }

    @atomic
    def record_ownership_assignment(
        self, project_id: str, key_type: str, key_text: str, owner_url: str, role: str,
        intent: str, rationale: str, basis_evidence_ids: list[str] | None = None,
        supersedes_id: str | None = None, created_at: str | None = None,
    ) -> dict[str, Any]:
        if key_type not in {"query", "topic"} or role not in {"primary", "supporting", "intent_variant", "accepted_overlap"}:
            raise ValueError("unsupported ownership key type or role")
        normalized = normalize_key(key_text)
        owner_url = canonical_page_url(owner_url)
        sites = self.store.rows("sites", project_id)
        try:
            owner_site = resolve_project_site(owner_url, sites)
        except ValueError as exc:
            raise ValueError("owner page must belong to a configured project site") from exc
        if not isinstance(intent, str) or not intent.strip() or len(intent) > 2000:
            raise ValueError("intent must be nonempty bounded text")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 4000:
            raise ValueError("rationale must be nonempty bounded text")
        evidence_ids = sorted(set(basis_evidence_ids or []))
        for evidence_id in evidence_ids:
            evidence = self.store.one("evidence", evidence_id)
            if not evidence or evidence["project_id"] != project_id:
                raise ValueError("assignment basis evidence must belong to the project")
        active = [row for row in self.ownership_assignments(project_id, active_only=True)
                  if row["key_type"] == key_type and row["normalized_key"] == normalized]
        at = created_at or utc_now()
        timestamp(at)
        assignment_id = stable_id("ownership", project_id, key_type, normalized, owner_url, role, intent, rationale,
                                  evidence_ids, supersedes_id)
        existing = self.store.one("ownership_assignments", assignment_id)
        if existing:
            return next(item for item in self.ownership_assignments(project_id) if item["id"] == assignment_id)
        if supersedes_id:
            superseded = next((item for item in active if item["id"] == supersedes_id), None)
            if not superseded:
                raise ValueError("superseded assignment must be active and use the same ownership key")
        current_primary = next((item for item in active if item["role"] == "primary"), None)
        if role == "primary" and current_primary and supersedes_id != current_primary["id"]:
            raise ValueError("a primary assignment exists; explicitly supersede it")
        row = {
            "id": assignment_id, "project_id": project_id, "key_type": key_type, "key_text": key_text.strip(),
            "normalized_key": normalized, "owner_site_id": owner_site["id"], "owner_url": owner_url,
            "role": role, "intent": intent.strip(), "rationale": rationale.strip(), "source": "human_declared",
            "basis_evidence_ids_json": canonical_json(evidence_ids),
            "provenance_json": canonical_json({"source": "human_declared", "recorded_at": at,
                                                "basis_evidence_ids": evidence_ids,
                                                "caveats": ["This assignment records intended ownership; it is not inferred from rankings."]}),
            "created_at": at, "supersedes_id": supersedes_id,
        }
        self.store.insert("ownership_assignments", row)
        return next(item for item in self.ownership_assignments(project_id) if item["id"] == assignment_id)

    @atomic
    def _ownership_decision(
        self, project_id: str, conflict_id: str, decision: str, primary_url: str | None,
        page_roles: dict[str, str], note: str, decided_at: str | None, *, state: str,
        actor_assertion: str, supersedes_id: str | None = None,
    ) -> dict[str, Any]:
        if decision not in {"accept", "differentiate", "consolidate", "route"}:
            raise ValueError("unsupported ownership decision")
        conflict_row = self.store.one("ownership_conflicts", conflict_id)
        if not conflict_row or conflict_row["project_id"] != project_id or not conflict_row["active"]:
            raise ValueError("ownership conflict is missing, inactive, or belongs to another project")
        conflict = self.store.decode(conflict_row)
        observed = {item["url"] for item in conflict["observed_pages"]}
        participant_pages = sorted(observed)
        normalized_roles = {}
        for page, role_text in page_roles.items():
            page = canonical_page_url(page)
            if page not in observed or not isinstance(role_text, str) or not role_text.strip() or len(role_text) > 1000:
                raise ValueError("page roles must use observed pages and bounded nonempty text")
            normalized_roles[page] = role_text.strip()
        if decision == "differentiate" and (set(normalized_roles) != observed
                or len({value.casefold() for value in normalized_roles.values()}) != len(observed)):
            raise ValueError("differentiate must assign every observed page a distinct intent")
        normalized_primary = canonical_page_url(primary_url) if primary_url else None
        if decision in {"consolidate", "route"} and normalized_primary not in observed:
            raise ValueError("consolidate or route requires an observed primary page")
        if decision in {"accept", "differentiate"} and normalized_primary is not None:
            raise ValueError("accept and differentiate do not select a primary page")
        if decision == "route" and set(normalized_roles) != observed:
            raise ValueError("route must assign a role to every observed page")
        if not isinstance(note, str) or not note.strip() or len(note) > 4000:
            raise ValueError("decision note must be nonempty bounded text")
        at = decided_at or utc_now()
        timestamp(at)
        if state not in {"pending", "recorded"}:
            raise ValueError("invalid ownership decision state")
        decision_id = stable_id("ownership_decision", project_id, conflict_id, decision, state, normalized_primary,
                                normalized_roles, note.strip(), supersedes_id)
        existing = self.store.one("ownership_decisions", decision_id)
        if existing:
            return self.store.decode(existing)
        _history, current = self._ownership_decision_history(project_id, conflict_id)
        if len(current) > 1:
            raise ValueError("ownership conflict has ambiguous current decisions; repair before recording another")
        if current and supersedes_id != current[0]["id"]:
            raise ValueError("a current ownership decision exists; explicitly supersede it")
        if not current and supersedes_id is not None:
            raise ValueError("superseded decision must be the current decision for this exact conflict")
        row = {
            "id": decision_id, "project_id": project_id, "conflict_id": conflict_id, "decision": decision,
            "state": state, "participant_pages_json": canonical_json(participant_pages),
            "primary_url": normalized_primary, "page_roles_json": canonical_json(normalized_roles),
            "note": note.strip(), "decided_at": at, "actor_assertion": actor_assertion,
            "input_evidence_ids_json": canonical_json(conflict["evidence_ids"]),
            "provenance_json": canonical_json({
                "source": actor_assertion, "recorded_at": at, "conflict_id": conflict_id,
                "participant_pages": participant_pages, "input_evidence_ids": conflict["evidence_ids"],
                "caveats": ["This decision records intent only; no site change or external execution occurred."],
            }),
            "supersedes_id": supersedes_id, "external_execution_available": 0,
        }
        self.store.insert("ownership_decisions", row)
        stored = self.store.one("ownership_decisions", decision_id)
        return self.store.decode(stored or row)

    @atomic
    def record_ownership_decision(
        self, project_id: str, conflict_id: str, decision: str, primary_url: str | None,
        page_roles: dict[str, str], note: str, decided_at: str | None = None,
        supersedes_id: str | None = None,
    ) -> dict[str, Any]:
        """Operator assertion. The MCP facade does not expose this finalizing operation."""
        return self._ownership_decision(
            project_id, conflict_id, decision, primary_url, page_roles, note, decided_at,
            state="recorded", actor_assertion="local_operator_assertion", supersedes_id=supersedes_id,
        )

    @atomic
    def draft_ownership_decision(
        self, project_id: str, conflict_id: str, decision: str, primary_url: str | None,
        page_roles: dict[str, str], note: str, created_at: str | None = None,
        supersedes_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a pending proposal; it is not a human decision or approval."""
        return self._ownership_decision(
            project_id, conflict_id, decision, primary_url, page_roles, note, created_at,
            state="pending", actor_assertion="agent_draft", supersedes_id=supersedes_id,
        )

    @atomic
    def draft_ownership_plan(
        self, project_id: str, decision_id: str, rollback_notes: str,
        dependencies: list[str] | None = None, created_at: str | None = None,
    ) -> dict[str, Any]:
        decision_row = self.store.one("ownership_decisions", decision_id)
        if not decision_row or decision_row["project_id"] != project_id:
            raise ValueError("ownership decision is missing or belongs to another project")
        decision = self.store.decode(decision_row)
        conflict = self.store.one("ownership_conflicts", decision["conflict_id"])
        if not conflict or conflict["project_id"] != project_id or not conflict["active"]:
            raise ValueError("ownership conflict is missing, inactive, or belongs to another project")
        _history, current = self._ownership_decision_history(project_id, decision["conflict_id"])
        if len(current) != 1 or current[0]["id"] != decision_id:
            raise ValueError("ownership plan requires the sole current decision for the active conflict")
        dependency_ids = sorted(set(dependencies or []))
        for dependency in dependency_ids:
            row = self.store.one("ownership_action_plans", dependency)
            if not row or row["project_id"] != project_id:
                raise ValueError("ownership plan dependency must exist in the same project")
        if not isinstance(rollback_notes, str) or not rollback_notes.strip() or len(rollback_notes) > 4000:
            raise ValueError("rollback notes must be nonempty bounded text")
        plan_id = stable_id("ownership_plan", project_id, decision_id, dependency_ids, rollback_notes.strip())
        existing = self.store.one("ownership_action_plans", plan_id)
        if existing:
            return self.store.decode(existing)
        at = created_at or utc_now()
        timestamp(at)
        steps = [
            {"id": "inspect-evidence", "kind": "local_review", "mode": "preview",
             "description": "Review every cited cross-site observation and caveat.", "dependencies": [],
             "external_write": False},
            {"id": "confirm-ownership", "kind": "ownership_review", "mode": "recommendation",
             "description": "Confirm human-declared page and intent ownership separately from observed visibility.",
             "dependencies": ["inspect-evidence"], "external_write": False},
            {"id": "draft-decision", "kind": f"{decision['decision']}_draft", "mode": "recommendation",
             "description": f"Draft the {decision['decision']} proposal without changing any site.",
             "dependencies": ["confirm-ownership"], "external_write": False},
            {"id": "request-approval", "kind": "approval_gate", "mode": "preview",
             "description": "Request separate authenticated approval before any external implementation.",
             "dependencies": ["draft-decision"], "external_write": False},
        ]
        row = {
            "id": plan_id, "project_id": project_id, "conflict_id": decision["conflict_id"],
            "decision_id": decision_id, "state": "draft", "created_at": at,
            "dependencies_json": canonical_json(dependency_ids), "steps_json": canonical_json(steps),
            "rollback_notes": rollback_notes.strip(), "external_execution_available": 0,
        }
        self.store.insert("ownership_action_plans", row)
        return self.store.decode(row)

    def export_project(self, project_id: str, path: str | Path, exported_at: str | None = None) -> dict[str, Any]:
        from .backup import validate_project_export
        from .portable import EXPORT_SCHEMA_VERSION, build_export, validate_portable_export

        payload = build_export(self.store, project_id, exported_at or utc_now())
        validate_project_export(payload)
        validate_portable_export(payload)
        serialized = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with Path(path).open("xb") as stream:
            stream.write(serialized)
        return {
            "path": str(path),
            "schema_version": EXPORT_SCHEMA_VERSION,
            "sha256": hashlib.sha256(serialized).hexdigest(),
        }

    def export_ownership(self, project_id: str, path: str | Path, exported_at: str | None = None) -> dict[str, Any]:
        from .portable import build_ownership_export

        payload = build_ownership_export(self.store, project_id, exported_at or utc_now())
        serialized = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with Path(path).open("xb") as stream:
            stream.write(serialized)
        return {
            "path": str(path), "format_version": payload["format_version"],
            "storage_schema_version": SCHEMA_VERSION, "sha256": hashlib.sha256(serialized).hexdigest(),
        }

    def backup_project(self, project_id: str, path: str | Path, exported_at: str | None = None) -> dict[str, Any]:
        from .backup import BACKUP_FORMAT_VERSION, build_backup

        payload = build_backup(self.store, project_id, exported_at or utc_now())
        serialized = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        with Path(path).open("xb") as stream:
            stream.write(serialized)
        return {
            "path": str(path), "format_version": BACKUP_FORMAT_VERSION,
            "storage_schema_version": SCHEMA_VERSION, "sha256": hashlib.sha256(serialized).hexdigest(),
            "external_execution_available": False,
        }

    def preview_restore(self, backup: dict[str, Any], expected_project_id: str | None = None) -> dict[str, Any]:
        from .backup import preview_restore

        return preview_restore(self.store, backup, expected_project_id)

    @atomic
    def restore_backup(self, backup: dict[str, Any], expected_project_id: str | None = None) -> dict[str, Any]:
        from .backup import apply_restore

        return apply_restore(self.store, backup, expected_project_id)
