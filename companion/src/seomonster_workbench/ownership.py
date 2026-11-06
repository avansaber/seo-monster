"""Deterministic cross-site visibility conflicts, separate from declared ownership."""

from __future__ import annotations

import json
import unicodedata
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit
from .scope import canonical_page_url, host, resolve_project_site
from .util import canonical_json, finite_number, stable_id, timestamp


OWNERSHIP_RULE_VERSION = "2026-09-09.2"


def normalize_key(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("ownership key must be text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if not normalized or len(normalized) > 500 or any(unicodedata.category(c) == "Cc" for c in normalized):
        raise ValueError("ownership key must be nonempty bounded text without control characters")
    return normalized


def _scope(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Canonical resolved request scope; response values win over aliases/defaults."""
    arguments = snapshot["arguments"]
    data = snapshot["envelope"]["data"]
    data = data if isinstance(data, dict) else {}
    known = {
        "site_url", "start_date", "end_date", "days", "dimensions", "data_state", "search_type", "filters",
        "row_limit", "limit", "start_row",
    }
    return {
        "source_tool": snapshot["tool"],
        "start_date": data.get("start_date", arguments.get("start_date")),
        "end_date": data.get("end_date", arguments.get("end_date")),
        "dimensions": data.get("dimensions") or arguments.get("dimensions") or ["query"],
        "data_state": data.get("data_state", arguments.get("data_state")),
        "search_type": data.get("search_type", arguments.get("search_type", "web")),
        "filters": data.get("filters", arguments.get("filters")),
        "row_limit": data.get("row_limit", arguments.get("row_limit", arguments.get("limit", 1000))),
        "start_row": data.get("start_row", arguments.get("start_row", 0)),
        "other_arguments": {key: value for key, value in arguments.items() if key not in known},
    }


def _property_site_ids(snapshot: dict[str, Any], sites: list[dict[str, Any]]) -> set[str]:
    """Resolve configured scopes covered by a GSC property, including zero-row captures."""
    data = snapshot["envelope"].get("data")
    data = data if isinstance(data, dict) else {}
    value = data.get("site_url") or snapshot["arguments"].get("site_url")
    if not isinstance(value, str):
        return set()
    if value.startswith("sc-domain:"):
        domain = value.removeprefix("sc-domain:").encode("idna").decode().lower()
        return {site["id"] for site in sites if host(site["canonical_url"]) == domain
                or host(site["canonical_url"]).endswith("." + domain)}
    try:
        prop = urlsplit(canonical_page_url(value))
    except ValueError:
        return set()
    matched = set()
    prop_path = prop.path.rstrip("/")
    for site in sites:
        configured = urlsplit(canonical_page_url(site["canonical_url"]))
        site_path = configured.path.rstrip("/")
        same_origin = (prop.scheme, prop.netloc) == (configured.scheme, configured.netloc)
        fully_covered = (not prop_path or site_path == prop_path
                         or site_path.startswith(prop_path + "/"))
        if same_origin and fully_covered:
            matched.add(site["id"])
    return matched


def detect_cross_site_conflicts(store, project_id: str) -> list[dict[str, Any]]:
    """Use only the latest comparable GSC capture per configured site scope."""
    sites = store.rows("sites", project_id)
    snapshots = {row["id"]: store.decode(row) for row in store.rows("snapshots", project_id)}
    candidates = []
    observed_site_ids: dict[str, set[str]] = defaultdict(set)
    for row in store.rows("evidence", project_id):
        item = store.decode(row)
        provenance = item["provenance"]
        value = item.get("value")
        if (item["availability"] != "observed" or item["evidence_type"] != "search_performance"
                or provenance.get("source_tool") != "gsc_search_analytics" or not isinstance(value, dict)):
            continue
        dimensions = value.get("dimensions", {})
        query, page = dimensions.get("query"), dimensions.get("page")
        if not isinstance(query, str) or not isinstance(page, str):
            continue
        page = canonical_page_url(page)
        page_host = host(page)
        try:
            site = resolve_project_site(page, sites)
        except ValueError:
            continue
        snapshot = snapshots.get(item["snapshot_id"])
        if not snapshot:
            raise ValueError("ownership evidence references a missing snapshot")
        scope = _scope(snapshot)
        observed_site_ids[item["snapshot_id"]].add(site["id"])
        candidates.append((item, query, page, page_host, scope, site))

    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in snapshots.values():
        if snapshot["tool"] != "gsc_search_analytics":
            continue
        scope = _scope(snapshot)
        dimensions = scope["dimensions"]
        if not isinstance(dimensions, list) or not {"query", "page"}.issubset(dimensions):
            continue
        covered = _property_site_ids(snapshot, sites) | observed_site_ids.get(snapshot["id"], set())
        for site_id in covered:
            key = (canonical_json(scope), site_id)
            current = latest.get(key)
            if current is None or (timestamp(snapshot["captured_at"]), snapshot["id"]) > (
                timestamp(current["captured_at"]), current["id"]
            ):
                latest[key] = snapshot

    selected_snapshots = {(scope_json, snapshot["id"], site_id)
                          for (scope_json, site_id), snapshot in latest.items()}

    grouped: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for candidate in candidates:
        item, query, _page, _page_host, scope, site = candidate
        scope_json = canonical_json(scope)
        if (scope_json, item["snapshot_id"], site["id"]) in selected_snapshots:
            grouped[(normalize_key(query), scope_json)].append(candidate)

    conflicts = []
    for (normalized, scope_json), entries in sorted(grouped.items()):
        site_ids = {entry[5]["id"] for entry in entries}
        if len(site_ids) < 2:
            continue
        deduped = {entry[0]["id"]: entry for entry in entries}
        entries = [deduped[key] for key in sorted(deduped)]
        evidence_ids = [entry[0]["id"] for entry in entries]
        pages = []
        impressions = []
        caveats = [
            "Cross-site visibility is an observed routing signal; it does not establish cannibalization or intended ownership."
        ]
        all_current = True
        for item, _query, page, _page_host, _scope_value, site in entries:
            value, provenance = item["value"], item["provenance"]
            metric = value.get("impressions")
            if finite_number(metric):
                impressions.append(metric)
            pages.append({
                "url": page, "site_id": site["id"], "site_url": site["canonical_url"],
                "clicks": value.get("clicks") if finite_number(value.get("clicks")) else None,
                "impressions": metric if finite_number(metric) else None,
                "position": value.get("position") if finite_number(value.get("position")) else None,
                "evidence_id": item["id"], "observed_at": item["observed_at"],
            })
            all_current = (all_current and provenance.get("freshness") == "current"
                           and provenance.get("provider", {}).get("status") == "available")
            caveats.extend(provenance.get("caveats", []))
        if len(impressions) != len(entries):
            impact = "unavailable"
            caveats.append("Impact is unavailable because one or more observed pages lack impressions; missing values are not zero.")
            total_text = "unavailable"
        else:
            total = sum(impressions)
            impact = "high" if total >= 1000 else "medium" if total >= 100 else "low"
            total_text = f"{total:g}"
        confidence = "high" if all_current else "low"
        if not all_current:
            caveats.append("At least one source is stale, freshness-unknown, or provider-limited; confidence is reduced.")
        observed_sites = sorted({item["site_url"] for item in pages})
        pages.sort(key=lambda item: (item["site_url"], item["url"], item["evidence_id"]))
        detected_at = max((entry[0]["observed_at"] for entry in entries), key=timestamp)
        conflict_id = stable_id("ownership_conflict", project_id, normalized, json.loads(scope_json), evidence_ids,
                                OWNERSHIP_RULE_VERSION)
        conflicts.append({
            "id": conflict_id, "project_id": project_id, "key_type": "query", "key_text": entries[0][1],
            "normalized_key": normalized, "evidence_ids_json": canonical_json(evidence_ids),
            "observed_pages_json": canonical_json(pages), "observed_sites_json": canonical_json(observed_sites),
            "measurement_scope_json": scope_json, "impact": impact, "confidence": confidence,
            "caveats_json": canonical_json(list(dict.fromkeys(caveats))), "rule_id": "ownership.cross-site-overlap",
            "rule_version": OWNERSHIP_RULE_VERSION,
            "calculation": (f"Observed {len(pages)} pages across {len(observed_sites)} configured sites in one "
                            f"comparable GSC scope; total observed impressions={total_text}."),
            "detected_at": detected_at, "active": 1,
        })
    return conflicts
