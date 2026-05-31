"""Google Analytics 4 tools (4).

A generic ``ga4_run_report`` plus three SEO-focused convenience wrappers that
preset dimensions/metrics/filters so the AI does not have to assemble GA4 API
payloads by hand (which it gets wrong). All are read-only.

GA4 auth shares the Google credential resolver (the analytics.readonly scope).
The property id comes from the argument or the configured default; both bare
("123456789") and prefixed ("properties/123456789") forms are accepted.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..clients.errors import ApiError
from ..clients.ga4 import normalize_property_id
from ..config import Config
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations, require_client


_SERVICE = "ga4"
_REMEDIATION = (
    "Configure Google auth (OAuth client + token, or a service-account key) and "
    "grant it Viewer access on the GA4 property. See README > Auth."
)
_ORGANIC_CHANNEL = "Organic Search"


def _require(clients: Mapping[str, Any]):
    return require_client(clients, "ga4", _SERVICE, remediation=_REMEDIATION)


def _resolve_property(arguments: Mapping[str, Any], config: Config) -> str | None:
    return normalize_property_id(arguments.get("property_id") or config.ga4_property_id)


def _missing_property_error() -> dict[str, Any]:
    return err(
        ErrorCode.INVALID_INPUT,
        _SERVICE,
        "No GA4 property specified.",
        remediation="Pass property_id (e.g. 'properties/123456789' or '123456789'), or set SEO_MCP_GA4_PROPERTY_ID.",
        docs_url=DOCS_BASE + "configuration",
    )


def _organic_filter() -> dict[str, Any]:
    return {"field": "sessionDefaultChannelGroup", "value": _ORGANIC_CHANNEL, "match_type": "EXACT"}


def _days_window(arguments: Mapping[str, Any], default_days: int = 28) -> tuple[str, str, int]:
    days = int(arguments.get("days", default_days))
    return f"{days}daysAgo", "today", days


# --- ga4_run_report -------------------------------------------------------

TOOL_RUN_REPORT = {
    "name": "ga4_run_report",
    "description": (
        "Run a GA4 report (Analytics Data API runReport) with arbitrary "
        "dimensions, metrics, a date range, an optional dimension filter, and "
        "ordering. The workhorse GA4 tool. Dates accept ISO (YYYY-MM-DD) or GA4 "
        "relatives like '28daysAgo' and 'today'."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "property_id": {"type": "string", "description": "GA4 property ('properties/123' or '123'). Defaults to the configured property."},
            "start_date": {"type": "string", "description": "ISO date or GA4 relative. Defaults to 28daysAgo. Wins over `days` when both are set."},
            "end_date": {"type": "string", "description": "ISO date or 'today'. Defaults to today."},
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "Convenience alias: derives end='today', start='Ndaysago'. Ignored when start_date is set explicitly."},
            "dimensions": {"type": "array", "items": {"type": "string"}, "description": "GA4 dimension API names. Defaults to [\"date\"]."},
            "metrics": {"type": "array", "items": {"type": "string"}, "description": "GA4 metric API names. Defaults to [\"sessions\"]."},
            "row_limit": {"type": "integer", "minimum": 1, "maximum": 100000, "description": "Defaults to 1000."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100000, "description": "Alias for row_limit. row_limit wins when both are set."},
            "dimension_filter": {
                "type": "object",
                "description": "Optional filter. Simple form: {\"field\": \"sessionDefaultChannelGroup\", \"value\": \"Organic Search\", \"match_type\": \"EXACT\"} or {\"field\": ..., \"in_list\": [...]}.",
            },
            "order_by": {
                "type": "object",
                "description": "Optional ordering: {\"metric\": \"sessions\", \"desc\": true} or {\"dimension\": \"date\", \"desc\": false}.",
            },
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def _report(
    client: Any,
    prop: str,
    *,
    dimensions: list[str],
    metrics: list[str],
    start_date: str,
    end_date: str,
    row_limit: int,
    dimension_filter: dict[str, Any] | None = None,
    order_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = client.run_report(
        prop,
        dimensions=dimensions,
        metrics=metrics,
        start_date=start_date,
        end_date=end_date,
        row_limit=row_limit,
        dimension_filter=dimension_filter,
        order_by=order_by,
    )
    return {
        "property_id": prop,
        "start_date": start_date,
        "end_date": end_date,
        "dimensions": dimensions,
        "metrics": metrics,
        **report,
    }


def ga4_run_report(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    prop = _resolve_property(arguments, config)
    if not prop:
        return _missing_property_error()

    dimensions = arguments.get("dimensions") or ["date"]
    metrics = arguments.get("metrics") or ["sessions"]
    # Date resolution: explicit start_date wins. Otherwise, if `days` was given
    # as a convenience alias, derive start = "Ndaysago" (GA4 relative form).
    days = arguments.get("days")
    if arguments.get("start_date"):
        start_date = arguments["start_date"]
    elif days is not None:
        start_date = f"{int(days)}daysAgo"
    else:
        start_date = "28daysAgo"
    end_date = arguments.get("end_date") or "today"
    # row_limit wins; `limit` is the friendlier alias.
    row_limit = int(arguments.get("row_limit", arguments.get("limit", 1000)))

    try:
        data = _report(
            client, prop,
            dimensions=dimensions, metrics=metrics,
            start_date=start_date, end_date=end_date, row_limit=row_limit,
            dimension_filter=arguments.get("dimension_filter"),
            order_by=arguments.get("order_by"),
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok(data)


# --- ga4_top_landing_pages ------------------------------------------------

TOOL_TOP_LANDING_PAGES = {
    "name": "ga4_top_landing_pages",
    "description": (
        "Top landing pages by sessions over the last N days, with engagement "
        "rate and conversions. Filtered to organic search by default (the SEO "
        "view). Convenience wrapper over ga4_run_report."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "property_id": {"type": "string", "description": "Defaults to the configured property."},
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "Lookback window. Defaults to 28."},
            "organic_only": {"type": "boolean", "description": "Limit to Organic Search traffic. Defaults to true."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100000, "description": "Max rows. Defaults to 50."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def ga4_top_landing_pages(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    prop = _resolve_property(arguments, config)
    if not prop:
        return _missing_property_error()
    start_date, end_date, days = _days_window(arguments)
    limit = int(arguments.get("limit", 50))
    organic_only = arguments.get("organic_only", True)

    try:
        data = _report(
            client, prop,
            dimensions=["landingPagePlusQueryString"],
            metrics=["sessions", "engagementRate", "conversions"],
            start_date=start_date, end_date=end_date, row_limit=limit,
            dimension_filter=_organic_filter() if organic_only else None,
            order_by={"metric": "sessions", "desc": True},
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    data["days"] = days
    data["organic_only"] = bool(organic_only)
    return ok(data)


# --- ga4_traffic_by_channel -----------------------------------------------

TOOL_TRAFFIC_BY_CHANNEL = {
    "name": "ga4_traffic_by_channel",
    "description": (
        "Sessions, engaged sessions, and conversions broken down by default "
        "channel group over the last N days. Separates organic from paid / "
        "referral / direct at a glance. Convenience wrapper over ga4_run_report."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "property_id": {"type": "string", "description": "Defaults to the configured property."},
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "Lookback window. Defaults to 28."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Max rows. Defaults to 20."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def ga4_traffic_by_channel(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    prop = _resolve_property(arguments, config)
    if not prop:
        return _missing_property_error()
    start_date, end_date, days = _days_window(arguments)
    limit = int(arguments.get("limit", 20))

    try:
        data = _report(
            client, prop,
            dimensions=["sessionDefaultChannelGroup"],
            metrics=["sessions", "engagedSessions", "conversions"],
            start_date=start_date, end_date=end_date, row_limit=limit,
            order_by={"metric": "sessions", "desc": True},
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    data["days"] = days
    return ok(data)


# --- ga4_organic_search_overview ------------------------------------------

TOOL_ORGANIC_OVERVIEW = {
    "name": "ga4_organic_search_overview",
    "description": (
        "Organic-search health over the last N days: window totals (sessions, "
        "engaged sessions, engagement rate, average session duration, "
        "conversions) plus a day-by-day trend of the same metrics. Two GA4 "
        "reports under the hood."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "property_id": {"type": "string", "description": "Defaults to the configured property."},
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "Lookback window. Defaults to 28."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}

_OVERVIEW_METRICS = ["sessions", "engagedSessions", "engagementRate", "averageSessionDuration", "conversions"]


def ga4_organic_search_overview(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    prop = _resolve_property(arguments, config)
    if not prop:
        return _missing_property_error()
    start_date, end_date, days = _days_window(arguments)
    organic = _organic_filter()

    try:
        totals_report = client.run_report(
            prop, dimensions=[], metrics=_OVERVIEW_METRICS,
            start_date=start_date, end_date=end_date, row_limit=1,
            dimension_filter=organic,
        )
        trend_report = client.run_report(
            prop, dimensions=["date"], metrics=_OVERVIEW_METRICS,
            start_date=start_date, end_date=end_date, row_limit=days + 1,
            dimension_filter=organic, order_by={"dimension": "date", "desc": False},
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    totals_row = totals_report["rows"][0]["metrics"] if totals_report["rows"] else []
    totals = dict(zip(_OVERVIEW_METRICS, totals_row))
    trend = [
        {"date": r["dimensions"][0] if r["dimensions"] else None, **dict(zip(_OVERVIEW_METRICS, r["metrics"]))}
        for r in trend_report["rows"]
    ]
    return ok(
        {
            "property_id": prop,
            "days": days,
            "start_date": start_date,
            "end_date": end_date,
            "channel": _ORGANIC_CHANNEL,
            "totals": totals,
            "trend": trend,
        }
    )


# --- ga4_setup_audit (Admin API; reads property config for SEO readiness) ---
#
# Uses the analyticsadmin v1beta REST client (no new dependency). The rules are
# a pure function over the normalized config so they test without any client.
# RULESETS §2; v1beta subset. The v1alpha checks (enhanced measurement, site
# search, Google Signals) are deferred and listed in the response.

# event_data_retention values >= 14 months (anything but the 2-month default).
_ACCEPTABLE_RETENTION = {
    "FOURTEEN_MONTHS", "TWENTY_SIX_MONTHS", "THIRTY_EIGHT_MONTHS", "FIFTY_MONTHS",
}


def _finding(rule_id: str, severity: str, observed: str, expected: str, why: str, benign: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "observed": observed,
        "expected": expected,
        "why": why,
        "benign_exception": benign,
    }


def _audit_setup(cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Apply the GA4-for-SEO setup rules to a normalized config dict. Pure: no
    client, no I/O. Every non-info finding carries why + benign_exception so the
    audit grades rather than scolds (RULESETS §0 / §2)."""
    findings: list[dict[str, Any]] = []

    if cfg.get("web_stream_count", 0) == 0:
        findings.append(_finding(
            "ga4.web_stream", "critical",
            f"{cfg.get('stream_count', 0)} streams, 0 web", ">= 1 web data stream",
            "No web data stream means the property captures no website measurement at all.",
            "App-only property (out of SEO scope).",
        ))

    if cfg.get("key_event_count", 0) == 0:
        findings.append(_finding(
            "ga4.key_events", "high",
            "0 key events", ">= 1 key event / conversion",
            "Without key events you cannot measure organic outcomes, which is what makes content and SEO work provable.",
            "Brand-new property, or a purely informational site with no conversion concept.",
        ))

    retention = cfg.get("data_retention")
    if retention == "TWO_MONTHS":
        findings.append(_finding(
            "ga4.data_retention", "medium",
            "TWO_MONTHS (default)", "14 months or longer",
            "The 2-month default discards the history needed for year-over-year SEO trend analysis.",
            "Intentionally short for a privacy or compliance reason.",
        ))
    elif retention is not None and retention not in _ACCEPTABLE_RETENTION:
        findings.append(_finding(
            "ga4.data_retention", "info",
            str(retention), "14 months or longer",
            "Could not classify the data-retention setting against the known values.",
            "New or 360-only retention value; verify it is at least 14 months.",
        ))

    if cfg.get("custom_dimension_count", 0) == 0:
        findings.append(_finding(
            "ga4.content_grouping", "low",
            "no custom dimensions", "a content-group custom dimension",
            "Content-group custom dimensions let you analyze organic performance by content type.",
            "Optional; many sites do fine without it.",
        ))

    return findings


