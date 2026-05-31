"""Google Search Console tools (10).

Each tool resolves the GSC client (AUTH_MISSING if unconfigured), validates and
defaults its arguments, calls the client, and shapes the response into the
standard envelope. Client failures arrive as ``ApiError`` and are converted with
``to_envelope``. Tools never raise to the transport.

Sitemap submit and indexing request are un-gated (available by default): they
are routine SEO tasks. Only Cloudflare cache purge sits behind the destructive
flag (see cf_tools in a later phase).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping

from ..errors import DOCS_BASE, ErrorCode, err, ok
from ..clients.errors import ApiError
from ._helpers import annotations, missing_site_error, require_client, resolve_site


_SERVICE = "gsc"
_REMEDIATION = (
    "Configure Google auth (OAuth client + token, or a service-account key) and "
    "ensure the account has access to the property. See README > Auth."
)

_DIMENSIONS = ["query", "page", "country", "device", "searchAppearance", "date"]
_SEARCH_TYPES = ["web", "image", "video", "news", "discover", "googleNews"]
_FILTER_OPERATORS = [
    "equals",
    "notEquals",
    "contains",
    "notContains",
    "includingRegex",
    "excludingRegex",
]
_MAX_INSPECT_BATCH = 25
_MAX_INDEXING_BATCH = 100


# --- helpers --------------------------------------------------------------


def _require(clients: Mapping[str, Any]):
    return require_client(clients, "gsc", _SERVICE, remediation=_REMEDIATION)


def _today() -> date:
    return date.today()


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "keys": row.get("keys", []),
        "clicks": row.get("clicks", 0),
        "impressions": row.get("impressions", 0),
        "ctr": row.get("ctr", 0.0),
        "position": row.get("position", 0.0),
    }


def _build_filter_groups(filters: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not filters:
        return []
    return [
        {
            "groupType": "and",
            "filters": [
                {
                    "dimension": f["dimension"],
                    "operator": f["operator"],
                    "expression": f["expression"],
                }
                for f in filters
            ],
        }
    ]


def _run_query(
    client: Any,
    site: str,
    *,
    start: str,
    end: str,
    dimensions: list[str],
    row_limit: int,
    start_row: int = 0,
    search_type: str = "web",
    data_state: str = "all",
    filters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "startDate": start,
        "endDate": end,
        "dimensions": dimensions,
        "rowLimit": row_limit,
        "startRow": start_row,
        "type": search_type,
        "dataState": data_state,
    }
    groups = _build_filter_groups(filters)
    if groups:
        body["dimensionFilterGroups"] = groups
    resp = client.search_analytics(site, body)
    return resp


# --- gsc_list_properties --------------------------------------------------

TOOL_LIST_PROPERTIES = {
    "name": "gsc_list_properties",
    "description": (
        "List every Search Console property the configured credentials can see, "
        "with each property's permission level. Use this to discover valid "
        "site_url values."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": annotations(read=True),
}


def gsc_list_properties(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    try:
        resp = client.list_sites()
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    properties = [
        {
            "site_url": e.get("siteUrl"),
            "permission_level": e.get("permissionLevel"),
            # Derived: which permission levels carry write capability. Lets the
            # AI host pre-screen which properties support gsc_submit_sitemap
            # and gsc_request_indexing instead of failing on the first call.
            "writable": e.get("permissionLevel") in {"siteOwner", "siteFullUser"},
        }
        for e in resp.get("siteEntry", [])
    ]
    return ok({"properties": properties, "count": len(properties)})


# --- gsc_search_analytics -------------------------------------------------

TOOL_SEARCH_ANALYTICS = {
    "name": "gsc_search_analytics",
    "description": (
        "Query Search Console search analytics (clicks, impressions, CTR, "
        "position) for a property, with dimensions, a date range, optional "
        "dimension filters, and data_state. The workhorse GSC tool."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "site_url": {"type": "string", "description": "Property to query. Defaults to the configured default site."},
            "start_date": {"type": "string", "description": "ISO date YYYY-MM-DD. Defaults to 28 days ago. Wins over `days` when both are set."},
            "end_date": {"type": "string", "description": "ISO date YYYY-MM-DD. Defaults to today."},
            "days": {"type": "integer", "minimum": 1, "maximum": 480, "description": "Lookback window in days. Convenience alias: derives end=today, start=today-days. Ignored when start_date is set explicitly."},
            "dimensions": {
                "type": "array",
                "items": {"type": "string", "enum": _DIMENSIONS},
                "description": "Defaults to [\"query\"].",
            },
            "row_limit": {"type": "integer", "minimum": 1, "maximum": 25000, "description": "Defaults to 1000."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25000, "description": "Alias for row_limit. row_limit wins when both are set."},
            "start_row": {"type": "integer", "minimum": 0, "description": "Pagination offset. Defaults to 0."},
            "search_type": {"type": "string", "enum": _SEARCH_TYPES, "description": "Defaults to web."},
            "data_state": {
                "type": "string",
                "enum": ["all", "final"],
                "description": "all matches the dashboard (fresh, partial data); final lags 2-3 days. Defaults to the configured value.",
            },
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "dimension": {"type": "string"},
                        "operator": {"type": "string", "enum": _FILTER_OPERATORS},
                        "expression": {"type": "string"},
                    },
                    "required": ["dimension", "operator", "expression"],
                    "additionalProperties": False,
                },
                "description": "Optional dimension filters, ANDed together.",
            },
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def gsc_search_analytics(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()

    # Date resolution: explicit start_date/end_date win. Otherwise, if `days`
    # was given as a convenience alias, derive end=today and start=today-days.
    # Otherwise fall back to the 28-day default.
    days = arguments.get("days")
    end = arguments.get("end_date") or _today().isoformat()
    if arguments.get("start_date"):
        start = arguments["start_date"]
    elif days is not None:
        start = (_today() - timedelta(days=int(days))).isoformat()
    else:
        start = (_today() - timedelta(days=28)).isoformat()
    dimensions = arguments.get("dimensions") or ["query"]
    # row_limit wins; `limit` is an accepted alias for AI hosts that conflate it
    # with the top-N tools.
    row_limit = int(arguments.get("row_limit", arguments.get("limit", 1000)))
    start_row = int(arguments.get("start_row", 0))
    search_type = arguments.get("search_type", "web")
    data_state = arguments.get("data_state") or config.gsc_data_state

    try:
        resp = _run_query(
            client,
            site,
            start=start,
            end=end,
            dimensions=dimensions,
            row_limit=row_limit,
            start_row=start_row,
            search_type=search_type,
            data_state=data_state,
            filters=arguments.get("filters"),
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    rows = [_normalize_row(r) for r in resp.get("rows", [])]
    return ok(
        {
            "site_url": site,
            "start_date": start,
            "end_date": end,
            "dimensions": dimensions,
            "data_state": data_state,
            "row_count": len(rows),
            "rows": rows,
        }
    )


# --- gsc_top_queries / gsc_top_pages --------------------------------------

def _top_tool(name: str, dimension: str, noun: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": (
            f"Convenience wrapper: top {noun} for a property over the last N "
            f"days by clicks. Equivalent to gsc_search_analytics with "
            f"dimensions=[\"{dimension}\"]. Uses the configured data_state "
            f"(default 'all'; 'final' lags 2-3 days behind the dashboard)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string", "description": "Defaults to the configured default site."},
                "days": {"type": "integer", "minimum": 1, "maximum": 480, "description": "Lookback window. Defaults to 28."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25000, "description": "Max rows to return. Defaults to 50."},
            },
            "additionalProperties": False,
        },
        "annotations": annotations(read=True),
    }


TOOL_TOP_QUERIES = _top_tool("gsc_top_queries", "query", "queries")
TOOL_TOP_PAGES = _top_tool("gsc_top_pages", "page", "pages")


def _top_handler(dimension: str):
    def handler(arguments, config, clients) -> dict[str, Any]:
        client, error = _require(clients)
        if error:
            return error
        site = resolve_site(arguments, config)
        if not site:
            return missing_site_error()
        days = int(arguments.get("days", 28))
        limit = int(arguments.get("limit", 50))
        end = _today().isoformat()
        start = (_today() - timedelta(days=days)).isoformat()
        try:
            resp = _run_query(
                client,
                site,
                start=start,
                end=end,
                dimensions=[dimension],
                row_limit=limit,
                data_state=config.gsc_data_state,
            )
        except ApiError as exc:
            return exc.to_envelope(_SERVICE)
        rows = [_normalize_row(r) for r in resp.get("rows", [])][:limit]
        return ok(
            {
                "site_url": site,
                "days": days,
                "start_date": start,
                "end_date": end,
                "dimension": dimension,
                "row_count": len(rows),
                "rows": rows,
            }
        )

    return handler


gsc_top_queries = _top_handler("query")
gsc_top_pages = _top_handler("page")


# --- gsc_compare_periods --------------------------------------------------

_COMPARE_SORT_KEYS = (
    "delta_clicks",
    "delta_impressions",
    "delta_ctr",
    "delta_position",
)


TOOL_COMPARE_PERIODS = {
    "name": "gsc_compare_periods",
    "description": (
        "Compare two equal-length time windows (current vs prior) and return "
        "per-key deltas in clicks, impressions, CTR, and position, plus keys "
        "present in only one window. v0.2.0: optional sort_by / sort_dir / "
        "min_delta_* filters and an anomalies_only z-score gate so the same "
        "tool covers 'biggest gainers', 'biggest losers', 'meaningful movers', "
        "and 'statistical outliers' without needing separate tools. When "
        "anomalies_only=true, the response's filters_applied.sigma_used field "
        "reports the population-stdev (sigma) actually computed from the "
        "matched rows' sort_by metric distribution; the effective z-score "
        "cutoff applied is `sigma_threshold * sigma_used`."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "site_url": {"type": "string", "description": "Defaults to the configured default site."},
            "dimensions": {
                "type": "array",
                "items": {"type": "string", "enum": _DIMENSIONS},
                "description": "Defaults to [\"query\"].",
            },
            "current_days": {"type": "integer", "minimum": 1, "maximum": 240, "description": "Length of each window in days. Defaults to 28."},
            "days": {"type": "integer", "minimum": 1, "maximum": 240, "description": "Alias for current_days. current_days wins when both are set."},
            "gap_days": {"type": "integer", "minimum": 0, "maximum": 240, "description": "Days between the two windows. Defaults to 0."},
            "row_limit": {"type": "integer", "minimum": 1, "maximum": 25000, "description": "Rows per window query. Defaults to 1000."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25000, "description": "Alias for row_limit. row_limit wins when both are set."},
            "sort_by": {
                "type": "string",
                "enum": list(_COMPARE_SORT_KEYS),
                "description": "Metric to sort matched rows by. Defaults to delta_clicks.",
            },
            "sort_dir": {
                "type": "string",
                "enum": ["asc", "desc"],
                "description": "Sort direction. 'desc' for biggest gainers (default). 'asc' for biggest losers. Note: delta_position is reversed (negative = improvement), so use 'asc' to find rank gains.",
            },
            "min_delta_clicks": {"type": "integer", "minimum": 0, "description": "Filter to rows with abs(delta_clicks) >= this. Defaults to 0 (no filter)."},
            "min_delta_impressions": {"type": "integer", "minimum": 0, "description": "Filter to rows with abs(delta_impressions) >= this. Defaults to 0."},
            "min_delta_position": {"type": "number", "minimum": 0, "description": "Filter to rows with abs(delta_position) >= this. Defaults to 0."},
            "anomalies_only": {"type": "boolean", "description": "If true, keep only rows where the sort_by metric exceeds sigma_threshold standard deviations from the matched-rows mean (statistical outliers). Default false."},
            "sigma_threshold": {"type": "number", "minimum": 0.5, "description": "Threshold for anomalies_only. Defaults to 2.0 (~95th percentile under normality)."},
            "top": {"type": "integer", "minimum": 1, "maximum": 25000, "description": "Cap the returned rows. No cap by default."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def _key_of(row: dict[str, Any]) -> tuple:
    return tuple(row.get("keys", []))


def gsc_compare_periods(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()

    dimensions = arguments.get("dimensions") or ["query"]
    # current_days wins; `days` is the friendlier alias for AI hosts that
    # already used it on the top-N tools.
    current_days = int(arguments.get("current_days", arguments.get("days", 28)))
    gap_days = int(arguments.get("gap_days", 0))
    row_limit = int(arguments.get("row_limit", arguments.get("limit", 1000)))

    today = _today()
    current_end = today
    current_start = today - timedelta(days=current_days)
    prior_end = current_start - timedelta(days=gap_days + 1)
    prior_start = prior_end - timedelta(days=current_days)

    try:
        current_resp = _run_query(
            client, site, start=current_start.isoformat(), end=current_end.isoformat(),
            dimensions=dimensions, row_limit=row_limit, data_state=config.gsc_data_state,
        )
        prior_resp = _run_query(
            client, site, start=prior_start.isoformat(), end=prior_end.isoformat(),
            dimensions=dimensions, row_limit=row_limit, data_state=config.gsc_data_state,
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    current = {_key_of(r): _normalize_row(r) for r in current_resp.get("rows", [])}
    prior = {_key_of(r): _normalize_row(r) for r in prior_resp.get("rows", [])}

    compared = []
    for key in current.keys() & prior.keys():
        c, p = current[key], prior[key]
        compared.append(
            {
                "keys": list(key),
                "current": c,
                "prior": p,
                "delta_clicks": c["clicks"] - p["clicks"],
                "delta_impressions": c["impressions"] - p["impressions"],
                "delta_ctr": round(c["ctr"] - p["ctr"], 6),
                # Position: lower is better, so a negative delta is an improvement.
                "delta_position": round(c["position"] - p["position"], 4),
            }
        )

    total_matched = len(compared)

    # --- v0.2.0 filter / sort / anomaly options ---
    sort_by = arguments.get("sort_by", "delta_clicks")
    if sort_by not in _COMPARE_SORT_KEYS:
        sort_by = "delta_clicks"
    sort_dir = arguments.get("sort_dir", "desc")
    reverse = (sort_dir != "asc")
    min_delta_clicks = int(arguments.get("min_delta_clicks", 0))
    min_delta_impressions = int(arguments.get("min_delta_impressions", 0))
    min_delta_position = float(arguments.get("min_delta_position", 0))
    anomalies_only = bool(arguments.get("anomalies_only", False))
    sigma_threshold = float(arguments.get("sigma_threshold", 2.0))
    top = arguments.get("top")

    if min_delta_clicks or min_delta_impressions or min_delta_position:
        compared = [
            r for r in compared
            if abs(r["delta_clicks"]) >= min_delta_clicks
            and abs(r["delta_impressions"]) >= min_delta_impressions
            and abs(r["delta_position"]) >= min_delta_position
        ]

    sigma_used: float | None = None
    if anomalies_only and len(compared) >= 2:
        values = [abs(r[sort_by]) for r in compared]
        mean = sum(values) / len(values)
        # Population stddev (matches numpy.std() default; appropriate when we
        # treat the matched set as the whole population, not a sample).
        sigma_used = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        if sigma_used > 0:
            threshold = sigma_threshold * sigma_used
            compared = [r for r in compared if abs(r[sort_by]) > threshold]

    compared.sort(key=lambda r: r[sort_by], reverse=reverse)
    if top is not None:
        compared = compared[: int(top)]

    only_current = [list(k) for k in current.keys() - prior.keys()]
    only_prior = [list(k) for k in prior.keys() - current.keys()]

    return ok(
        {
            "site_url": site,
            "dimensions": dimensions,
            "current_window": {"start_date": current_start.isoformat(), "end_date": current_end.isoformat()},
            "prior_window": {"start_date": prior_start.isoformat(), "end_date": prior_end.isoformat()},
            "matched_count": len(compared),
            "total_matched": total_matched,
            "filters_applied": {
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "min_delta_clicks": min_delta_clicks,
                "min_delta_impressions": min_delta_impressions,
                "min_delta_position": min_delta_position,
                "anomalies_only": anomalies_only,
                "sigma_threshold": sigma_threshold if anomalies_only else None,
                "sigma_used": round(sigma_used, 4) if sigma_used is not None else None,
                "top": int(top) if top is not None else None,
            },
            "rows": compared,
            "unmatched": {"only_current": only_current, "only_prior": only_prior},
        }
    )


# --- url inspection -------------------------------------------------------

def _shape_inspection(url: str, result: dict[str, Any]) -> dict[str, Any]:
    idx = result.get("indexStatusResult", {})
    shaped = {
        "url": url,
        "verdict": idx.get("verdict"),
        "coverage_state": idx.get("coverageState"),
        "crawled_as": idx.get("crawledAs"),
        "last_crawl_time": idx.get("lastCrawlTime"),
        "indexing_state": idx.get("indexingState"),
        "page_fetch_state": idx.get("pageFetchState"),
        "robots_txt_state": idx.get("robotsTxtState"),
        "google_canonical": idx.get("googleCanonical"),
        "user_canonical": idx.get("userCanonical"),
    }
    mobile = result.get("mobileUsabilityResult", {})
    if mobile:
        shaped["mobile_usability_verdict"] = mobile.get("verdict")
    rich = result.get("richResultsResult", {})
    if rich:
        shaped["rich_results_verdict"] = rich.get("verdict")
    return shaped


TOOL_INSPECT_URL = {
    "name": "gsc_inspect_url",
    "description": (
        "Inspect a single URL via the URL Inspection API: index verdict, "
        "coverage state, crawl info, canonicals, and mobile/rich-results "
        "summaries when present."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to inspect. Required."},
            "site_url": {"type": "string", "description": "Owning property. Defaults to the configured default site."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def gsc_inspect_url(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    url = arguments.get("url")
    if not url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url is required.", docs_url=DOCS_BASE + "gsc")
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()
    try:
        resp = client.inspect_url(url=url, site_url=site)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok(_shape_inspection(url, resp.get("inspectionResult", {})))


TOOL_BATCH_INSPECT = {
    "name": "gsc_batch_inspect_urls",
    "description": (
        "Inspect several URLs in one call (capped at 25). Returns a result per "
        "URL plus a list of per-URL failures. Rate-limit and transient errors "
        "are reported per URL without failing the whole batch."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": _MAX_INSPECT_BATCH,
                "description": f"URLs to inspect (max {_MAX_INSPECT_BATCH}).",
            },
            "site_url": {"type": "string", "description": "Owning property. Defaults to the configured default site."},
        },
        "required": ["urls"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def gsc_batch_inspect_urls(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    urls = arguments.get("urls") or []
    if not urls:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "urls must be a non-empty list.", docs_url=DOCS_BASE + "gsc")
    if len(urls) > _MAX_INSPECT_BATCH:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            f"Too many URLs ({len(urls)}); max is {_MAX_INSPECT_BATCH} per call.",
            docs_url=DOCS_BASE + "gsc",
        )
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()

    results: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for url in urls:
        try:
            resp = client.inspect_url(url=url, site_url=site)
            results.append(_shape_inspection(url, resp.get("inspectionResult", {})))
        except ApiError as exc:
            failed.append({"url": url, "code": str(exc.code), "message": exc.message})
    return ok({"site_url": site, "inspected": len(results), "results": results, "failed": failed})


# --- sitemaps -------------------------------------------------------------

TOOL_LIST_SITEMAPS = {
    "name": "gsc_list_sitemaps",
    "description": "List the sitemaps Google knows about for a property, with submission and indexing status.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "site_url": {"type": "string", "description": "Defaults to the configured default site."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def gsc_list_sitemaps(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()
    try:
        resp = client.list_sitemaps(site)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    sitemaps = [
        {
            "path": s.get("path"),
            "last_submitted": s.get("lastSubmitted"),
            "last_downloaded": s.get("lastDownloaded"),
            "is_pending": s.get("isPending"),
            "is_sitemaps_index": s.get("isSitemapsIndex"),
            "contents": s.get("contents", []),
            "errors": s.get("errors"),
            "warnings": s.get("warnings"),
        }
        for s in resp.get("sitemap", [])
    ]
    return ok({"site_url": site, "count": len(sitemaps), "sitemaps": sitemaps})


TOOL_SUBMIT_SITEMAP = {
    "name": "gsc_submit_sitemap",
    "description": (
        "Submit a sitemap to Search Console. Requires the writable webmasters "
        "scope. Available by default (a routine SEO task; not gated). Accepts "
        "either `sitemap_url` (preferred, friendly name) or `feedpath` (the raw "
        "API field name; kept for back-compat)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "sitemap_url": {"type": "string", "description": "Full sitemap URL, e.g. https://www.example.com/sitemap.xml. Preferred."},
            "feedpath": {"type": "string", "description": "Alias for sitemap_url (the raw Google API field name). Either works; sitemap_url wins when both are set."},
            "site_url": {"type": "string", "description": "Owning property. Defaults to the configured default site."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=False, destructive=False),
}


def gsc_submit_sitemap(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    sitemap_url = arguments.get("sitemap_url") or arguments.get("feedpath")
    if not sitemap_url:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            "sitemap_url (or feedpath) is required.",
            docs_url=DOCS_BASE + "gsc",
        )
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()
    try:
        client.submit_sitemap(site, sitemap_url)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok({"site_url": site, "sitemap_url": sitemap_url, "submitted": True})


# --- indexing request -----------------------------------------------------

TOOL_REQUEST_INDEXING = {
    "name": "gsc_request_indexing",
    "description": (
        "Ask Google to (re)crawl one or more URLs via the Indexing API "
        "(URL_UPDATED). Requires the indexing scope. Available by default. "
        "Accepts a single `url` (string) or `urls` (array up to "
        f"{_MAX_INDEXING_BATCH}). A scope or disabled-API error stops the batch "
        "and is returned with remediation; per-URL errors are collected. "
        "`notify_time: null` on success is normal upstream behavior, not a "
        "failure."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Single URL convenience form. Either url or urls is required."},
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": _MAX_INDEXING_BATCH,
                "description": f"URLs to request indexing for (max {_MAX_INDEXING_BATCH}). Either url or urls is required.",
            },
            "site_url": {"type": "string", "description": "Informational; the Indexing API is project-scoped, not property-scoped."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=False, destructive=False),
}

# Errors that affect every URL: stop the batch and surface them directly.
_FATAL_INDEXING_CODES = {
    ErrorCode.SCOPE_INSUFFICIENT,
    ErrorCode.SERVICE_DISABLED,
    ErrorCode.AUTH_INVALID,
}


def gsc_request_indexing(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    # Accept singular `url` as a convenience for AI hosts that default to it;
    # canonical `urls` (array) wins when both are given.
    urls = arguments.get("urls") or []
    single = arguments.get("url")
    if not urls and single:
        urls = [single]
    if not urls:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url (string) or urls (non-empty array) is required.", docs_url=DOCS_BASE + "gsc")
    if len(urls) > _MAX_INDEXING_BATCH:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            f"Too many URLs ({len(urls)}); max is {_MAX_INDEXING_BATCH} per call.",
            docs_url=DOCS_BASE + "gsc",
        )

    submitted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for url in urls:
        try:
            resp = client.request_indexing(url)
            notify_time = (
                resp.get("urlNotificationMetadata", {})
                .get("latestUpdate", {})
                .get("notifyTime")
            )
            submitted.append({"url": url, "notify_time": notify_time})
        except ApiError as exc:
            if exc.code in _FATAL_INDEXING_CODES:
                # Affects all URLs; stop and return the actionable error.
                env = exc.to_envelope(_SERVICE)
                env["error"]["details"] = {
                    **(env["error"]["details"] or {}),
                    "submitted_before_failure": submitted,
                }
                return env
            failed.append({"url": url, "code": str(exc.code), "message": exc.message})

    return ok({"submitted_count": len(submitted), "submitted": submitted, "failed": failed})


# --- query intelligence (v0.2.0) ------------------------------------------
#
# These four tools are pure filter+sort over `gsc_search_analytics` results.
# They do not introduce new client calls beyond what already exists; the
# value they add is presenting the right slice for a common SEO task so the
# AI host does not have to assemble dimension filters and CTR thresholds.

TOOL_QUERY_OPPORTUNITIES = {
    "name": "gsc_query_opportunities",
    "description": (
        "Queries already ranking in the top N positions but with below-target "
        "CTR. These are title and meta-description optimization candidates. "
        "Returns rows sorted by impressions desc (the bigger the impression "
        "volume, the more clicks a CTR lift unlocks)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "site_url": {"type": "string", "description": "Defaults to the configured default site."},
            "days": {"type": "integer", "minimum": 1, "maximum": 480, "description": "Lookback window. Defaults to 28."},
            "position_max": {"type": "number", "minimum": 1, "maximum": 100, "description": "Only include queries with average position <= this. Defaults to 10."},
            "ctr_max": {"type": "number", "minimum": 0, "maximum": 1, "description": "Only include queries with CTR <= this (e.g. 0.03 = 3%). Defaults to 0.03."},
            "impressions_min": {"type": "integer", "minimum": 1, "maximum": 1000000, "description": "Drop low-volume noise. Defaults to 100."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Max rows to return. Defaults to 50."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def gsc_query_opportunities(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()

    days = int(arguments.get("days", 28))
    position_max = float(arguments.get("position_max", 10.0))
    ctr_max = float(arguments.get("ctr_max", 0.03))
    impressions_min = int(arguments.get("impressions_min", 100))
    limit = int(arguments.get("limit", 50))
    end = _today().isoformat()
    start = (_today() - timedelta(days=days)).isoformat()
    try:
        resp = _run_query(
            client, site,
            start=start, end=end, dimensions=["query"],
            row_limit=25000, data_state=config.gsc_data_state,
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    rows = []
    for r in resp.get("rows", []):
        if r.get("impressions", 0) < impressions_min:
            continue
        if r.get("position", 999) > position_max:
            continue
        if r.get("ctr", 0) > ctr_max:
            continue
        rows.append(_normalize_row(r))
    rows.sort(key=lambda x: x["impressions"], reverse=True)
    rows = rows[:limit]
    return ok(
        {
            "site_url": site, "days": days,
            "start_date": start, "end_date": end,
            "row_count": len(rows), "rows": rows,
            "filters_applied": {
                "position_max": position_max,
                "ctr_max": ctr_max,
                "impressions_min": impressions_min,
            },
        }
    )


TOOL_QUERY_GAPS = {
    "name": "gsc_query_gaps",
    "description": (
        "Queries that drive impressions but very few clicks. These are content "
        "opportunity signals: a page is being shown for the query, the click "
        "experience is not landing. Returns rows sorted by impressions desc."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "site_url": {"type": "string"},
            "days": {"type": "integer", "minimum": 1, "maximum": 480, "description": "Defaults to 28."},
            "impressions_min": {"type": "integer", "minimum": 1, "maximum": 1000000, "description": "Floor for inclusion. Defaults to 50."},
            "clicks_max": {"type": "integer", "minimum": 0, "maximum": 1000, "description": "Drop queries that already convert. Defaults to 2."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Defaults to 50."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def gsc_query_gaps(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()

    days = int(arguments.get("days", 28))
    impressions_min = int(arguments.get("impressions_min", 50))
    clicks_max = int(arguments.get("clicks_max", 2))
    limit = int(arguments.get("limit", 50))
    end = _today().isoformat()
    start = (_today() - timedelta(days=days)).isoformat()
    try:
        resp = _run_query(
            client, site,
            start=start, end=end, dimensions=["query"],
            row_limit=25000, data_state=config.gsc_data_state,
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    rows = []
    for r in resp.get("rows", []):
        if r.get("impressions", 0) < impressions_min:
            continue
        if r.get("clicks", 0) > clicks_max:
            continue
        rows.append(_normalize_row(r))
    rows.sort(key=lambda x: x["impressions"], reverse=True)
    rows = rows[:limit]
    return ok(
        {
            "site_url": site, "days": days,
            "start_date": start, "end_date": end,
            "row_count": len(rows), "rows": rows,
            "filters_applied": {
                "impressions_min": impressions_min,
                "clicks_max": clicks_max,
            },
        }
    )


TOOL_NEW_QUERIES = {
    "name": "gsc_new_queries",
    "description": (
        "Queries that have impressions in the current window but had no "
        "impressions in the prior comparison window. Useful for spotting "
        "emerging topics or pages that just started ranking."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "site_url": {"type": "string"},
            "days": {"type": "integer", "minimum": 1, "maximum": 240, "description": "Current window in days. Defaults to 7."},
            "prior_days": {"type": "integer", "minimum": 1, "maximum": 480, "description": "Prior window in days. Defaults to 28 (so 7 vs 28 catches genuinely new entries)."},
            "impressions_min": {"type": "integer", "minimum": 1, "description": "Minimum impressions in the current window to include. Defaults to 5."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Defaults to 50."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def gsc_new_queries(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()

    days = int(arguments.get("days", 7))
    prior_days = int(arguments.get("prior_days", 28))
    impressions_min = int(arguments.get("impressions_min", 5))
    limit = int(arguments.get("limit", 50))
    today = _today()
    current_end = today
    current_start = today - timedelta(days=days)
    prior_end = current_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=prior_days)

    try:
        current_resp = _run_query(
            client, site,
            start=current_start.isoformat(), end=current_end.isoformat(),
            dimensions=["query"], row_limit=25000, data_state=config.gsc_data_state,
        )
        prior_resp = _run_query(
            client, site,
            start=prior_start.isoformat(), end=prior_end.isoformat(),
            dimensions=["query"], row_limit=25000, data_state=config.gsc_data_state,
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    prior_keys = {_key_of(r) for r in prior_resp.get("rows", [])}
    new_rows = []
    for r in current_resp.get("rows", []):
        if _key_of(r) in prior_keys:
            continue
        if r.get("impressions", 0) < impressions_min:
            continue
        new_rows.append(_normalize_row(r))
    new_rows.sort(key=lambda x: x["impressions"], reverse=True)
    new_rows = new_rows[:limit]
    return ok(
        {
            "site_url": site,
            "current_window": {"start_date": current_start.isoformat(), "end_date": current_end.isoformat(), "days": days},
            "prior_window": {"start_date": prior_start.isoformat(), "end_date": prior_end.isoformat(), "days": prior_days},
            "row_count": len(new_rows), "rows": new_rows,
            "filters_applied": {"impressions_min": impressions_min},
        }
    )


TOOL_TOP_PAGES_BY_QUERY = {
    "name": "gsc_top_pages_by_query",
    "description": (
        "Which pages rank for a specific query. The classic cannibalization "
        "audit input: if multiple pages rank for the same query, you typically "
        "want to consolidate or differentiate them. Filters search_analytics "
        "by an exact query match and returns rows by page dimension."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Exact query string. Required."},
            "site_url": {"type": "string", "description": "Defaults to the configured default site."},
            "days": {"type": "integer", "minimum": 1, "maximum": 480, "description": "Defaults to 28."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Defaults to 20."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def gsc_top_pages_by_query(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    query = arguments.get("query")
    if not query:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "query is required.", docs_url=DOCS_BASE + "gsc")
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()
    days = int(arguments.get("days", 28))
    limit = int(arguments.get("limit", 20))
    end = _today().isoformat()
    start = (_today() - timedelta(days=days)).isoformat()
    try:
        resp = _run_query(
            client, site,
            start=start, end=end, dimensions=["page"],
            row_limit=limit, data_state=config.gsc_data_state,
            filters=[{"dimension": "query", "operator": "equals", "expression": query}],
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    rows = [_normalize_row(r) for r in resp.get("rows", [])][:limit]
    return ok(
        {
            "site_url": site, "query": query, "days": days,
            "start_date": start, "end_date": end,
            "row_count": len(rows), "rows": rows,
        }
    )


# --- v0.5.0 multi-property + lifecycle tools ------------------------------
#
# Four tools that reinforce moat 1 (multi-source breadth) of the plan: fleet
# views across many GSC properties + page-level lifecycle wrappers + a
# heuristic coverage audit. All ride on the existing GscClient; no new auth
# or client surface.


TOOL_PORTFOLIO_SUMMARY = {
    "name": "gsc_portfolio_summary",
    "description": (
        "Multi-property fleet view. Lists every property the credentials can "
        "see and returns a one-row summary per property (total clicks, "
        "impressions, average CTR, average position) for the last N days. "
        "The single fastest answer to 'how is the portfolio doing?' across "
        "agency or multi-brand setups. Honors the v0.2.0 alias convention "
        "(`days`) and applies the configured data_state."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "minimum": 1, "maximum": 480, "description": "Lookback window. Defaults to 28."},
            "include": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional allow-list of site_url values to include. Defaults to all visible properties.",
            },
            "exclude": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional deny-list of site_url values to skip.",
            },
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def gsc_portfolio_summary(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    days = int(arguments.get("days", 28))
    include = set(arguments.get("include") or [])
    exclude = set(arguments.get("exclude") or [])

    try:
        sites_resp = client.list_sites()
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    end = _today().isoformat()
    start = (_today() - timedelta(days=days)).isoformat()

    properties: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for entry in sites_resp.get("siteEntry", []):
        site = entry.get("siteUrl")
        if not site:
            continue
        if include and site not in include:
            continue
        if site in exclude:
            continue
        try:
            # Single aggregated query per property: no dimensions ->
            # GSC returns one row of totals.
            resp = _run_query(
                client, site,
                start=start, end=end,
                dimensions=[], row_limit=1,
                data_state=config.gsc_data_state,
            )
        except ApiError as exc:
            errors.append({"site_url": site, "code": exc.code.value, "message": exc.message})
            continue
        rows = resp.get("rows") or []
        if rows:
            row = _normalize_row(rows[0])
            properties.append({
                "site_url": site,
                "permission_level": entry.get("permissionLevel"),
                "writable": entry.get("permissionLevel") in {"siteOwner", "siteFullUser"},
                "clicks": row["clicks"],
                "impressions": row["impressions"],
                "ctr": row["ctr"],
                "position": row["position"],
            })
        else:
            properties.append({
                "site_url": site,
                "permission_level": entry.get("permissionLevel"),
                "writable": entry.get("permissionLevel") in {"siteOwner", "siteFullUser"},
                "clicks": 0, "impressions": 0, "ctr": 0.0, "position": None,
            })

    # Portfolio-level rollup. Position is intentionally NOT averaged at the
    # rollup layer because impression-weighted averaging would give a more
    # meaningful number, and the AI host can compute that from the per-row
    # data if it wants to.
    portfolio_totals = {
        "clicks": sum(p["clicks"] for p in properties),
        "impressions": sum(p["impressions"] for p in properties),
        "property_count": len(properties),
    }

    return ok({
        "days": days,
        "start_date": start,
        "end_date": end,
        "portfolio_totals": portfolio_totals,
        "properties": properties,
        "errors": errors,
    })


def _movers_tool(name: str, direction: str, description_verb: str) -> dict[str, Any]:
    """Tool-schema builder for the trending/decaying page wrappers. Both
    are convenience aliases over gsc_compare_periods with dimensions=["page"]
    and a fixed sort direction on delta_impressions."""
    return {
        "name": name,
        "description": (
            f"Pages whose impressions {description_verb} most over the last "
            "N days vs the prior N days. Equivalent to "
            "`gsc_compare_periods` with `dimensions=[\"page\"]`, "
            f"`sort_by=\"delta_impressions\"`, `sort_dir=\"{direction}\"`. "
            "Use this for lifecycle triage (find pages to invest in or "
            "rescue)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "site_url": {"type": "string", "description": "Defaults to the configured default site."},
                "days": {"type": "integer", "minimum": 1, "maximum": 240, "description": "Length of each window. Defaults to 28."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25000, "description": "Top-N to return. Defaults to 25."},
            },
            "additionalProperties": False,
        },
        "annotations": annotations(read=True),
    }


def _movers_handler(direction: str):
    def handler(arguments, config, clients) -> dict[str, Any]:
        # Translate to gsc_compare_periods arguments and dispatch directly to
        # its handler. This keeps the underlying statistical machinery (z-score,
        # filters, sigma_used) consistent across the three lifecycle tools.
        days = int(arguments.get("days", 28))
        limit = int(arguments.get("limit", 25))
        compare_args = {
            "site_url": arguments.get("site_url"),
            "dimensions": ["page"],
            "current_days": days,
            "row_limit": 25000,  # pull broad; the compare_periods top caps after sort
            "sort_by": "delta_impressions",
            "sort_dir": direction,
            "top": limit,
        }
        return gsc_compare_periods(compare_args, config, clients)
    return handler


TOOL_TRENDING_PAGES = _movers_tool("gsc_trending_pages", "desc", "grew")
TOOL_DECAYING_PAGES = _movers_tool("gsc_decaying_pages", "asc", "fell")
gsc_trending_pages = _movers_handler("desc")
gsc_decaying_pages = _movers_handler("asc")


TOOL_COVERAGE_AUDIT = {
    "name": "gsc_coverage_audit",
    "description": (
        "Heuristic coverage audit. The GSC Index Coverage report is NOT "
        "exposed in the public API, so this tool takes a user-supplied URL "
        "list (typically pulled from a sitemap) and bulk-inspects each, then "
        "rolls up the verdicts (PASS / PARTIAL / FAIL plus coverage_state "
        "frequencies). Honest substitute for the Coverage UI; the AI host "
        "can use the rollup to decide which URLs to deep-inspect."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "site_url": {"type": "string", "description": "Defaults to the configured default site."},
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 200,
                "description": "Absolute URLs to audit (cap 200; chunks of 25 are inspected per GSC's URL Inspection batch limit).",
            },
        },
        "required": ["urls"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def gsc_coverage_audit(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()
    urls = arguments.get("urls") or []
    if not urls:
        return err(
            ErrorCode.INVALID_INPUT, _SERVICE,
            "urls must be a non-empty list.",
            docs_url=DOCS_BASE + "gsc",
        )

    verdict_counts: dict[str, int] = {}
    coverage_counts: dict[str, int] = {}
    per_url: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for url in urls[:200]:
        try:
            # Round 6 §11b.i: the bug was here, with positional (site, url).
            # client.inspect_url is now kwargs-only so this call site cannot
            # silently regress.
            result = client.inspect_url(url=url, site_url=site)
        except ApiError as exc:
            failed.append({"url": url, "code": exc.code.value, "message": exc.message})
            continue
        # Match the unwrap convention used by gsc_inspect_url: the response
        # carries an inspectionResult wrapper that _shape_inspection expects
        # to be passed unwrapped.
        shaped = _shape_inspection(url, result.get("inspectionResult", {}))
        verdict = shaped.get("verdict") or "UNKNOWN"
        coverage = shaped.get("coverage_state") or "Unknown"
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        coverage_counts[coverage] = coverage_counts.get(coverage, 0) + 1
        per_url.append({
            "url": url,
            "verdict": verdict,
            "coverage_state": coverage,
            "indexing_state": shaped.get("indexing_state"),
            "last_crawl_time": shaped.get("last_crawl_time"),
            "google_canonical": shaped.get("google_canonical"),
            "user_canonical": shaped.get("user_canonical"),
        })

    return ok({
        "site_url": site,
        "audited_count": len(per_url),
        "failed_count": len(failed),
        "verdict_counts": verdict_counts,
        "coverage_state_counts": coverage_counts,
        "per_url": per_url,
        "failed": failed,
    })


# --- registry -------------------------------------------------------------

TOOLS = [
    TOOL_LIST_PROPERTIES,
    TOOL_SEARCH_ANALYTICS,
    TOOL_TOP_QUERIES,
    TOOL_TOP_PAGES,
    TOOL_COMPARE_PERIODS,
    TOOL_INSPECT_URL,
    TOOL_BATCH_INSPECT,
    TOOL_LIST_SITEMAPS,
    TOOL_SUBMIT_SITEMAP,
    TOOL_REQUEST_INDEXING,
    # v0.2.0 query intelligence
    TOOL_QUERY_OPPORTUNITIES,
    TOOL_QUERY_GAPS,
    TOOL_NEW_QUERIES,
    TOOL_TOP_PAGES_BY_QUERY,
    # v0.5.0 multi-property + lifecycle
    TOOL_PORTFOLIO_SUMMARY,
    TOOL_TRENDING_PAGES,
    TOOL_DECAYING_PAGES,
    TOOL_COVERAGE_AUDIT,
]

HANDLERS = {
    "gsc_list_properties": gsc_list_properties,
    "gsc_search_analytics": gsc_search_analytics,
    "gsc_top_queries": gsc_top_queries,
    "gsc_top_pages": gsc_top_pages,
    "gsc_compare_periods": gsc_compare_periods,
    "gsc_inspect_url": gsc_inspect_url,
    "gsc_batch_inspect_urls": gsc_batch_inspect_urls,
    "gsc_list_sitemaps": gsc_list_sitemaps,
    "gsc_submit_sitemap": gsc_submit_sitemap,
    "gsc_request_indexing": gsc_request_indexing,
    # v0.2.0 query intelligence
    "gsc_query_opportunities": gsc_query_opportunities,
    "gsc_query_gaps": gsc_query_gaps,
    "gsc_new_queries": gsc_new_queries,
    "gsc_top_pages_by_query": gsc_top_pages_by_query,
    # v0.5.0 multi-property + lifecycle
    "gsc_portfolio_summary": gsc_portfolio_summary,
    "gsc_trending_pages": gsc_trending_pages,
    "gsc_decaying_pages": gsc_decaying_pages,
    "gsc_coverage_audit": gsc_coverage_audit,
}
