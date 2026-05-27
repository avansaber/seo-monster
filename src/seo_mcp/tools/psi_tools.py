"""PageSpeed Insights tool (1).

Shapes the raw PSI payload into a lab + field split: Lighthouse category scores
(0-100), lab Core Web Vitals from the synthetic run, and field Core Web Vitals
from CrUX when available. The PSI client always exists (anonymous endpoint), so
this tool does not return AUTH_MISSING for a missing key; it works with tighter
rate limits instead.
"""

from __future__ import annotations

from typing import Any

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import require_client


_SERVICE = "psi"
_CATEGORIES = ["performance", "accessibility", "best-practices", "seo"]

# Lab audit id -> friendly label.
_LAB_METRICS = (
    ("largest-contentful-paint", "LCP"),
    ("cumulative-layout-shift", "CLS"),
    ("total-blocking-time", "TBT"),
    ("speed-index", "speed_index"),
    ("interactive", "TTI"),
    ("first-contentful-paint", "FCP"),
)

# CrUX field metric id -> (label, is_shift). Shift scores are unitless.
_FIELD_METRICS = (
    ("LARGEST_CONTENTFUL_PAINT_MS", "LCP", False),
    ("INTERACTION_TO_NEXT_PAINT", "INP", False),
    ("CUMULATIVE_LAYOUT_SHIFT_SCORE", "CLS", True),
    ("FIRST_CONTENTFUL_PAINT_MS", "FCP", False),
    ("EXPERIMENTAL_TIME_TO_FIRST_BYTE", "TTFB", False),
)


TOOL_ANALYZE = {
    "name": "psi_analyze",
    "description": (
        "Run PageSpeed Insights on a URL and return Lighthouse category scores, "
        "lab Core Web Vitals (synthetic), and field Core Web Vitals (real-user "
        "CrUX) when available. Defaults to the mobile strategy (Google ranks on "
        "mobile)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Page URL to analyze. Required."},
            "strategy": {"type": "string", "enum": ["mobile", "desktop"], "description": "Defaults to mobile."},
            "categories": {
                "type": "array",
                "items": {"type": "string", "enum": _CATEGORIES},
                "description": "Lighthouse categories to run. Defaults to all four.",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
}


def _lighthouse_scores(lighthouse: dict[str, Any]) -> dict[str, Any]:
    cats = lighthouse.get("categories", {})
    scores: dict[str, Any] = {}
    for cat_id in _CATEGORIES:
        score = cats.get(cat_id, {}).get("score")
        scores[cat_id] = round(score * 100) if score is not None else None
    return scores


def _lab_cwv(lighthouse: dict[str, Any]) -> dict[str, Any]:
    audits = lighthouse.get("audits", {})
    out: dict[str, Any] = {}
    for audit_id, label in _LAB_METRICS:
        out[label] = audits.get(audit_id, {}).get("displayValue")
    return out


def _field_cwv(loading_experience: dict[str, Any]) -> dict[str, Any] | None:
    metrics = loading_experience.get("metrics", {})
    if not metrics:
        return None
    out: dict[str, Any] = {"overall_category": loading_experience.get("overall_category")}
    for metric_id, label, is_shift in _FIELD_METRICS:
        metric = metrics.get(metric_id)
        if not metric:
            continue
        p75 = metric.get("percentile")
        entry = {"category": metric.get("category")}
        if is_shift:
            # CrUX reports CLS percentile as an integer x100; expose the ratio.
            entry["p75"] = round(p75 / 100, 3) if isinstance(p75, (int, float)) else p75
        else:
            entry["p75_ms"] = p75
        out[label] = entry
    return out


def psi_analyze(arguments, config, clients) -> dict[str, Any]:
    client, error = require_client(
        clients,
        "psi",
        _SERVICE,
        remediation="PageSpeed Insights needs no key to work, but a PSI_API_KEY relaxes rate limits.",
    )
    if error:
        return error

    url = arguments.get("url")
    if not url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url is required.", docs_url=DOCS_BASE + "psi")
    strategy = arguments.get("strategy", "mobile")
    categories = arguments.get("categories") or list(_CATEGORIES)

    try:
        data = client.analyze(url, strategy=strategy, categories=categories)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    lighthouse = data.get("lighthouseResult", {})
    field = _field_cwv(data.get("loadingExperience", {}))

    return ok(
        {
            "url": url,
            "strategy": strategy,
            "lighthouse_scores": _lighthouse_scores(lighthouse),
            "lab_core_web_vitals": _lab_cwv(lighthouse),
            "field_core_web_vitals": field,
            "field_data_available": field is not None,
        }
    )


TOOLS = [TOOL_ANALYZE]
HANDLERS = {"psi_analyze": psi_analyze}