TOOL_SETUP_AUDIT = {
    "name": "ga4_setup_audit",
    "description": (
        "Audit a GA4 property's configuration for SEO-measurement readiness "
        "(read-only): is a web data stream present, are key events / "
        "conversions defined, is data retention long enough for year-over-year "
        "analysis, and are content-group custom dimensions set. Findings are "
        "severity-graded with the reason and the benign exception for each. "
        "Answers 'can this property actually measure my organic outcomes?' It "
        "checks hygiene, not whether your events are the right business events."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "property_id": {
                "type": "string",
                "description": "GA4 property: 'properties/123456789' or bare '123456789'. Defaults to the configured default.",
            },
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def ga4_setup_audit(arguments, config, clients) -> dict[str, Any]:
    client, error = require_client(clients, "ga4_admin", _SERVICE, remediation=_REMEDIATION)
    if error:
        return error
    prop = normalize_property_id(arguments.get("property_id") or config.ga4_property_id)
    if not prop:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            "No GA4 property specified.",
            remediation="Pass property_id (e.g. 'properties/123456789') or set SEO_MCP_GA4_PROPERTY_ID.",
            docs_url=DOCS_BASE + "configuration",
        )
    try:
        cfg = client.get_setup(prop)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    findings = _audit_setup(cfg)
    by_severity: dict[str, int] = {}
    for f in findings:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1
    if any(f["severity"] in ("critical", "high") for f in findings):
        verdict = "issues"
    elif findings:
        verdict = "review"
    else:
        verdict = "clean"

    return ok(
        {
            "property_id": prop,
            "config": cfg,
            "findings": findings,
            "summary": {"by_severity": by_severity, "verdict": verdict},
            "deferred_checks": [
                "enhanced_measurement (GA4 Admin v1alpha)",
                "site_search (part of enhanced measurement; v1alpha)",
                "google_signals (v1alpha)",
            ],
            "notes": [
                "Read-only: no configuration is changed.",
                "Audits measurement hygiene for SEO. It cannot tell you whether "
                "your key events are the RIGHT business events; that is your context.",
            ],
        }
    )


# --- registry -------------------------------------------------------------

TOOLS = [
    TOOL_RUN_REPORT,
    TOOL_TOP_LANDING_PAGES,
    TOOL_TRAFFIC_BY_CHANNEL,
    TOOL_ORGANIC_OVERVIEW,
    TOOL_SETUP_AUDIT,
]

HANDLERS = {
    "ga4_run_report": ga4_run_report,
    "ga4_top_landing_pages": ga4_top_landing_pages,
    "ga4_traffic_by_channel": ga4_traffic_by_channel,
    "ga4_organic_search_overview": ga4_organic_search_overview,
    "ga4_setup_audit": ga4_setup_audit,
}
