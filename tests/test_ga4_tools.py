"""Offline tests for the 4 GA4 tools and the Ga4Client, using RunReportResponse-
shaped fakes. No BetaAnalyticsDataClient, no network."""

from __future__ import annotations

import pytest

from seo_mcp.clients.ga4 import normalize_property_id
from seo_mcp.tools import ga4_tools


PROP = "properties/123456789"


def _cfg(make_config, **extra):
    return make_config(SEO_MCP_GA4_PROPERTY_ID=PROP, **extra)


# --- property id normalization --------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("123456789", "properties/123456789"),
        ("properties/123456789", "properties/123456789"),
        ("  123  ", "properties/123"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_property_id(value, expected):
    assert normalize_property_id(value) == expected


# --- auth gating ----------------------------------------------------------


def test_tools_return_auth_missing_when_unconfigured(make_config):
    cfg = make_config()  # no Google creds, no client wired
    for name, handler in ga4_tools.HANDLERS.items():
        result = handler({}, cfg, {})
        assert result["ok"] is False, name
        assert result["error"]["code"] == "AUTH_MISSING", name
        assert result["error"]["service"] == "ga4"


def test_run_report_missing_property_returns_invalid_input(make_config, make_ga4_client, ga4_response):
    client = make_ga4_client(ga4_response(["date"], ["sessions"], []))
    # Google configured (client wired) but no property anywhere.
    result = ga4_tools.ga4_run_report({}, make_config(), {"ga4": client})
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


# --- ga4_run_report -------------------------------------------------------


def test_run_report_defaults_and_normalization(make_config, make_ga4_client, ga4_response):
    resp = ga4_response(
        ["date"],
        ["sessions"],
        [(["20260501"], ["120"]), (["20260502"], ["95"])],
    )
    client = make_ga4_client(resp)
    result = ga4_tools.ga4_run_report({}, _cfg(make_config), {"ga4": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["property_id"] == PROP
    assert data["dimensions"] == ["date"]
    assert data["metrics"] == ["sessions"]
    assert data["row_count"] == 2
    assert data["rows"][0] == {"dimensions": ["20260501"], "metrics": [120]}
    # The request carried the defaults.
    req = client._analytics.requests[0]
    assert req.property == PROP
    assert req.limit == 1000
    assert [d.name for d in req.dimensions] == ["date"]
    assert [m.name for m in req.metrics] == ["sessions"]
    assert req.date_ranges[0].start_date == "28daysAgo"
    assert req.date_ranges[0].end_date == "today"


def test_run_report_metric_value_coercion(make_config, make_ga4_client, ga4_response):
    resp = ga4_response(["country"], ["sessions", "engagementRate"], [(["usa"], ["120", "0.835"])])
    client = make_ga4_client(resp)
    result = ga4_tools.ga4_run_report(
        {"dimensions": ["country"], "metrics": ["sessions", "engagementRate"]}, _cfg(make_config), {"ga4": client}
    )
    metrics = result["data"]["rows"][0]["metrics"]
    assert metrics[0] == 120 and isinstance(metrics[0], int)
    assert metrics[1] == 0.835 and isinstance(metrics[1], float)


def test_run_report_accepts_days_alias(make_config, make_ga4_client, ga4_response):
    client = make_ga4_client(ga4_response(["date"], ["sessions"], []))
    ga4_tools.ga4_run_report({"days": 14}, _cfg(make_config), {"ga4": client})
    req = client._analytics.requests[0]
    assert req.date_ranges[0].start_date == "14daysAgo"
    assert req.date_ranges[0].end_date == "today"


def test_run_report_accepts_limit_alias(make_config, make_ga4_client, ga4_response):
    client = make_ga4_client(ga4_response(["date"], ["sessions"], []))
    ga4_tools.ga4_run_report({"limit": 250}, _cfg(make_config), {"ga4": client})
    assert client._analytics.requests[0].limit == 250


def test_run_report_start_date_wins_over_days(make_config, make_ga4_client, ga4_response):
    client = make_ga4_client(ga4_response(["date"], ["sessions"], []))
    ga4_tools.ga4_run_report(
        {"start_date": "2026-01-01", "end_date": "2026-01-31", "days": 365},
        _cfg(make_config),
        {"ga4": client},
    )
    req = client._analytics.requests[0]
    assert req.date_ranges[0].start_date == "2026-01-01"


def test_run_report_bare_property_id_is_normalized(make_config, make_ga4_client, ga4_response):
    client = make_ga4_client(ga4_response(["date"], ["sessions"], []))
    ga4_tools.ga4_run_report({"property_id": "987654"}, make_config(), {"ga4": client})
    assert client._analytics.requests[0].property == "properties/987654"


def test_run_report_builds_dimension_filter_and_order(make_config, make_ga4_client, ga4_response):
    client = make_ga4_client(ga4_response(["pagePath"], ["sessions"], []))
    args = {
        "dimensions": ["pagePath"],
        "metrics": ["sessions"],
        "dimension_filter": {"field": "sessionDefaultChannelGroup", "value": "Organic Search", "match_type": "EXACT"},
        "order_by": {"metric": "sessions", "desc": True},
    }
    ga4_tools.ga4_run_report(args, _cfg(make_config), {"ga4": client})
    req = client._analytics.requests[0]
    assert req.dimension_filter.filter.field_name == "sessionDefaultChannelGroup"
    assert req.dimension_filter.filter.string_filter.value == "Organic Search"
    assert len(req.order_bys) == 1
    assert req.order_bys[0].metric.metric_name == "sessions"
    assert req.order_bys[0].desc is True


def test_run_report_in_list_filter(make_config, make_ga4_client, ga4_response):
    client = make_ga4_client(ga4_response(["country"], ["sessions"], []))
    args = {"dimensions": ["country"], "dimension_filter": {"field": "country", "in_list": ["usa", "canada"]}}
    ga4_tools.ga4_run_report(args, _cfg(make_config), {"ga4": client})
    req = client._analytics.requests[0]
    assert list(req.dimension_filter.filter.in_list_filter.values) == ["usa", "canada"]


# --- convenience wrappers -------------------------------------------------


def test_top_landing_pages_organic_filter_default(make_config, make_ga4_client, ga4_response):
    resp = ga4_response(
        ["landingPagePlusQueryString"],
        ["sessions", "engagementRate", "conversions"],
        [(["/blog/post"], ["80", "0.7", "3"])],
    )
    client = make_ga4_client(resp)
    result = ga4_tools.ga4_top_landing_pages({"days": 14}, _cfg(make_config), {"ga4": client})
    assert result["ok"] is True
    assert result["data"]["organic_only"] is True
    assert result["data"]["days"] == 14
    req = client._analytics.requests[0]
    assert [d.name for d in req.dimensions] == ["landingPagePlusQueryString"]
    assert req.dimension_filter.filter.string_filter.value == "Organic Search"
    assert req.date_ranges[0].start_date == "14daysAgo"
    assert req.order_bys[0].metric.metric_name == "sessions"


def test_top_landing_pages_organic_off_drops_filter(make_config, make_ga4_client, ga4_response):
    resp = ga4_response(["landingPagePlusQueryString"], ["sessions", "engagementRate", "conversions"], [])
    client = make_ga4_client(resp)
    ga4_tools.ga4_top_landing_pages({"organic_only": False}, _cfg(make_config), {"ga4": client})
    req = client._analytics.requests[0]
    # No dimension filter set when organic_only is false.
    assert not req.dimension_filter.filter.field_name


def test_traffic_by_channel(make_config, make_ga4_client, ga4_response):
    resp = ga4_response(
        ["sessionDefaultChannelGroup"],
        ["sessions", "engagedSessions", "conversions"],
        [(["Organic Search"], ["500", "420", "12"]), (["Direct"], ["300", "250", "5"])],
    )
    client = make_ga4_client(resp)
    result = ga4_tools.ga4_traffic_by_channel({}, _cfg(make_config), {"ga4": client})
    assert result["ok"] is True
    assert result["data"]["row_count"] == 2
    req = client._analytics.requests[0]
    assert [d.name for d in req.dimensions] == ["sessionDefaultChannelGroup"]
    assert [m.name for m in req.metrics] == ["sessions", "engagedSessions", "conversions"]


def test_organic_overview_totals_and_trend(make_config, make_ga4_client, ga4_response):
    metrics = ["sessions", "engagedSessions", "engagementRate", "averageSessionDuration", "conversions"]
    totals = ga4_response([], metrics, [([], ["1000", "820", "0.82", "95.4", "30"])])
    trend = ga4_response(
        ["date"],
        metrics,
        [(["20260501"], ["500", "410", "0.82", "90.0", "15"]), (["20260502"], ["500", "410", "0.82", "100.8", "15"])],
    )
    client = make_ga4_client([totals, trend])
    result = ga4_tools.ga4_organic_search_overview({}, _cfg(make_config), {"ga4": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["channel"] == "Organic Search"
    assert data["totals"]["sessions"] == 1000
    assert data["totals"]["engagementRate"] == 0.82
    assert len(data["trend"]) == 2
    assert data["trend"][0] == {
        "date": "20260501",
        "sessions": 500,
        "engagedSessions": 410,
        "engagementRate": 0.82,
        "averageSessionDuration": 90,
        "conversions": 15,
    }
    # Two reports issued: totals (no date dim) then trend (date dim).
    assert len(client._analytics.requests) == 2
    assert [d.name for d in client._analytics.requests[0].dimensions] == []
    assert [d.name for d in client._analytics.requests[1].dimensions] == ["date"]


def test_organic_overview_handles_empty_totals(make_config, make_ga4_client, ga4_response):
    metrics = ["sessions", "engagedSessions", "engagementRate", "averageSessionDuration", "conversions"]
    empty = ga4_response([], metrics, [])
    trend = ga4_response(["date"], metrics, [])
    client = make_ga4_client([empty, trend])
    result = ga4_tools.ga4_organic_search_overview({}, _cfg(make_config), {"ga4": client})
    assert result["ok"] is True
    assert result["data"]["totals"] == {}
    assert result["data"]["trend"] == []


# --- error mapping --------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [(401, "AUTH_INVALID"), (403, "AUTH_INVALID"), (404, "NOT_FOUND"), (400, "INVALID_INPUT"), (429, "RATE_LIMITED"), (500, "UPSTREAM_ERROR")],
)
def test_run_report_maps_api_core_errors(make_config, make_ga4_client, fake_ga4_error, code, expected):
    # FakeGoogleApiError exposes .code (the api_core path the mapper uses).
    client = make_ga4_client(fake_ga4_error(code, "boom"))
    result = ga4_tools.ga4_run_report({}, _cfg(make_config), {"ga4": client})
    assert result["ok"] is False
    assert result["error"]["code"] == expected
    assert result["error"]["service"] == "ga4"


def test_permission_denied_maps_to_auth_invalid(make_config, make_ga4_client, fake_ga4_error):
    client = make_ga4_client(fake_ga4_error(403, "User does not have sufficient permissions for this property."))
    result = ga4_tools.ga4_traffic_by_channel({}, _cfg(make_config), {"ga4": client})
    assert result["error"]["code"] == "AUTH_INVALID"


# --- client probe ---------------------------------------------------------


def test_probe_runs_report_with_default_property(make_ga4_client, ga4_response):
    client = make_ga4_client(ga4_response([], ["sessions"], [([], ["10"])]), default_property=PROP)
    assert client.probe() is True
    assert client._analytics.requests[0].property == PROP


def test_probe_without_property_raises(make_ga4_client, ga4_response):
    from seo_mcp.clients.errors import ApiError

    client = make_ga4_client(ga4_response([], ["sessions"], []), default_property=None)
    with pytest.raises(ApiError):
        client.probe()


# --- ga4_site_search ------------------------------------------------------


def test_site_search_returns_terms(make_config, make_ga4_client, ga4_response):
    resp = ga4_response(
        ["searchTerm"],
        ["eventCount", "sessions"],
        [(["pricing"], ["40", "33"]), (["refund policy"], ["12", "11"])],
    )
    client = make_ga4_client(resp)
    result = ga4_tools.ga4_site_search({"days": 14, "limit": 25}, _cfg(make_config), {"ga4": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["has_site_search_data"] is True
    assert data["days"] == 14
    assert data["rows"][0] == {"dimensions": ["pricing"], "metrics": [40, 33]}
    req = client._analytics.requests[0]
    assert [d.name for d in req.dimensions] == ["searchTerm"]
    assert [m.name for m in req.metrics] == ["eventCount", "sessions"]
    assert req.date_ranges[0].start_date == "14daysAgo"
    assert req.limit == 25
    assert req.order_bys[0].metric.metric_name == "eventCount"


def test_site_search_empty_is_honest(make_config, make_ga4_client, ga4_response):
    client = make_ga4_client(ga4_response(["searchTerm"], ["eventCount", "sessions"], []))
    result = ga4_tools.ga4_site_search({}, _cfg(make_config), {"ga4": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["has_site_search_data"] is False
    assert "note" in data
    assert data["rows"] == []


# --- ga4_landing_page_conversions -----------------------------------------


def test_landing_page_conversions_organic_default(make_config, make_ga4_client, ga4_response):
    resp = ga4_response(
        ["landingPage", "sessionDefaultChannelGroup"],
        ["sessions", "conversions"],
        [(["/buy", "Organic Search"], ["200", "18"]), (["/blog", "Organic Search"], ["500", "4"])],
    )
    client = make_ga4_client(resp)
    result = ga4_tools.ga4_landing_page_conversions({"days": 30}, _cfg(make_config), {"ga4": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["organic_only"] is True
    assert data["days"] == 30
    assert data["rows"][0] == {"dimensions": ["/buy", "Organic Search"], "metrics": [200, 18]}
    req = client._analytics.requests[0]
    assert [d.name for d in req.dimensions] == ["landingPage", "sessionDefaultChannelGroup"]
    assert [m.name for m in req.metrics] == ["sessions", "conversions"]
    assert req.dimension_filter.filter.string_filter.value == "Organic Search"
    assert req.order_bys[0].metric.metric_name == "conversions"
    assert req.order_bys[0].desc is True


def test_landing_page_conversions_organic_off_drops_filter(make_config, make_ga4_client, ga4_response):
    resp = ga4_response(["landingPage", "sessionDefaultChannelGroup"], ["sessions", "conversions"], [])
    client = make_ga4_client(resp)
    ga4_tools.ga4_landing_page_conversions({"organic_only": False}, _cfg(make_config), {"ga4": client})
    req = client._analytics.requests[0]
    assert not req.dimension_filter.filter.field_name
