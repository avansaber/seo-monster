"""Offline tests for the 6 Cloudflare tools, driving the real CfClient through a
fake transport (CfClient._raw_request). Emphasis on the destructive gate (blocks
writes and makes zero calls when off) and the confirm-token logic."""

from __future__ import annotations

import pytest

from seo_mcp.tools import cf_tools


def _cfg(make_config, **extra):
    return make_config(CF_API_TOKEN="testtoken", CF_ZONE="example.com", **extra)


# --- auth gating ----------------------------------------------------------


def test_read_tools_auth_missing_when_no_token(make_config):
    cfg = make_config()  # no CF token, no client wired
    for name in ("cf_list_zones", "cf_zone_info", "cf_list_dns", "cf_web_analytics"):
        result = cf_tools.HANDLERS[name]({}, cfg, {})
        assert result["ok"] is False, name
        assert result["error"]["code"] == "AUTH_MISSING", name
        assert result["error"]["service"] == "cf"


# --- cf_list_zones --------------------------------------------------------


def test_list_zones(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_list_zones({}, _cfg(make_config), {"cf": client})
    assert result["ok"] is True
    assert result["data"]["count"] == 2
    assert result["data"]["zones"][0] == {"name": "example.com", "status": "active", "plan": "Pro", "id": "zone123"}


# --- cf_zone_info ---------------------------------------------------------


def test_zone_info_uses_default_zone(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_zone_info({}, _cfg(make_config), {"cf": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["name"] == "example.com"
    assert data["plan"] == "Pro"
    assert data["name_servers"] == ["ns1.cloudflare.com", "ns2.cloudflare.com"]
    # Resolved via the name-filtered lookup.
    assert any("name=example.com" in path for _, path, _ in client._transport.calls)


def test_zone_info_missing_zone_invalid_input(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    # No zone arg and no CF_ZONE configured.
    cfg = make_config(CF_API_TOKEN="testtoken")
    result = cf_tools.cf_zone_info({}, cfg, {"cf": client})
    assert result["error"]["code"] == "INVALID_INPUT"
    assert client._transport.calls == []  # never resolved


# --- cf_list_dns ----------------------------------------------------------


def test_list_dns(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_list_dns({"type": "TXT"}, _cfg(make_config), {"cf": client})
    assert result["ok"] is True
    assert result["data"]["zone"] == "example.com"
    assert result["data"]["count"] == 2
    assert result["data"]["records"][1]["type"] == "TXT"
    # The type filter is passed through on the dns_records request.
    dns_call = next(c for c in client._transport.calls if "dns_records" in c[1])
    assert "type=TXT" in dns_call[1]


# --- cf_web_analytics -----------------------------------------------------


def test_web_analytics_list_all(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_web_analytics({}, _cfg(make_config), {"cf": client})
    assert result["ok"] is True
    assert result["data"]["account_id"] == "acct123"
    assert result["data"]["count"] == 1
    assert result["data"]["sites"][0]["site_tag"] == "tag-abc"


def test_web_analytics_by_tag(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_web_analytics({"host_or_tag": "tag-abc"}, _cfg(make_config), {"cf": client})
    assert result["ok"] is True
    assert result["data"]["site"]["host"] == "example.com"
    # A bare tag goes straight to the detail endpoint (no list call needed).
    assert not any("rum/site_info/list" in c[1] for c in client._transport.calls)


def test_web_analytics_by_host_resolves_tag(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_web_analytics({"host_or_tag": "example.com"}, _cfg(make_config), {"cf": client})
    assert result["ok"] is True
    assert result["data"]["site"]["site_tag"] == "tag-abc"
    # A hostname triggers a list lookup to find the tag.
    assert any("rum/site_info/list" in c[1] for c in client._transport.calls)


def test_web_analytics_unknown_host_not_found(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_web_analytics({"host_or_tag": "nope.com"}, _cfg(make_config), {"cf": client})
    assert result["error"]["code"] == "NOT_FOUND"


# --- cf_purge_cache: destructive gate -------------------------------------


def test_purge_cache_blocked_when_destructive_off_zero_calls(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    cfg = _cfg(make_config)  # destructive not set -> off
    result = cf_tools.cf_purge_cache({"urls": ["https://example.com/a"]}, cfg, {"cf": client})
    assert result["ok"] is False
    assert result["error"]["code"] == "DESTRUCTIVE_DISABLED"
    # CRITICAL: the blocked purge must not touch the network.
    assert client._transport.calls == []


def test_purge_cache_succeeds_when_enabled(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    cfg = _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true")
    urls = ["https://example.com/a", "https://example.com/b"]
    result = cf_tools.cf_purge_cache({"urls": urls}, cfg, {"cf": client})
    assert result["ok"] is True
    assert result["data"]["zone"] == "example.com"
    assert result["data"]["purged_count"] == 2
    purge_call = next(c for c in client._transport.calls if "purge_cache" in c[1])
    assert purge_call[0] == "POST"
    assert purge_call[2] == {"files": urls}


def test_purge_cache_requires_urls(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    cfg = _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true")
    result = cf_tools.cf_purge_cache({"urls": []}, cfg, {"cf": client})
    assert result["error"]["code"] == "INVALID_INPUT"


# --- cf_purge_cache_all: gate + confirm -----------------------------------


def test_purge_all_blocked_when_destructive_off_zero_calls(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    cfg = _cfg(make_config)  # off
    result = cf_tools.cf_purge_cache_all({"confirm": "example.com"}, cfg, {"cf": client})
    assert result["error"]["code"] == "DESTRUCTIVE_DISABLED"
    assert client._transport.calls == []


def test_purge_all_confirm_mismatch_does_not_purge(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    cfg = _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true")
    result = cf_tools.cf_purge_cache_all({"confirm": "wrong.com"}, cfg, {"cf": client})
    assert result["ok"] is False
    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert result["error"]["details"]["resolved_zone"] == "example.com"
    # The zone was resolved, but no purge request was issued.
    assert not any("purge_cache" in c[1] for c in client._transport.calls)


def test_purge_all_succeeds_with_matching_confirm(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    cfg = _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true")
    result = cf_tools.cf_purge_cache_all({"confirm": "example.com"}, cfg, {"cf": client})
    assert result["ok"] is True
    assert result["data"]["purged"] is True
    purge_call = next(c for c in client._transport.calls if "purge_cache" in c[1])
    assert purge_call[2] == {"purge_everything": True}


# --- error mapping --------------------------------------------------------


def test_resolve_zone_not_found(make_config, make_cf_client, cf_payloads, cf_envelope):
    responses = dict(cf_payloads)
    responses["resolve"] = cf_envelope([])  # empty result -> zone not found
    client = make_cf_client(responses)
    result = cf_tools.cf_zone_info({"zone": "ghost.com"}, _cfg(make_config), {"cf": client})
    assert result["error"]["code"] == "NOT_FOUND"


def test_cf_success_false_maps_to_upstream_error(make_config, make_cf_client, cf_payloads):
    responses = dict(cf_payloads)
    responses["zones"] = {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}], "result": None}
    client = make_cf_client(responses)
    result = cf_tools.cf_list_zones({}, _cfg(make_config), {"cf": client})
    assert result["ok"] is False
    assert result["error"]["code"] == "UPSTREAM_ERROR"
    assert result["error"]["details"]["cf_errors"][0]["code"] == 10000


def test_cf_http_error_maps_via_status(make_config, make_cf_client, cf_payloads):
    from seo_mcp.clients.errors import ApiError
    from seo_mcp.errors import ErrorCode

    responses = dict(cf_payloads)
    responses["zones"] = ApiError(ErrorCode.AUTH_INVALID, "403 forbidden")
    client = make_cf_client(responses)
    result = cf_tools.cf_list_zones({}, _cfg(make_config), {"cf": client})
    assert result["error"]["code"] == "AUTH_INVALID"


# --- client-level HTTP error parsing --------------------------------------


def test_client_error_from_http_extracts_cf_errors():
    from seo_mcp.clients.cloudflare import CfClient

    error = CfClient._error_from_http(403, '{"success": false, "errors": [{"code": 9109, "message": "Unauthorized"}]}')
    assert str(error.code) == "AUTH_INVALID"
    assert error.details["cf_errors"][0]["code"] == 9109


def test_build_cf_client_returns_none_without_token(make_config):
    from seo_mcp.clients.cloudflare import build_cf_client

    assert build_cf_client(make_config()) is None
    assert build_cf_client(_cfg(make_config)) is not None
