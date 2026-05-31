"""crux_history (1). Wraps the CrUX History API to return the last 25
weekly collection periods of Core Web Vitals at p75. Complements ``psi_analyze``
(single-window Lighthouse + CrUX) by showing the trend, which is what most
SEO conversations actually want: "is this getting better or worse?".

Accepts either ``url`` (page-level) or ``origin`` (host-level). Returns a
flat structure: ``periods`` (one per collection week) and ``metrics`` keyed
by metric name, each with a parallel ``p75`` list aligned with ``periods``.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import ANNOT_READ, require_client


_SERVICE = "crux"
_REMEDIATION = (
    "PSI_API_KEY is reused for CrUX History. Without it the call still works "
    "but is rate-limited; set PSI_API_KEY for stable usage."
)


def _require_crux(clients: Mapping[str, Any]):
    return require_client(clients, "crux", _SERVICE, remediation=_REMEDIATION)


TOOL = {
    "name": "crux_history",
    "description": (
        "Return the last 25 weekly collection periods of Core Web Vitals "
        "at p75 (LCP, INP, CLS, etc.) for a URL or origin via the Chrome UX "
        "Report History API. Complements psi_analyze with the trend axis."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Page-level URL. Use this OR origin."},
            "origin": {"type": "string", "description": "Origin (scheme + host). Use this OR url."},
            "form_factor": {
                "type": "string",
                "enum": ["PHONE", "DESKTOP", "TABLET", "ALL_FORM_FACTORS"],
                "description": "Form factor; omit for the combined view.",
            },
            "metrics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Subset of CrUX metric names (snake_case). Omit for all defaults.",
            },
        },
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def crux_history(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_crux(clients)
    if error:
        return error
    url = arguments.get("url")
    origin = arguments.get("origin")
    if not (url or origin):
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "Pass either url or origin.", docs_url=DOCS_BASE + "crux")
    if url and origin:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "Pass url OR origin, not both.")
    try:
        response = client.query(
            url=url,
            origin=origin,
            form_factor=arguments.get("form_factor"),
            metrics=arguments.get("metrics"),
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    if response.get("no_data"):
        return ok({"key": url or origin, "periods": [], "metrics": {}, "no_data": True})
    record = response.get("record") or {}
    periods = _format_periods(record.get("collectionPeriods") or [])
    metrics_out = _format_metrics(record.get("metrics") or {})
    return ok({
        "key": url or origin,
        "form_factor": arguments.get("form_factor"),
        "periods": periods,
        "metrics": metrics_out,
        "no_data": False,
    })


def _format_periods(periods: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for p in periods:
        first = p.get("firstDate") or {}
        last = p.get("lastDate") or {}
        out.append({
            "first_date": _iso(first),
            "last_date": _iso(last),
        })
    return out


def _iso(date_obj: dict[str, Any]) -> str:
    y, m, d = date_obj.get("year"), date_obj.get("month"), date_obj.get("day")
    if y and m and d:
        return f"{y:04d}-{m:02d}-{d:02d}"
    return ""


def _format_metrics(metrics: dict[str, Any]) -> dict[str, dict[str, list[float | None]]]:
    out: dict[str, dict[str, list[float | None]]] = {}
    for name, payload in metrics.items():
        timeseries = (payload or {}).get("percentilesTimeseries") or {}
        # CrUX returns either string percentile values or nulls when a period
        # has no data; coerce to float | None so the AI gets a uniform shape.
        out[name] = {"p75": [_as_float(v) for v in (timeseries.get("p75s") or [])]}
    return out


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --- crux_snapshot ---------------------------------------------------------

# CrUX metric name -> (label, good_max, poor_min, is_shift). p75 <= good_max is
# GOOD, p75 >= poor_min is POOR, between is NEEDS_IMPROVEMENT. Thresholds are
# Google's published Core Web Vitals boundaries (ms, except CLS which is a
# unitless shift score). queryRecord returns the p75 directly; CrUX does not
# emit a per-metric category label, so we derive it from these thresholds.
_SNAPSHOT_METRICS = (
    ("largest_contentful_paint", "LCP", 2500, 4000, False),
    ("interaction_to_next_paint", "INP", 200, 500, False),
    ("cumulative_layout_shift", "CLS", 0.1, 0.25, True),
    ("first_contentful_paint", "FCP", 1800, 3000, False),
    ("experimental_time_to_first_byte", "TTFB", 800, 1800, False),
)

# Core Web Vitals that decide the overall pass/fail (LCP, INP, CLS).
_CORE_LABELS = ("LCP", "INP", "CLS")

_SNAPSHOT_METRIC_NAMES = [name for name, *_ in _SNAPSHOT_METRICS]


TOOL_SNAPSHOT = {
    "name": "crux_snapshot",
    "description": (
        "Return the CURRENT (latest 28-day) p75 Core Web Vitals snapshot (LCP, "
        "INP, CLS, FCP, TTFB) for a URL or origin via the Chrome UX Report "
        "queryRecord API, with each metric's GOOD/NEEDS_IMPROVEMENT/POOR category "
        "and an overall verdict. Use crux_history for the 25-week trend. Small "
        "pages/origins legitimately have no field data; that returns no_data."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Page-level URL. Use this OR origin."},
            "origin": {"type": "string", "description": "Origin (scheme + host). Use this OR url."},
            "form_factor": {
                "type": "string",
                "enum": ["PHONE", "DESKTOP", "TABLET", "ALL_FORM_FACTORS"],
                "description": "Form factor; omit for the combined view.",
            },
        },
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def _categorize(p75: float | None, good_max: float, poor_min: float) -> str | None:
    if p75 is None:
        return None
    if p75 <= good_max:
        return "GOOD"
    if p75 >= poor_min:
        return "POOR"
    return "NEEDS_IMPROVEMENT"


def _snapshot_metrics(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, label, good_max, poor_min, is_shift in _SNAPSHOT_METRICS:
        payload = metrics.get(name)
        if not payload:
            continue
        percentiles = (payload or {}).get("percentiles") or {}
        p75 = _as_float(percentiles.get("p75"))
        entry: dict[str, Any] = {"category": _categorize(p75, good_max, poor_min)}
        if is_shift:
            entry["p75"] = p75
        else:
            entry["p75_ms"] = p75
        out[label] = entry
    return out


def _overall_category(metrics_out: dict[str, dict[str, Any]]) -> str | None:
    """Worst category across the three core vitals. Google's "passing CWV"
    rule is all three GOOD; we surface the worst so the AI sees the verdict."""
    rank = {"GOOD": 0, "NEEDS_IMPROVEMENT": 1, "POOR": 2}
    seen = [metrics_out[label]["category"] for label in _CORE_LABELS if label in metrics_out]
    seen = [c for c in seen if c is not None]
    if not seen:
        return None
    return max(seen, key=lambda c: rank.get(c, 0))


def crux_snapshot(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_crux(clients)
    if error:
        return error
    url = arguments.get("url")
    origin = arguments.get("origin")
    if not (url or origin):
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "Pass either url or origin.", docs_url=DOCS_BASE + "crux")
    if url and origin:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "Pass url OR origin, not both.")
    try:
        response = client.query_current(
            url=url,
            origin=origin,
            form_factor=arguments.get("form_factor"),
            metrics=_SNAPSHOT_METRIC_NAMES,
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    if response.get("no_data"):
        return ok({"key": url or origin, "metrics": {}, "overall_category": None, "no_data": True})
    record = response.get("record") or {}
    metrics_out = _snapshot_metrics(record.get("metrics") or {})
    return ok({
        "key": url or origin,
        "form_factor": arguments.get("form_factor"),
        "metrics": metrics_out,
        "overall_category": _overall_category(metrics_out),
        "no_data": False,
    })


TOOLS = [TOOL, TOOL_SNAPSHOT]
HANDLERS = {"crux_history": crux_history, "crux_snapshot": crux_snapshot}
