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
    assert data["properties"][0] == {
        "site_url": SITE,
        "permission_level": "siteOwner",
        "writable": True,
    }
    # siteFullUser is also writable.
    assert data["properties"][1]["writable"] is True


def test_list_properties_writable_false_for_restricted(make_config, make_gsc_client, gsc_payloads):
    payloads = {
        **gsc_payloads,
        "sites_list": {
            "siteEntry": [
                {"siteUrl": "sc-domain:x.com", "permissionLevel": "siteRestrictedUser"},
                {"siteUrl": "sc-domain:y.com", "permissionLevel": "siteUnverifiedUser"},
            ]
        },
    }
    client = make_gsc_client(payloads)
    data = gsc_tools.gsc_list_properties({}, make_config(), {"gsc": client})["data"]
    assert [p["writable"] for p in data["properties"]] == [False, False]


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


def test_search_analytics_accepts_days_alias(make_config, make_gsc_client, gsc_payloads):
    from datetime import date, timedelta

    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_search_analytics({"days": 7}, _cfg(make_config), {"gsc": client})
    assert result["ok"] is True
    expected_start = (date.today() - timedelta(days=7)).isoformat()
    assert result["data"]["start_date"] == expected_start
    _, kw = client._service.calls[0]
    assert kw["body"]["startDate"] == expected_start


