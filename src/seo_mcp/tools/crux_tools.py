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


TOOLS = [TOOL]
HANDLERS = {"crux_history": crux_history}
