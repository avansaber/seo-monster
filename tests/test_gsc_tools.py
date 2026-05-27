"""Offline tests for the 10 GSC tools, driven through the tool handlers with a
fake Search Console / Indexing service. No network, no googleapiclient build."""

from __future__ import annotations

import pytest

from seo_mcp.tools import gsc_tools


SITE = "sc-domain:example.com"


def _cfg(make_config, **extra):
    return make_config(SEO_MCP_GSC_DEFAULT_SITE=SITE, **extra)


# --- auth gating ----------------------------------------------------------


def test_tools_return_auth_missing_when_unconfigured(make_config):
    cfg = make_config()  # no Google creds, no client wired
    for name, handler in gsc_tools.HANDLERS.items():
        result = handler({"url": "u", "urls": ["u"], "feedpath": "f"}, cfg, {})
        assert result["ok"] is False, name
        assert result["error"]["code"] == "AUTH_MISSING", name
        assert result["error"]["service"] == "gsc"


# --- list_properties ------------------------------------------------------


def test_list_properties(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_list_properties({}, make_config(), {"gsc": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["count"] == 2
    assert data["properties"][0] == {"site_url": SITE, "permission_level": "siteOwner"}


# --- search_analytics -----------------------------------------------------


def test_search_analytics_defaults_and_shape(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_search_analytics({}, _cfg(make_config), {"gsc": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["site_url"] == SITE
    assert data["dimensions"] == ["query"]
    assert data["data_state"] == "all"
    assert data["row_count"] == 2
    assert data["rows"][0]["clicks"] == 120
    # The body sent to the API carries the defaults.
    op, kw = client._service.calls[0]
    assert op == "search"
    body = kw["body"]
    assert body["dimensions"] == ["query"]
    assert body["rowLimit"] == 1000
    assert body["type"] == "web"
    assert body["dataState"] == "all"


def test_search_analytics_uses_explicit_site_and_data_state(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    args = {"site_url": "https://other.com/", "data_state": "final", "dimensions": ["page", "query"]}
    result = gsc_tools.gsc_search_analytics(args, _cfg(make_config), {"gsc": client})
    assert result["data"]["site_url"] == "https://other.com/"
    op, kw = client._service.calls[0]
    assert kw["siteUrl"] == "https://other.com/"
    assert kw["body"]["dataState"] == "final"
    assert kw["body"]["dimensions"] == ["page", "query"]


def test_search_analytics_data_state_defaults_from_config(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    cfg = _cfg(make_config, SEO_MCP_DATA_STATE="final")
    gsc_tools.gsc_search_analytics({}, cfg, {"gsc": client})
    _, kw = client._service.calls[0]
    assert kw["body"]["dataState"] == "final"


def test_search_analytics_builds_filter_groups(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    args = {"filters": [{"dimension": "country", "operator": "equals", "expression": "usa"}]}
    gsc_tools.gsc_search_analytics(args, _cfg(make_config), {"gsc": client})
    _, kw = client._service.calls[0]
    groups = kw["body"]["dimensionFilterGroups"]
    assert groups == [
        {"groupType": "and", "filters": [{"dimension": "country", "operator": "equals", "expression": "usa"}]}
    ]


def test_search_analytics_missing_site_returns_invalid_input(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_search_analytics({}, make_config(), {"gsc": client})
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


# --- top_queries / top_pages ----------------------------------------------


def test_top_queries(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_top_queries({"days": 7, "limit": 1}, _cfg(make_config), {"gsc": client})
    assert result["ok"] is True
    assert result["data"]["dimension"] == "query"
    assert result["data"]["days"] == 7
    assert result["data"]["row_count"] == 1  # limited to 1
    _, kw = client._service.calls[0]
    assert kw["body"]["dimensions"] == ["query"]
    assert kw["body"]["rowLimit"] == 1


def test_top_pages_uses_page_dimension(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    gsc_tools.gsc_top_pages({}, _cfg(make_config), {"gsc": client})
    _, kw = client._service.calls[0]
    assert kw["body"]["dimensions"] == ["page"]


# --- compare_periods ------------------------------------------------------


def test_compare_periods_deltas(make_config, make_gsc_client):
    current = {"rows": [
        {"keys": ["a"], "clicks": 100, "impressions": 1000, "ctr": 0.10, "position": 3.0},
        {"keys": ["b"], "clicks": 10, "impressions": 200, "ctr": 0.05, "position": 9.0},
    ]}
    prior = {"rows": [
        {"keys": ["a"], "clicks": 60, "impressions": 800, "ctr": 0.075, "position": 4.0},
        {"keys": ["c"], "clicks": 5, "impressions": 100, "ctr": 0.05, "position": 12.0},
    ]}
    client = make_gsc_client({"search": [current, prior]})
    result = gsc_tools.gsc_compare_periods({}, _cfg(make_config), {"gsc": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["matched_count"] == 1
    row = data["rows"][0]
    assert row["keys"] == ["a"]
    assert row["delta_clicks"] == 40
    assert row["delta_impressions"] == 200
    assert row["delta_position"] == -1.0  # 3.0 - 4.0, improvement
    assert data["unmatched"]["only_current"] == [["b"]]
    assert data["unmatched"]["only_prior"] == [["c"]]
    # Two queries were issued with distinct windows.
    assert len(client._service.calls) == 2
    assert client._service.calls[0][1]["body"]["startDate"] != client._service.calls[1][1]["body"]["startDate"]


# --- inspect_url ----------------------------------------------------------


def test_inspect_url(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_inspect_url({"url": "https://www.example.com/page"}, _cfg(make_config), {"gsc": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["verdict"] == "PASS"
    assert data["coverage_state"] == "Submitted and indexed"
    assert data["crawled_as"] == "MOBILE"
    assert data["mobile_usability_verdict"] == "PASS"


def test_inspect_url_requires_url(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_inspect_url({}, _cfg(make_config), {"gsc": client})
    assert result["error"]["code"] == "INVALID_INPUT"


# --- batch_inspect --------------------------------------------------------


def test_batch_inspect_collects_results_and_failures(make_config, make_gsc_client, gsc_payloads, fake_http_error):
    # First URL succeeds, second raises a 404 -> recorded in failed.
    responses = dict(gsc_payloads)
    responses["inspect"] = [gsc_payloads["inspect"], fake_http_error(404)]
    client = make_gsc_client(responses)
    result = gsc_tools.gsc_batch_inspect_urls(
        {"urls": ["https://www.example.com/ok", "https://www.example.com/missing"]},
        _cfg(make_config),
        {"gsc": client},
    )
    assert result["ok"] is True
    assert result["data"]["inspected"] == 1
    assert len(result["data"]["failed"]) == 1
    assert result["data"]["failed"][0]["code"] == "NOT_FOUND"


def test_batch_inspect_rejects_oversized_batch(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_batch_inspect_urls({"urls": [f"u{i}" for i in range(26)]}, _cfg(make_config), {"gsc": client})
    assert result["error"]["code"] == "INVALID_INPUT"


# --- sitemaps -------------------------------------------------------------


def test_list_sitemaps(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_list_sitemaps({}, _cfg(make_config), {"gsc": client})
    assert result["ok"] is True
    assert result["data"]["count"] == 1
    assert result["data"]["sitemaps"][0]["path"] == "https://www.example.com/sitemap.xml"
    assert result["data"]["sitemaps"][0]["is_sitemaps_index"] is True


def test_submit_sitemap_ungated(make_config, make_gsc_client, gsc_payloads):
    # No destructive flag set; submit must still work (un-gated write).
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_submit_sitemap(
        {"feedpath": "https://www.example.com/sitemap.xml"}, _cfg(make_config), {"gsc": client}
    )
    assert result["ok"] is True
    assert result["data"]["submitted"] is True
    op, kw = client._service.calls[0]
    assert op == "submit"
    assert kw["feedpath"] == "https://www.example.com/sitemap.xml"


def test_submit_sitemap_requires_feedpath(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_submit_sitemap({}, _cfg(make_config), {"gsc": client})
    assert result["error"]["code"] == "INVALID_INPUT"


# --- request_indexing -----------------------------------------------------


def test_request_indexing_ungated_success(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_request_indexing(
        {"urls": ["https://www.example.com/new"]}, _cfg(make_config), {"gsc": client}
    )
    assert result["ok"] is True
    assert result["data"]["submitted_count"] == 1
    assert result["data"]["submitted"][0]["notify_time"] == "2026-05-27T12:00:00Z"


def test_request_indexing_scope_error_stops_batch(make_config, make_gsc_client, gsc_payloads, fake_http_error):
    responses = dict(gsc_payloads)
    responses["publish"] = fake_http_error(403, "ACCESS_TOKEN_SCOPE_INSUFFICIENT: need indexing scope")
    client = make_gsc_client(responses)
    result = gsc_tools.gsc_request_indexing(
        {"urls": ["https://www.example.com/a", "https://www.example.com/b"]},
        _cfg(make_config),
        {"gsc": client},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "SCOPE_INSUFFICIENT"
    # Only the first URL was attempted before stopping.
    assert sum(1 for c in client._service.calls if c[0] == "publish") == 1


def test_request_indexing_service_disabled_extracts_activation_url(make_config, make_gsc_client, gsc_payloads, fake_http_error):
    msg = "SERVICE_DISABLED: Indexing API has not been used in project. https://console.cloud.google.com/apis/api/indexing.googleapis.com/overview?project=123"
    responses = dict(gsc_payloads)
    responses["publish"] = fake_http_error(403, msg)
    client = make_gsc_client(responses)
    result = gsc_tools.gsc_request_indexing({"urls": ["u"]}, _cfg(make_config), {"gsc": client})
    assert result["error"]["code"] == "SERVICE_DISABLED"
    assert "console.cloud.google.com" in result["error"]["details"]["activation_url"]


# --- upstream error mapping -----------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [(401, "AUTH_INVALID"), (403, "AUTH_INVALID"), (404, "NOT_FOUND"), (429, "RATE_LIMITED"), (500, "UPSTREAM_ERROR")],
)
def test_status_mapping_on_list_properties(make_config, make_gsc_client, gsc_payloads, fake_http_error, status, expected):
    responses = dict(gsc_payloads)
    responses["sites_list"] = fake_http_error(status)
    client = make_gsc_client(responses)
    result = gsc_tools.gsc_list_properties({}, make_config(), {"gsc": client})
    assert result["ok"] is False
    assert result["error"]["code"] == expected
