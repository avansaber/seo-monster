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
from ._helpers import annotations, require_client


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

# Google is deprecating field (CrUX) data in the PSI API: `loadingExperience`
# can return null even for high-traffic URLs. We surface it when present but do
# not depend on it (field_data_available stays honest). The durable source of
# field Core Web Vitals is the dedicated CrUX API, exposed by crux_snapshot
# (current p75) and crux_history (trend). See PLAN Part 6b A2.
_FIELD_DATA_NOTE = (
    "Field (CrUX) data here comes from PageSpeed Insights' loadingExperience, "
    "which Google is deprecating in the PSI API (it can be null even for "
    "high-traffic URLs). For durable field Core Web Vitals use crux_snapshot "
    "(current p75) or crux_history (trend)."
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
    "annotations": annotations(read=True),
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
            "field_data_note": _FIELD_DATA_NOTE,
        }
    )


# --- psi_opportunities -----------------------------------------------------

# Lighthouse SEO-category audit id -> severity, per RULESETS-content-and-audit
# §4 (PSI-SEO audit ruleset). Audits not listed here fall back to "info" so a
# newly-added Lighthouse SEO audit still surfaces rather than being dropped.
_SEO_AUDIT_SEVERITY = {
    "is-crawlable": "critical",
    "http-status-code": "critical",
    "viewport": "critical",
    "document-title": "high",
    "meta-description": "high",
    "hreflang": "high",
    "canonical": "high",
    "image-alt": "medium",
    "link-text": "medium",
    "crawlable-anchors": "medium",
    "font-size": "low",
    "tap-targets": "low",
}

_SEO_AUDIT_NOTE = (
    "The Lighthouse SEO category is an on-page-basics checklist, not a ranking "
    "predictor. Passing these removes blockers; it does not cause ranking. For "
    "page-level depth prefer the dedicated inspect_meta / check_canonical / "
    "robots_txt_validate tools; use this for the at-a-glance category cross-check."
)


TOOL_OPPORTUNITIES = {
    "name": "psi_opportunities",
    "description": (
        "Run PageSpeed Insights and return the actionable Lighthouse opportunity "
        "audits (estimated load-time savings) plus the Lighthouse SEO-category "
        "audits graded by severity (critical/high/medium/low). Lab data only; "
        "does not use field/CrUX data (use crux_snapshot / crux_history for "
        "real-user metrics). Defaults to the mobile strategy."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Page URL to analyze. Required."},
            "strategy": {"type": "string", "enum": ["mobile", "desktop"], "description": "Defaults to mobile."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def _opportunity_audits(lighthouse: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the actionable perf opportunities (details.type == 'opportunity')."""
    audits = lighthouse.get("audits", {})
    out: list[dict[str, Any]] = []
    for audit_id, audit in audits.items():
        if not isinstance(audit, dict):
            continue
        details = audit.get("details") or {}
        if details.get("type") != "opportunity":
            continue
        out.append(
            {
                "id": audit_id,
                "title": audit.get("title"),
                "display_value": audit.get("displayValue"),
                "overall_savings_ms": details.get("overallSavingsMs"),
            }
        )
    # Heaviest savings first; missing savings sort last.
    out.sort(key=lambda a: a.get("overall_savings_ms") or 0, reverse=True)
    return out


def _seo_audits(lighthouse: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk the SEO category's auditRefs, resolve each to its audit, and grade."""
    categories = lighthouse.get("categories", {})
    audits = lighthouse.get("audits", {})
    refs = (categories.get("seo") or {}).get("auditRefs") or []
    out: list[dict[str, Any]] = []
    for ref in refs:
        audit_id = ref.get("id")
        if not audit_id:
            continue
        audit = audits.get(audit_id) or {}
        # Manual / informative audits have no numeric score; skip the pure
        # group headers but keep anything Lighthouse actually scored.
        score = audit.get("score")
        score_mode = audit.get("scoreDisplayMode")
        if score is None and score_mode in ("manual", "notApplicable", "informative"):
            continue
        out.append(
            {
                "id": audit_id,
                "title": audit.get("title"),
                "score": score,
                "passed": score == 1 if score is not None else None,
                "severity": _SEO_AUDIT_SEVERITY.get(audit_id, "info"),
            }
        )
    return out


def psi_opportunities(arguments, config, clients) -> dict[str, Any]:
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

    try:
        data = client.analyze(url, strategy=strategy, categories=["performance", "seo"])
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    lighthouse = data.get("lighthouseResult", {})

    return ok(
        {
            "url": url,
            "strategy": strategy,
            "opportunities": _opportunity_audits(lighthouse),
            "seo_audits": _seo_audits(lighthouse),
            "notes": [_SEO_AUDIT_NOTE],
        }
    )


TOOLS = [TOOL_ANALYZE, TOOL_OPPORTUNITIES]
HANDLERS = {"psi_analyze": psi_analyze, "psi_opportunities": psi_opportunities}
