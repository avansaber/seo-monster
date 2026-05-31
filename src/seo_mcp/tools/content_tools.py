"""Content intelligence tools (Layer 1).

``content_opportunities`` ranks data-grounded content/blog topics from the
property's *own* Search Console data. It fuses four evidence-based signals into
a transparent opportunity score and flags cannibalization. This is the "rules"
layer: SEOMonster contributes the scoring rules and the data fusion; the host
LLM turns the ranked candidates into a brief / outline / draft (later layers).

Design notes (see .private/RULESETS-content-and-audit.md §1 and PLAN Part 6c):

- No new client or dependency: it calls ``GscClient.search_analytics`` three
  times (query rows current window, query rows prior window, query x page rows
  current window) and fuses the results.
- The expected-CTR curve self-calibrates from the site's own data. GSC has no
  ``position`` dimension (position is a metric = the average position), so the
  curve is built by bucketing query rows by ``round(position)`` and taking the
  site's realized CTR (clicks / impressions) per bucket. Sparse buckets fall
  back to a published reference curve. This adapts to each site and sidesteps
  the fact that published CTR curves are unreliable post-AI-Overviews.

Honest bound: this maximizes expected return on effort given demand the site
*already has*. It does not do cold-start keyword research (a site with no
impressions for a topic has nothing to surface), and it does not guarantee a
ranking. The host LLM writes the content; this tool decides what to write about.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Mapping

from ..clients.errors import ApiError
from ..errors import ok
from ._helpers import annotations, missing_site_error, require_client, resolve_site

_SERVICE = "gsc"
_REMEDIATION = (
    "Configure Google auth (OAuth client + token, or a service-account key) "
    "with Search Console access. See README > Auth."
)

# Default scoring weights (RULESETS §1.4). Documented and tunable; never hidden
# from the caller (they are echoed in the response under "weights").
_W_CTR_GAP = 0.40
_W_STRIKING = 0.20
_W_DEMAND = 0.25
_W_MOMENTUM = 0.15

# Effort multipliers (RULESETS §1.3): optimizing one already-ranking page is the
# highest-ROI action; consolidating cannibalizing pages is slightly more work.
_EFFORT_OPTIMIZE = 1.2
_EFFORT_CONSOLIDATE = 1.1

# Position band considered actionable (striking distance). Beyond ~20 the lift
# needed is too large for a CTR/refresh play; top-2 is effectively already won.
_POSITION_CEILING = 20.0

# Reference position -> CTR fallback curve (blended organic; RULESETS §1.1).
# Used ONLY for position buckets where the site has too little of its own data.
# Order-of-magnitude figures from public studies (AWR / Sistrix / Backlinko);
# the site's own calibrated curve always wins when available.
_REFERENCE_CTR = {
    1: 0.300, 2: 0.160, 3: 0.100, 4: 0.075, 5: 0.055,
    6: 0.043, 7: 0.034, 8: 0.029, 9: 0.026, 10: 0.024,
    11: 0.018, 12: 0.016, 13: 0.014, 14: 0.013, 15: 0.012,
    16: 0.011, 17: 0.010, 18: 0.010, 19: 0.009, 20: 0.009,
}
# A bucket needs at least this many queries before we trust its own CTR over
# the reference curve.
_MIN_BUCKET_QUERIES = 5

_ROW_LIMIT = 25000


TOOL_CONTENT_OPPORTUNITIES = {
    "name": "content_opportunities",
    "description": (
        "Rank data-grounded content/blog topics from your own Search Console "
        "data. Fuses CTR-vs-expected gap, striking-distance position, demand "
        "volume, and momentum into a transparent opportunity score, flags "
        "cannibalization, and reports the click upside plus the score's "
        "components. Read-only. It prioritizes demand you already have; it does "
        "not do cold-start keyword research (that needs existing impressions) "
        "and does not write the content or guarantee a ranking."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "site_url": {"type": "string", "description": "Defaults to the configured default site."},
            "days": {"type": "integer", "minimum": 1, "maximum": 240, "description": "Current window length. Momentum compares it to the equally long prior window. Defaults to 28."},
            "count": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Max ranked candidates to return. Defaults to 15."},
            "impressions_min": {"type": "integer", "minimum": 1, "maximum": 1000000, "description": "Drop low-volume noise. Defaults to 100."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def _body(start: str, end: str, dimensions: list[str], data_state: str) -> dict[str, Any]:
    return {
        "startDate": start,
        "endDate": end,
        "dimensions": dimensions,
        "rowLimit": _ROW_LIMIT,
        "type": "web",
        "dataState": data_state,
    }


def _reference_ctr(bucket: int) -> float:
    if bucket < 1:
        bucket = 1
    return _REFERENCE_CTR.get(bucket, 0.005)


def _calibrate_curve(rows: list[dict[str, Any]]) -> dict[int, float]:
    """Site's own CTR by rounded-position bucket: realized clicks/impressions
    per bucket, kept only for buckets with enough queries to trust."""
    agg: dict[int, list[float]] = {}  # bucket -> [clicks, impressions, n_queries]
    for r in rows:
        pos = r.get("position", 0.0) or 0.0
        if pos <= 0:
            continue
        bucket = max(1, round(pos))
        slot = agg.setdefault(bucket, [0.0, 0.0, 0])
        slot[0] += r.get("clicks", 0) or 0
        slot[1] += r.get("impressions", 0) or 0
        slot[2] += 1
    curve: dict[int, float] = {}
    for bucket, (clicks, impressions, n) in agg.items():
        if n >= _MIN_BUCKET_QUERIES and impressions > 0:
            curve[bucket] = clicks / impressions
    return curve


def _expected_ctr(position: float, curve: dict[int, float]) -> float:
    bucket = max(1, round(position))
    if bucket in curve:
        return curve[bucket]
    return _reference_ctr(bucket)


def _striking_weight(position: float) -> float:
    """Bell-ish weight peaking in positions ~5-12 (highest marginal ROI),
    tapering to near-zero at the top (already won) and past position 20."""
    if position <= 1.5:
        return 0.10
    if position <= 5:
        return 0.10 + (position - 1.5) / (5 - 1.5) * 0.90  # 0.10 -> 1.0
    if position <= 12:
        return 1.0
    if position <= _POSITION_CEILING:
        return max(0.0, 1.0 - (position - 12) / (_POSITION_CEILING - 12))  # 1.0 -> 0.0
    return 0.0


def _query_of(row: dict[str, Any]) -> str:
    keys = row.get("keys") or [""]
    return keys[0] if keys else ""


def content_opportunities(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    client, error = require_client(clients, "gsc", _SERVICE, remediation=_REMEDIATION)
    if error:
        return error
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()

    days = int(arguments.get("days", 28))
    count = int(arguments.get("count", 15))
    impressions_min = int(arguments.get("impressions_min", 100))

    today = date.today()
    cur_start = (today - timedelta(days=days)).isoformat()
    cur_end = today.isoformat()
    # Prior window: equally long, immediately preceding (1-day gap to avoid overlap).
    prior_end = (today - timedelta(days=days + 1)).isoformat()
    prior_start = (today - timedelta(days=days * 2 + 1)).isoformat()

    data_state = config.gsc_data_state
    try:
        current = client.search_analytics(site, _body(cur_start, cur_end, ["query"], data_state))
        prior = client.search_analytics(site, _body(prior_start, prior_end, ["query"], data_state))
        query_page = client.search_analytics(site, _body(cur_start, cur_end, ["query", "page"], data_state))
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    current_rows = current.get("rows", [])
    curve = _calibrate_curve(current_rows)

    prior_impressions: dict[str, float] = {}
    for r in prior.get("rows", []):
        prior_impressions[_query_of(r)] = r.get("impressions", 0) or 0

    # Distinct ranking pages per query (impressions > 0) = cannibalization signal.
    pages_per_query: dict[str, int] = {}
    for r in query_page.get("rows", []):
        if (r.get("impressions", 0) or 0) <= 0:
            continue
        q = _query_of(r)
        pages_per_query[q] = pages_per_query.get(q, 0) + 1

    # First pass: compute raw signals for each in-band candidate.
    raw: list[dict[str, Any]] = []
    for r in current_rows:
        impressions = r.get("impressions", 0) or 0
        position = r.get("position", 0.0) or 0.0
        if impressions < impressions_min:
            continue
        if position <= 0 or position > _POSITION_CEILING:
            continue
        query = _query_of(r)
        clicks = r.get("clicks", 0) or 0
        ctr = r.get("ctr", 0.0) or 0.0
        expected = _expected_ctr(position, curve)
        upside = impressions * max(0.0, expected - ctr)
        delta = impressions - prior_impressions.get(query, 0)
        momentum = min(1.0, max(0.0, delta) / impressions) if impressions > 0 else 0.0
        n_pages = pages_per_query.get(query, 1)
        action = "consolidate" if n_pages >= 2 else "optimize"
        effort = _EFFORT_CONSOLIDATE if action == "consolidate" else _EFFORT_OPTIMIZE
        raw.append(
            {
                "query": query,
                "position": position,
                "impressions": impressions,
                "clicks": clicks,
                "ctr": ctr,
                "expected_ctr": expected,
                "upside": upside,
                "striking": _striking_weight(position),
                "demand": math.log10(impressions) if impressions > 0 else 0.0,
                "momentum": momentum,
                "n_pages": n_pages,
                "action": action,
                "effort": effort,
            }
        )

    # Normalize the two volume-scaled signals across the candidate set so the
    # weighted sum is comparable, then score (RULESETS §1.4).
    max_upside = max((c["upside"] for c in raw), default=0.0) or 1.0
    max_demand = max((c["demand"] for c in raw), default=0.0) or 1.0

    candidates: list[dict[str, Any]] = []
    for c in raw:
        upside_norm = c["upside"] / max_upside
        demand_norm = c["demand"] / max_demand
        base = (
            _W_CTR_GAP * upside_norm
            + _W_STRIKING * c["striking"]
            + _W_DEMAND * demand_norm
            + _W_MOMENTUM * c["momentum"]
        )
        score = round(base * c["effort"], 4)  # value_multiplier = 1.0 in v1
        candidates.append(
            {
                "topic": c["query"],
                "target_query": c["query"],
                "action": c["action"],
                "emerging": c["momentum"] >= 0.5,
                "position": round(c["position"], 1),
                "impressions": c["impressions"],
                "clicks": c["clicks"],
                "actual_ctr": round(c["ctr"], 4),
                "expected_ctr": round(c["expected_ctr"], 4),
                "click_upside": round(c["upside"], 1),
                "ranking_pages": c["n_pages"],
                "score": score,
                "components": {
                    "ctr_gap_upside_norm": round(upside_norm, 4),
                    "striking_distance": round(c["striking"], 3),
                    "demand_norm": round(demand_norm, 4),
                    "momentum": round(c["momentum"], 3),
                    "effort_multiplier": c["effort"],
                },
            }
        )

    candidates.sort(key=lambda c: c["score"], reverse=True)
    top = candidates[:count]

    return ok(
        {
            "site_url": site,
            "days": days,
            "window": {"current": [cur_start, cur_end], "prior": [prior_start, prior_end]},
            "candidate_count": len(top),
            "total_in_band": len(candidates),
            "candidates": top,
            "ctr_curve_calibrated": {str(b): round(v, 4) for b, v in sorted(curve.items())},
            "weights": {
                "ctr_gap": _W_CTR_GAP,
                "striking_distance": _W_STRIKING,
                "demand": _W_DEMAND,
                "momentum": _W_MOMENTUM,
            },
            "filters_applied": {
                "impressions_min": impressions_min,
                "position_ceiling": _POSITION_CEILING,
                "value_multiplier": 1.0,
            },
            "notes": [
                "Scored from your own Search Console data; ranks by expected "
                "return on effort, not by guaranteed position.",
                "The expected-CTR curve is self-calibrated from your buckets "
                "with at least 5 queries; a reference curve fills sparse buckets.",
                "v1 scope: GA4 conversion-value weighting and new-vs-refresh "
                "page-intent analysis are not yet applied. Cannibalization is "
                "flagged via the count of ranking pages per query.",
            ],
        }
    )


TOOLS = [TOOL_CONTENT_OPPORTUNITIES]
HANDLERS = {"content_opportunities": content_opportunities}
