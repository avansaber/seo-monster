"""Deterministic normalization and opportunity rules."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .adapter import AdaptedResult
from .util import canonical_json, digest, stable_id, finite_number


RULESET_VERSION = "2026-09-09.2"


def _base_caveats(result: AdaptedResult) -> list[str]:
    caveats = list(result.caveats)
    if result.tool in {"gsc_search_analytics", "gsc_compare_periods"}:
        data = result.envelope.get("data")
        if not result.arguments.get("site_url") and not (isinstance(data, dict) and data.get("site_url")):
            caveats.append("GSC property scope is unavailable; only captured page hosts can be attributed to this project.")
    if result.freshness == "stale":
        caveats.append("The source declares this snapshot stale; confidence is reduced and the value is not treated as current.")
    elif result.freshness == "unknown":
        caveats.append("Source freshness is unknown; current-state conclusions are unavailable.")
    provider_status = str(result.provider.get("status", "available"))
    if provider_status != "available":
        caveats.append(f"Provider status is {provider_status}; unavailable information is not converted to zero.")
    return caveats


def _provenance(result: AdaptedResult, transform: str, caveats: list[str]) -> dict[str, Any]:
    return {
        "source_snapshot_id": result.snapshot_id,
        "source_tool": result.tool,
        "captured_at": result.captured_at,
        "arguments": result.arguments,
        "arguments_hash": digest(result.arguments),
        "envelope_hash": digest(result.envelope),
        "provider": result.provider,
        "transform": transform,
        "transform_version": RULESET_VERSION,
        "freshness": result.freshness,
        "caveats": caveats,
    }


def _evidence(
    result: AdaptedResult,
    evidence_type: str,
    subject: str,
    value: Any,
    availability: str,
    transform: str,
    caveats: list[str],
    ordinal: int,
) -> dict[str, Any]:
    evidence_id = stable_id("ev", result.snapshot_id, RULESET_VERSION, evidence_type, subject, ordinal, value)
    return {
        "id": evidence_id,
        "project_id": result.project_id,
        "snapshot_id": result.snapshot_id,
        "evidence_type": evidence_type,
        "subject": subject,
        "observed_at": result.captured_at,
        "value_json": canonical_json(value) if value is not None else None,
        "availability": availability,
        "provenance_json": canonical_json(_provenance(result, transform, caveats)),
    }


def normalize(result: AdaptedResult) -> list[dict[str, Any]]:
    """Turn a saved public envelope into lossless evidence records."""
    caveats = _base_caveats(result)
    if not result.envelope["ok"]:
        error = result.envelope["error"] or {}
        subject = f"provider:{error.get('service') or result.provider.get('name', 'unknown')}"
        return [
            _evidence(
                result,
                "provider_error",
                subject,
                error,
                "unavailable",
                "envelope-error/v1",
                caveats + ["The failed provider response is evidence of unavailability, not a zero-valued signal."],
                0,
            )
        ]

    data = result.envelope["data"]
    if result.provider.get("status") not in {"available", "partial", "degraded"}:
        availability = "contradictory" if result.provider.get("status") == "contradictory" else "unavailable"
        return [_evidence(result, "provider_limited", f"tool:{result.tool}", data, availability,
                          "provider-limit/v1", caveats, 0)]
    rows = data.get("rows") if isinstance(data, dict) else None
    if result.tool in {"gsc_search_analytics", "gsc_compare_periods", "ga4_run_report"} and isinstance(rows, list):
        if not rows:
            return [
                _evidence(
                    result,
                    "no_data",
                    f"tool:{result.tool}",
                    {"reason": "empty_rows", "data_state": data.get("data_state")},
                    "unavailable",
                    "gsc-empty/v1",
                    caveats + ["No rows were returned; metrics remain unavailable rather than zero."],
                    0,
                )
            ]
        dimensions = data.get("dimensions", result.arguments.get("dimensions", []))
        if not isinstance(dimensions, list) or not all(isinstance(d, str) for d in dimensions):
            raise ValueError("dimensions must be a list of names")
        evidence_rows: list[dict[str, Any]] = []
        evidence_type = "analytics_performance" if result.tool == "ga4_run_report" else "search_performance"
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError("tabular rows must be objects")
            keys = row.get("dimensions", []) if result.tool == "ga4_run_report" else row.get("keys", [])
            if not isinstance(keys, list):
                raise ValueError("row dimension values must be a list")
            if len(keys) != len(dimensions):
                raise ValueError("row dimensions must match the declared dimension count")
            labels = {str(dimensions[i]): key for i, key in enumerate(keys) if i < len(dimensions)}
            subject = "|".join(f"{key}={labels[key]}" for key in sorted(labels)) or f"row:{index}"
            value = {**row, "dimensions": labels}
            if result.tool == "ga4_run_report":
                metric_names = data.get("metrics", result.arguments.get("metrics", []))
                metric_values = row.get("metrics", [])
                if not isinstance(metric_names, list) or not isinstance(metric_values, list):
                    raise ValueError("GA4 metrics must be lists")
                if len(metric_names) != len(metric_values) or not all(isinstance(m, str) for m in metric_names):
                    raise ValueError("GA4 metric names and values must align")
                value["normalized_metrics"] = dict(zip(metric_names, metric_values))
            if result.tool == "gsc_compare_periods" and isinstance(row.get("prior"), dict):
                value["previous_clicks"] = row["prior"].get("clicks")
            evidence_rows.append(
                _evidence(result, evidence_type, subject, value, "observed", "tabular-row/v1", caveats, index)
            )
        failed = data.get("failed", [])
        for offset, failure in enumerate(failed, start=len(evidence_rows)):
            evidence_rows.append(
                _evidence(
                    result,
                    "partial_provider_error",
                    f"failed:{offset}",
                    failure,
                    "unavailable",
                    "partial-error/v1",
                    caveats + ["A partial failure reduces coverage; missing rows are not inferred."],
                    offset,
                )
            )
        return evidence_rows

    if result.tool in {"redirect_chain_audit", "gsc_batch_inspect_urls"} and isinstance(data, dict):
        result_rows = data.get("results") or data.get("urls") or []
        if isinstance(result_rows, list) and result_rows:
            return [
                _evidence(
                    result,
                    "migration_observation",
                    str(row.get("url") or row.get("requested_url") or f"row:{index}"),
                    row,
                    "observed",
                    "migration-row/v1",
                    caveats,
                    index,
                )
                for index, row in enumerate(result_rows)
            ]

    if result.provider.get("status") == "contradictory":
        availability = "contradictory"
    else:
        availability = "observed" if data not in (None, {}, []) else "unavailable"
    generic_caveats = caveats[:]
    if availability == "unavailable":
        generic_caveats.append("The response contains no observed values; absence is not represented as zero.")
    if availability == "contradictory":
        generic_caveats.append("The captured sources conflict; the workbench preserves both and produces no confident conclusion.")
    return [
        _evidence(
            result,
            f"{result.tool}_result",
            f"tool:{result.tool}",
            data,
            availability,
            "opaque-envelope/v1",
            generic_caveats,
            0,
        )
    ]


def _confidence(result: AdaptedResult, partial: bool = False) -> str:
    if result.tool in {"gsc_search_analytics", "gsc_compare_periods"}:
        data = result.envelope.get("data")
        if not result.arguments.get("site_url") and not (isinstance(data, dict) and data.get("site_url")):
            return "low"
    if result.freshness != "current" or result.provider.get("status", "available") != "available" or partial:
        return "low"
    return "high"


def _finding_and_recommendation(
    result: AdaptedResult,
    *,
    kind: str,
    title: str,
    affected_pages: list[str],
    impact: str,
    confidence: str,
    effort: str,
    caveats: list[str],
    evidence_ids: list[str],
    rule_id: str,
    calculation: str,
    recommendation_title: str,
    rationale: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    fingerprint = digest(
        {
            "snapshot_id": result.snapshot_id,
            "rule_id": rule_id,
            "rule_version": RULESET_VERSION,
            "evidence_ids": sorted(evidence_ids),
        }
    )
    finding_id = f"finding_{fingerprint[:24]}"
    recommendation_id = stable_id("rec", finding_id, RULESET_VERSION)
    finding = {
        "id": finding_id,
        "project_id": result.project_id,
        "snapshot_id": result.snapshot_id,
        "kind": kind,
        "title": title,
        "affected_pages_json": canonical_json(sorted(set(affected_pages))),
        "impact": impact,
        "confidence": confidence,
        "effort": effort,
        "caveats_json": canonical_json(caveats),
        "evidence_ids_json": canonical_json(sorted(evidence_ids)),
        "rule_id": rule_id,
        "rule_version": RULESET_VERSION,
        "calculation": calculation,
        "fingerprint": fingerprint,
    }
    recommendation = {
        "id": recommendation_id,
        "project_id": result.project_id,
        "finding_id": finding_id,
        "title": recommendation_title,
        "rationale": rationale,
        "state": "recommended",
        "provenance_json": canonical_json(
            {
                "input_evidence_ids": sorted(evidence_ids),
                "source_snapshot_id": result.snapshot_id,
                "timestamp": result.captured_at,
                "rule_id": rule_id,
                "rule_version": RULESET_VERSION,
                "calculation": calculation,
                "caveats": caveats,
            }
        ),
    }
    return finding, recommendation


def analyze(result: AdaptedResult, evidence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply deterministic, versioned rules to normalized evidence."""
    if not result.envelope["ok"] or result.provider.get("status") not in {"available", "partial", "degraded"}:
        return [], []
    base_caveats = _base_caveats(result)
    data = result.envelope["data"] if isinstance(result.envelope["data"], dict) else {}
    partial = bool(data.get("failed"))
    caveats = base_caveats + (["The source reported partial failures; priority excludes unavailable rows."] if partial else [])
    findings: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []

    if result.tool == "gsc_search_analytics":
        usable = [item for item in evidence if item["evidence_type"] == "search_performance"]
        for item in usable:
            value = json.loads(item["value_json"])
            labels = value.get("dimensions", {})
            query = str(labels.get("query", "unknown query"))
            page = str(labels.get("page", ""))
            position = value.get("position")
            impressions = value.get("impressions")
            if finite_number(position) and finite_number(impressions) and 5 <= position <= 20 and impressions >= 100:
                impact = "high" if impressions >= 1000 else "medium"
                calculation = f"position {position:g} is within [5,20] and impressions {impressions:g} >= 100"
                pair = _finding_and_recommendation(
                    result,
                    kind="striking_distance",
                    title=f"Striking-distance query: {query}",
                    affected_pages=[page] if page else [],
                    impact=impact,
                    confidence=_confidence(result, partial),
                    effort="medium",
                    caveats=caveats,
                    evidence_ids=[item["id"]],
                    rule_id="gsc.striking-distance",
                    calculation=calculation,
                    recommendation_title=f"Review the page serving “{query}”",
                    rationale="Inspect the recorded query/page evidence and draft a page-specific improvement plan; do not execute changes automatically.",
                )
                findings.append(pair[0])
                recommendations.append(pair[1])

        by_query: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
        for item in usable:
            value = json.loads(item["value_json"])
            labels = value.get("dimensions", {})
            query = labels.get("query")
            page = labels.get("page")
            if query and page:
                by_query[str(query)].append((item, value))
        for query, entries in sorted(by_query.items()):
            pages = sorted({str(value["dimensions"]["page"]) for _, value in entries})
            if len(pages) < 2:
                continue
            ids = [item["id"] for item, _ in entries]
            calculation = f"{len(pages)} distinct pages were observed for the same query in one snapshot"
            pair = _finding_and_recommendation(
                result,
                kind="cannibalization_signal",
                title=f"Multiple pages share query visibility: {query}",
                affected_pages=pages,
                impact="medium",
                confidence=_confidence(result, partial),
                effort="medium",
                caveats=caveats + ["Shared visibility is a routing signal, not proof that consolidation is correct."],
                evidence_ids=ids,
                rule_id="gsc.same-query-pages",
                calculation=calculation,
                recommendation_title=f"Decide page ownership for “{query}”",
                rationale="Choose a primary page, deliberate differentiation, or an explicit accept decision after reviewing all page-level evidence.",
            )
            findings.append(pair[0])
            recommendations.append(pair[1])

    if result.tool == "gsc_compare_periods":
        for item in [entry for entry in evidence if entry["evidence_type"] == "search_performance"]:
            value = json.loads(item["value_json"])
            delta = value.get("delta_clicks")
            previous = value.get("previous_clicks")
            labels = value.get("dimensions", {})
            page = str(labels.get("page", ""))
            if finite_number(delta) and finite_number(previous) and previous > 0:
                rate = delta / previous
                if rate <= -0.2:
                    calculation = f"delta_clicks {delta:g} / previous_clicks {previous:g} = {rate:.4f} <= -0.2000"
                    pair = _finding_and_recommendation(
                        result,
                        kind="content_decay_signal",
                        title=f"Observed click decline: {page or item['subject']}",
                        affected_pages=[page] if page else [],
                        impact="high" if rate <= -0.5 else "medium",
                        confidence=_confidence(result, partial),
                        effort="medium",
                        caveats=caveats + ["Period-over-period association does not establish the cause of the decline."],
                        evidence_ids=[item["id"]],
                        rule_id="gsc.content-decay",
                        calculation=calculation,
                        recommendation_title="Investigate the recorded decline before changing content",
                        rationale="Review query mix, SERP changes, seasonality, and technical signals, then draft a reversible plan.",
                    )
                    findings.append(pair[0])
                    recommendations.append(pair[1])

    if result.tool in {"redirect_chain_audit", "gsc_batch_inspect_urls"}:
        for item in [entry for entry in evidence if entry["evidence_type"] == "migration_observation"]:
            value = json.loads(item["value_json"])
            final_url = value.get("final_url") or value.get("google_canonical")
            expected_url = value.get("expected_url") or value.get("user_canonical")
            chain = value.get("status_chain", [])
            mismatch = bool(final_url and expected_url and final_url != expected_url)
            long_chain = isinstance(chain, list) and len(chain) > 2
            if mismatch or long_chain:
                calculation = f"canonical_mismatch={str(mismatch).lower()}; redirect_hops={max(len(chain) - 1, 0)}"
                pair = _finding_and_recommendation(
                    result,
                    kind="migration_validation",
                    title=f"Migration route needs review: {item['subject']}",
                    affected_pages=[item["subject"]],
                    impact="high" if mismatch else "medium",
                    confidence=_confidence(result),
                    effort="low",
                    caveats=caveats + ["The recorded route is observational evidence; no redirect or canonical change was executed."],
                    evidence_ids=[item["id"]],
                    rule_id="migration.route-integrity",
                    calculation=calculation,
                    recommendation_title="Draft a migration correction with a rollback route",
                    rationale="Confirm the intended destination, preview the mapping, and require explicit approval outside this workbench before execution.",
                )
                findings.append(pair[0])
                recommendations.append(pair[1])

    return findings, recommendations