def test_search_analytics_accepts_limit_alias(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    gsc_tools.gsc_search_analytics({"limit": 7}, _cfg(make_config), {"gsc": client})
    _, kw = client._service.calls[0]
    assert kw["body"]["rowLimit"] == 7


def test_search_analytics_start_date_wins_over_days(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    gsc_tools.gsc_search_analytics(
        {"start_date": "2026-01-01", "end_date": "2026-01-31", "days": 365},
        _cfg(make_config),
        {"gsc": client},
    )
    _, kw = client._service.calls[0]
    assert kw["body"]["startDate"] == "2026-01-01"


def test_compare_periods_accepts_days_and_limit_aliases(make_config, make_gsc_client):
    empty = {"rows": []}
    client = make_gsc_client({"search": [empty, empty]})
    result = gsc_tools.gsc_compare_periods(
        {"days": 7, "limit": 5}, _cfg(make_config), {"gsc": client}
    )
    assert result["ok"] is True
    # Both compare queries used the alias-derived row_limit.
    for _, kw in client._service.calls:
        assert kw["body"]["rowLimit"] == 5


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


def _compare_payload(rows):
    return {"rows": [
        {"keys": [r[0]], "clicks": r[1], "impressions": r[2], "ctr": r[3], "position": r[4]}
        for r in rows
    ]}


def test_compare_periods_min_delta_clicks_filters(make_config, make_gsc_client):
    current = _compare_payload([("big-mover", 100, 1000, 0.10, 3.0),
                                ("small-mover", 11, 100, 0.11, 5.0)])
    prior = _compare_payload([("big-mover", 50, 800, 0.06, 4.0),
                              ("small-mover", 10, 90, 0.11, 5.0)])
    client = make_gsc_client({"search": [current, prior]})
    result = gsc_tools.gsc_compare_periods({"min_delta_clicks": 5}, _cfg(make_config), {"gsc": client})
    keys = [r["keys"][0] for r in result["data"]["rows"]]
    assert keys == ["big-mover"]
    assert result["data"]["total_matched"] == 2
    assert result["data"]["matched_count"] == 1


def test_compare_periods_sort_dir_asc_for_losers(make_config, make_gsc_client):
    # Two rows: one gained clicks, one lost clicks. sort_dir=asc surfaces the
    # loser at the top of the list.
    current = _compare_payload([("gainer", 90, 900, 0.1, 4.0),
                                ("loser",  10, 200, 0.05, 8.0)])
    prior = _compare_payload([("gainer", 30, 600, 0.05, 6.0),
                              ("loser",  80, 400, 0.20, 5.0)])
    client = make_gsc_client({"search": [current, prior]})
    result = gsc_tools.gsc_compare_periods({"sort_dir": "asc"}, _cfg(make_config), {"gsc": client})
    keys = [r["keys"][0] for r in result["data"]["rows"]]
    assert keys == ["loser", "gainer"]


def test_compare_periods_sort_by_delta_position_asc_finds_rank_gains(make_config, make_gsc_client):
    # delta_position < 0 means a better SERP position. asc sort surfaces the
    # biggest rank gains first.
    current = _compare_payload([("climber", 50, 500, 0.10, 3.0),
                                ("slipper", 50, 500, 0.10, 12.0)])
    prior = _compare_payload([("climber", 50, 500, 0.10, 9.0),
                              ("slipper", 50, 500, 0.10, 6.0)])
    client = make_gsc_client({"search": [current, prior]})
    result = gsc_tools.gsc_compare_periods({"sort_by": "delta_position", "sort_dir": "asc"},
                                           _cfg(make_config), {"gsc": client})
    keys = [r["keys"][0] for r in result["data"]["rows"]]
    assert keys == ["climber", "slipper"]


def test_compare_periods_anomalies_only_keeps_outliers(make_config, make_gsc_client):
    # 9 small moves + 1 huge mover. With sigma_threshold=2 the huge mover
    # should be retained; the others filtered out.
    current = _compare_payload(
        [(f"q-{i}", 11, 100, 0.11, 5.0) for i in range(9)] +
        [("huge", 500, 1000, 0.5, 3.0)]
    )
    prior = _compare_payload(
        [(f"q-{i}", 10, 100, 0.10, 5.0) for i in range(9)] +
        [("huge", 50, 100, 0.5, 6.0)]
    )
    client = make_gsc_client({"search": [current, prior]})
    result = gsc_tools.gsc_compare_periods(
        {"anomalies_only": True, "sigma_threshold": 2.0},
        _cfg(make_config), {"gsc": client},
    )
    keys = [r["keys"][0] for r in result["data"]["rows"]]
    assert "huge" in keys
    assert all(k != "q-0" for k in keys)  # the small-move rows are gone
    assert result["data"]["filters_applied"]["anomalies_only"] is True
    assert result["data"]["filters_applied"]["sigma_used"] is not None


def test_compare_periods_top_caps_returned_rows(make_config, make_gsc_client):
    current = _compare_payload([(f"q-{i}", 100 - i, 1000, 0.1, 3.0) for i in range(20)])
    prior = _compare_payload([(f"q-{i}", 10, 900, 0.1, 5.0) for i in range(20)])
    client = make_gsc_client({"search": [current, prior]})
    result = gsc_tools.gsc_compare_periods({"top": 3}, _cfg(make_config), {"gsc": client})
    assert result["data"]["matched_count"] == 3
    assert result["data"]["total_matched"] == 20


def test_compare_periods_defaults_are_no_op(make_config, make_gsc_client):
    # Round-trip the original v0.1.x test as a regression guard: with no new
    # flags set, results should still be identical to the original behaviour.
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
    assert result["data"]["matched_count"] == 1
    assert result["data"]["unmatched"]["only_current"] == [["b"]]
    assert result["data"]["unmatched"]["only_prior"] == [["c"]]


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


def test_submit_sitemap_ungated_feedpath_backcompat(make_config, make_gsc_client, gsc_payloads):
    # No destructive flag set; submit must still work (un-gated write).
    # `feedpath` still accepted as a back-compat alias.
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_submit_sitemap(
        {"feedpath": "https://www.example.com/sitemap.xml"}, _cfg(make_config), {"gsc": client}
    )
    assert result["ok"] is True
    assert result["data"]["submitted"] is True
    assert result["data"]["sitemap_url"] == "https://www.example.com/sitemap.xml"
    op, kw = client._service.calls[0]
    assert op == "submit"
    assert kw["feedpath"] == "https://www.example.com/sitemap.xml"


def test_submit_sitemap_accepts_sitemap_url_alias(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_submit_sitemap(
        {"sitemap_url": "https://www.example.com/sitemap.xml"}, _cfg(make_config), {"gsc": client}
    )
    assert result["ok"] is True
    assert result["data"]["sitemap_url"] == "https://www.example.com/sitemap.xml"


def test_submit_sitemap_sitemap_url_wins_over_feedpath(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    gsc_tools.gsc_submit_sitemap(
        {"sitemap_url": "https://example.com/new.xml", "feedpath": "https://example.com/old.xml"},
        _cfg(make_config),
        {"gsc": client},
    )
    _, kw = client._service.calls[0]
    assert kw["feedpath"] == "https://example.com/new.xml"


def test_submit_sitemap_requires_one_of(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_submit_sitemap({}, _cfg(make_config), {"gsc": client})
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "sitemap_url" in result["error"]["message"]


# --- request_indexing -----------------------------------------------------


def test_request_indexing_ungated_success(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_request_indexing(
        {"urls": ["https://www.example.com/new"]}, _cfg(make_config), {"gsc": client}
    )
    assert result["ok"] is True
    assert result["data"]["submitted_count"] == 1
    assert result["data"]["submitted"][0]["notify_time"] == "2026-05-27T12:00:00Z"


def test_request_indexing_accepts_singular_url_alias(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_request_indexing(
        {"url": "https://www.example.com/new"}, _cfg(make_config), {"gsc": client}
    )
    assert result["ok"] is True
    assert result["data"]["submitted_count"] == 1
    assert result["data"]["submitted"][0]["url"] == "https://www.example.com/new"


def test_request_indexing_requires_url_or_urls(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_request_indexing({}, _cfg(make_config), {"gsc": client})
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "url" in result["error"]["message"]


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


def test_property_scope_403_maps_to_not_found(make_config, make_gsc_client, gsc_payloads, fake_http_error):
    # Round-2 feedback 7a.i: when GSC returns 403 with a 'URL not under the
    # verified property' message, that is a scope mismatch, not bad creds.
    # The mapper should NOT route it to AUTH_INVALID.
    responses = dict(gsc_payloads)
    responses["inspect"] = fake_http_error(
        403,
        "Forbidden. The URL is not under the verified site / property scope.",
    )
    client = make_gsc_client(responses)
    result = gsc_tools.gsc_inspect_url(
        {"url": "https://other.example.com/page"}, _cfg(make_config), {"gsc": client}
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"
    assert "property" in result["error"]["remediation"].lower()
    assert "site_url" in result["error"]["remediation"]


def test_property_scope_403_handles_google_actual_text(make_config, make_gsc_client, gsc_payloads, fake_http_error):
    # Round-3 feedback 8b: the verbatim text Google returns from URL Inspection
    # when the inspected URL is outside the property's scope. The v0.1.1
    # markers were hypothetical and missed this exact phrasing, sending the
    # error through to AUTH_INVALID. Pin the real text here so the regression
    # cannot recur.
    responses = dict(gsc_payloads)
    responses["inspect"] = fake_http_error(
        403,
        "You do not own this site, or the inspected URL is not part of this property.",
    )
    client = make_gsc_client(responses)
    result = gsc_tools.gsc_inspect_url(
        {"url": "https://www.avansaber.com/about.php"}, _cfg(make_config), {"gsc": client}
    )
    assert result["error"]["code"] == "NOT_FOUND"
    assert "site_url" in result["error"]["remediation"]


def test_generic_403_still_maps_to_auth_invalid(make_config, make_gsc_client, gsc_payloads, fake_http_error):
    # The new branch is property-scope-specific; an unrelated 403 still goes
    # to AUTH_INVALID. Guards against over-broad remap.
    responses = dict(gsc_payloads)
    responses["sites_list"] = fake_http_error(403, "Insufficient permissions for this account.")
    client = make_gsc_client(responses)
    result = gsc_tools.gsc_list_properties({}, _cfg(make_config), {"gsc": client})
    assert result["error"]["code"] == "AUTH_INVALID"


# --- v0.2.0 query intelligence -------------------------------------------


def _qa_payload(rows):
    return {"rows": [
        {"keys": [r[0]], "clicks": r[1], "impressions": r[2], "ctr": r[3], "position": r[4]}
        for r in rows
    ]}


def test_query_opportunities_filters_and_sorts(make_config, make_gsc_client, gsc_payloads):
    # Rows: (query, clicks, impressions, ctr, position)
    payload = _qa_payload([
        ("a-top-low-ctr",       3, 1000, 0.003, 6.0),    # passes: pos<=10, ctr<=.03, impr>=100
        ("b-top-low-ctr-bigger",5, 5000, 0.001, 5.0),    # passes; should sort first (more impressions)
        ("c-top-but-ctr-fine",  90, 1000, 0.09, 4.0),    # rejected: CTR above threshold
        ("d-below-rank",        2, 1000, 0.002, 25.0),   # rejected: position > 10
        ("e-low-volume",        1, 50,   0.02, 7.0),     # rejected: impressions < 100
    ])
    responses = dict(gsc_payloads); responses["search"] = payload
    client = make_gsc_client(responses)
    result = gsc_tools.gsc_query_opportunities({}, _cfg(make_config), {"gsc": client})
    assert result["ok"] is True
    keys = [r["keys"][0] for r in result["data"]["rows"]]
    assert keys == ["b-top-low-ctr-bigger", "a-top-low-ctr"]
    assert result["data"]["filters_applied"] == {"position_max": 10.0, "ctr_max": 0.03, "impressions_min": 100}


def test_query_opportunities_respects_custom_thresholds(make_config, make_gsc_client, gsc_payloads):
    payload = _qa_payload([("ok", 1, 200, 0.005, 8.0)])  # passes both default and custom
    responses = dict(gsc_payloads); responses["search"] = payload
    client = make_gsc_client(responses)
    # With ctr_max=0.001, the row is rejected.
    result = gsc_tools.gsc_query_opportunities({"ctr_max": 0.001}, _cfg(make_config), {"gsc": client})
    assert result["data"]["row_count"] == 0


def test_query_gaps_filters_and_sorts(make_config, make_gsc_client, gsc_payloads):
    payload = _qa_payload([
        ("a-many-impr-no-click", 0, 500, 0.0, 14.0),   # passes
        ("b-some-clicks",        5, 800, 0.006, 11.0),  # rejected: clicks > 2
        ("c-no-impressions",     0, 10,  0.0, 30.0),   # rejected: impressions < 50
        ("d-also-passes",        1, 100, 0.01, 8.0),   # passes; should sort below "a"
    ])
    responses = dict(gsc_payloads); responses["search"] = payload
    client = make_gsc_client(responses)
    result = gsc_tools.gsc_query_gaps({}, _cfg(make_config), {"gsc": client})
    assert [r["keys"][0] for r in result["data"]["rows"]] == ["a-many-impr-no-click", "d-also-passes"]


def test_new_queries_returns_only_unseen_in_prior(make_config, make_gsc_client):
    current = _qa_payload([
        ("brand-new",   2, 50, 0.04, 12.0),
        ("seen-before", 4, 80, 0.05, 9.0),
    ])
    prior = _qa_payload([
        ("seen-before", 1, 30, 0.03, 14.0),
        ("ancient",     0, 5,  0.0,  40.0),
    ])
    client = make_gsc_client({"search": [current, prior]})
    result = gsc_tools.gsc_new_queries({"days": 7, "prior_days": 28, "impressions_min": 1},
                                       _cfg(make_config), {"gsc": client})
    keys = [r["keys"][0] for r in result["data"]["rows"]]
    assert keys == ["brand-new"]
    # Two calls (current + prior) with non-overlapping date windows.
    assert len(client._service.calls) == 2
    a, b = (c[1]["body"] for c in client._service.calls)
    assert a["startDate"] != b["startDate"]


def test_top_pages_by_query_requires_query(make_config, make_gsc_client, gsc_payloads):
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_top_pages_by_query({}, _cfg(make_config), {"gsc": client})
    assert result["error"]["code"] == "INVALID_INPUT"


def test_top_pages_by_query_builds_query_filter(make_config, make_gsc_client, gsc_payloads):
    payload = {"rows": [
        {"keys": ["https://x/blog/a"], "clicks": 5, "impressions": 100, "ctr": 0.05, "position": 4.0},
        {"keys": ["https://x/blog/b"], "clicks": 1, "impressions": 30, "ctr": 0.03, "position": 9.0},
    ]}
    responses = dict(gsc_payloads); responses["search"] = payload
    client = make_gsc_client(responses)
    result = gsc_tools.gsc_top_pages_by_query({"query": "seo monster", "days": 30}, _cfg(make_config), {"gsc": client})
    assert result["ok"] is True
    assert result["data"]["query"] == "seo monster"
    assert result["data"]["row_count"] == 2
    _, kw = client._service.calls[0]
    body = kw["body"]
    assert body["dimensions"] == ["page"]
    groups = body["dimensionFilterGroups"]
    assert groups[0]["filters"][0] == {"dimension": "query", "operator": "equals", "expression": "seo monster"}


def test_query_intelligence_returns_auth_missing_when_unconfigured(make_config):
    cfg = make_config()
    for tool in ("gsc_query_opportunities", "gsc_query_gaps",
                 "gsc_new_queries", "gsc_top_pages_by_query"):
        handler = gsc_tools.HANDLERS[tool]
        result = handler({"query": "x"}, cfg, {})
        assert result["error"]["code"] == "AUTH_MISSING", tool


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


# --- v0.5.0 multi-property + lifecycle ------------------------------------


def test_portfolio_summary_aggregates_per_property(make_config, make_gsc_client, gsc_payloads):
    # Two visible properties; both succeed with the same one-row aggregate.
    payloads = dict(gsc_payloads)
    payloads["search"] = {"rows": [{"keys": [], "clicks": 100, "impressions": 5000, "ctr": 0.02, "position": 6.0}]}
    client = make_gsc_client(payloads)
    result = gsc_tools.gsc_portfolio_summary({"days": 14}, make_config(), {"gsc": client})
    assert result["ok"] is True
    d = result["data"]
    assert d["days"] == 14
    assert d["portfolio_totals"]["property_count"] == 2
    assert d["portfolio_totals"]["clicks"] == 200  # 100 + 100
    assert d["portfolio_totals"]["impressions"] == 10000  # 5000 + 5000
    sites = {p["site_url"] for p in d["properties"]}
    assert sites == {"sc-domain:example.com", "https://www.example.com/"}


def test_portfolio_summary_include_filter(make_config, make_gsc_client, gsc_payloads):
    payloads = dict(gsc_payloads)
    payloads["search"] = {"rows": [{"keys": [], "clicks": 100, "impressions": 5000, "ctr": 0.02, "position": 6.0}]}
    client = make_gsc_client(payloads)
    result = gsc_tools.gsc_portfolio_summary(
        {"days": 14, "include": ["sc-domain:example.com"]},
        make_config(), {"gsc": client},
    )
    assert result["data"]["portfolio_totals"]["property_count"] == 1
    assert result["data"]["properties"][0]["site_url"] == "sc-domain:example.com"


def test_portfolio_summary_collects_per_property_errors(make_config, make_gsc_client, gsc_payloads, fake_http_error):
    """Per-property failures should not poison the rollup; they go to .errors."""
    payloads = dict(gsc_payloads)
    payloads["search"] = [
        # first property: success
        {"rows": [{"keys": [], "clicks": 50, "impressions": 1000, "ctr": 0.05, "position": 4.0}]},
        # second property: 403
        fake_http_error(403, "no access"),
    ]
    client = make_gsc_client(payloads)
    result = gsc_tools.gsc_portfolio_summary({"days": 7}, make_config(), {"gsc": client})
    d = result["data"]
    assert len(d["properties"]) == 1
    assert len(d["errors"]) == 1
    assert d["errors"][0]["code"] == "AUTH_INVALID"


def test_trending_pages_translates_to_compare_periods(make_config, make_gsc_client, gsc_payloads):
    """The trending wrapper should hit compare_periods with dimensions=["page"],
    sort_dir=desc, top=limit. We verify by checking the returned filters_applied."""
    payloads = dict(gsc_payloads)
    payloads["search"] = {"rows": [{"keys": ["https://example.com/a"], "clicks": 50, "impressions": 800, "ctr": 0.06, "position": 5.0}]}
    cfg = _cfg(make_config)
    client = make_gsc_client(payloads)
    result = gsc_tools.gsc_trending_pages({"days": 14, "limit": 5}, cfg, {"gsc": client})
    assert result["ok"] is True
    filters = result["data"]["filters_applied"]
    assert filters["sort_by"] == "delta_impressions"
    assert filters["sort_dir"] == "desc"
    assert filters["top"] == 5


def test_decaying_pages_uses_ascending_sort(make_config, make_gsc_client, gsc_payloads):
    payloads = dict(gsc_payloads)
    payloads["search"] = {"rows": [{"keys": ["https://example.com/a"], "clicks": 50, "impressions": 800, "ctr": 0.06, "position": 5.0}]}
    cfg = _cfg(make_config)
    client = make_gsc_client(payloads)
    result = gsc_tools.gsc_decaying_pages({"days": 28}, cfg, {"gsc": client})
    filters = result["data"]["filters_applied"]
    assert filters["sort_dir"] == "asc"
    assert filters["sort_by"] == "delta_impressions"


def test_coverage_audit_rolls_up_verdicts(make_config, make_gsc_client, gsc_payloads):
    """Each URL is inspected; the rollup tallies verdict_counts and
    coverage_state_counts. Three URLs all pass."""
    cfg = _cfg(make_config)
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_coverage_audit(
        {"urls": ["https://example.com/a", "https://example.com/b", "https://example.com/c"]},
        cfg, {"gsc": client},
    )
    assert result["ok"] is True
    d = result["data"]
    assert d["audited_count"] == 3
    assert d["failed_count"] == 0
    assert d["verdict_counts"]["PASS"] == 3
    assert "Submitted and indexed" in d["coverage_state_counts"]


def test_coverage_audit_collects_per_url_failures(make_config, make_gsc_client, gsc_payloads, fake_http_error):
    """A per-URL failure should not fail the whole call; it goes to .failed."""
    payloads = dict(gsc_payloads)
    payloads["inspect"] = [
        gsc_payloads["inspect"],
        fake_http_error(403),
    ]
    cfg = _cfg(make_config)
    client = make_gsc_client(payloads)
    result = gsc_tools.gsc_coverage_audit(
        {"urls": ["https://example.com/a", "https://example.com/dead"]},
        cfg, {"gsc": client},
    )
    d = result["data"]
    assert d["audited_count"] == 1
    assert d["failed_count"] == 1
    assert d["failed"][0]["url"] == "https://example.com/dead"


def test_coverage_audit_rejects_empty_urls(make_config, make_gsc_client, gsc_payloads):
    cfg = _cfg(make_config)
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_coverage_audit({"urls": []}, cfg, {"gsc": client})
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


# --- Round 6 §11b.i regression guard ------------------------------------


def test_gsc_client_inspect_url_is_kwargs_only():
    """Round 6 §11b.i: an arg-order swap in gsc_coverage_audit returned 100%
    NOT_FOUND on a real account because mocked tests accept any positional
    arg. The kwargs-only signature on GscClient.inspect_url turns the bug
    class into a TypeError. This test pins the keyword-only contract so a
    refactor can't silently break it again."""
    import inspect as inspect_module
    from seo_mcp.clients.gsc import GscClient

    sig = inspect_module.signature(GscClient.inspect_url)
    params = sig.parameters
    # 'self' plus url and site_url, all named.
    assert set(params) == {"self", "url", "site_url"}
    # url and site_url must be KEYWORD_ONLY.
    assert params["url"].kind is inspect_module.Parameter.KEYWORD_ONLY, (
        "GscClient.inspect_url(url, ...) must be keyword-only; "
        "see Round 6 §11b.i regression"
    )
    assert params["site_url"].kind is inspect_module.Parameter.KEYWORD_ONLY, (
        "GscClient.inspect_url(site_url=...) must be keyword-only; "
        "see Round 6 §11b.i regression"
    )


def test_coverage_audit_passes_url_not_site_to_inspect(make_config, make_gsc_client, gsc_payloads):
    """Round 6 §11b.i: assert each per-URL inspect call is invoked with the
    correct URL as the ``url`` kwarg. Captures the kwargs from the recorded
    fake-service calls; if a future refactor passes the property string
    instead of the URL, this test fails."""
    cfg = _cfg(make_config)
    client = make_gsc_client(gsc_payloads)
    result = gsc_tools.gsc_coverage_audit(
        {"urls": ["https://example.com/a", "https://example.com/b"]},
        cfg, {"gsc": client},
    )
    assert result["ok"] is True
    # FakeGscService records every call as (op_name, kwargs_dict).
    inspect_calls = [
        call for call in client._service.calls if call[0] == "inspect"
    ]
    assert len(inspect_calls) == 2
    bodies = [call[1]["body"] for call in inspect_calls]
    # inspectionUrl field carries the URL we asked about, NOT the property.
    assert bodies[0]["inspectionUrl"] == "https://example.com/a"
    assert bodies[0]["siteUrl"] == SITE
    assert bodies[1]["inspectionUrl"] == "https://example.com/b"
    assert bodies[1]["siteUrl"] == SITE
