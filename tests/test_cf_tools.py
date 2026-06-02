"""Offline tests for the 6 Cloudflare tools, driving the real CfClient through a
fake transport (CfClient._raw_request). Emphasis on the destructive gate (blocks
writes and makes zero calls when off) and the confirm-token logic."""

from __future__ import annotations

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


# --- cf_settings_audit ----------------------------------------------------
#
# conftest's FakeCfTransport has no label for the /settings path, so we build a
# small local fake: a real CfClient whose _raw_request returns a canned settings
# envelope for the settings call and the zone envelope for the name-filtered
# resolve. We do NOT edit conftest.


def _zone_envelope():
    return {
        "success": True,
        "errors": [],
        "result": [{"id": "zone123", "name": "example.com"}],
    }


def _settings_envelope(settings):
    return {"success": True, "errors": [], "result": settings}


def _make_settings_client(settings):
    from seo_mcp.clients.cloudflare import CfClient

    client = CfClient(token="testtoken")

    def _raw(method, path, body=None):
        if "/settings" in path:
            return _settings_envelope(settings)
        return _zone_envelope()  # the name-filtered zone resolve

    client._raw_request = _raw
    return client


_BAD_SETTINGS = [
    {"id": "ssl", "value": "flexible"},
    {"id": "always_use_https", "value": "off"},
    {"id": "security_header", "value": {"strict_transport_security": {"enabled": False}}},
    {"id": "automatic_https_rewrites", "value": "off"},
    {"id": "brotli", "value": "off"},
    {"id": "browser_cache_ttl", "value": 0},
]

_GOOD_SETTINGS = [
    {"id": "ssl", "value": "strict"},
    {"id": "always_use_https", "value": "on"},
    {"id": "security_header", "value": {"strict_transport_security": {"enabled": True, "max_age": 31536000}}},
    {"id": "automatic_https_rewrites", "value": "on"},
    {"id": "brotli", "value": "on"},
    {"id": "browser_cache_ttl", "value": 14400},
]


def test_settings_audit_bad_zone_flags_issues(make_config):
    client = _make_settings_client(_BAD_SETTINGS)
    result = cf_tools.cf_settings_audit({}, _cfg(make_config), {"cf": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["zone"] == "example.com"
    assert data["summary"]["verdict"] == "issues"  # ssl + always_https are high
    rule_ids = {f["rule_id"] for f in data["findings"]}
    assert {"cf.ssl_mode", "cf.always_https", "cf.hsts"} <= rule_ids
    # snapshot carries the observed raw values.
    assert data["settings_snapshot"]["ssl"] == "flexible"


def test_settings_audit_hsts_never_critical_when_off(make_config):
    client = _make_settings_client(_BAD_SETTINGS)
    result = cf_tools.cf_settings_audit({}, _cfg(make_config), {"cf": client})
    hsts = next(f for f in result["data"]["findings"] if f["rule_id"] == "cf.hsts")
    # NEVER hard-fail HSTS: it is graded medium, never critical/high.
    assert hsts["severity"] == "medium"
    assert hsts["severity"] not in ("critical", "high")
    assert all(
        f["severity"] != "critical" for f in result["data"]["findings"] if f["rule_id"] == "cf.hsts"
    )


def test_settings_audit_good_zone_is_clean(make_config):
    client = _make_settings_client(_GOOD_SETTINGS)
    result = cf_tools.cf_settings_audit({}, _cfg(make_config), {"cf": client})
    assert result["ok"] is True
    assert result["data"]["findings"] == []
    assert result["data"]["summary"]["verdict"] == "clean"


def test_settings_audit_short_hsts_flagged_medium(make_config):
    settings = list(_GOOD_SETTINGS)
    settings[2] = {"id": "security_header", "value": {"strict_transport_security": {"enabled": True, "max_age": 3600}}}
    client = _make_settings_client(settings)
    result = cf_tools.cf_settings_audit({}, _cfg(make_config), {"cf": client})
    hsts = next(f for f in result["data"]["findings"] if f["rule_id"] == "cf.hsts")
    assert hsts["severity"] == "medium"
    # A short-max-age HSTS does not escalate the verdict to issues by itself.
    assert result["data"]["summary"]["verdict"] == "review"


def test_settings_audit_auth_missing_without_client(make_config):
    result = cf_tools.cf_settings_audit({}, make_config(), {})
    assert result["error"]["code"] == "AUTH_MISSING"


# --- single redirects (cf_list/create/delete_redirect) --------------------

from seo_mcp.clients.errors import ApiError  # noqa: E402
from seo_mcp.clients.http import HttpResponse  # noqa: E402
from seo_mcp.errors import ErrorCode  # noqa: E402


class _FakeHttp:
    """Canned (status) for the redirect-target pre-flight; records URLs."""

    def __init__(self, status: int) -> None:
        self._status = status
        self.calls: list[str] = []

    def fetch(self, url: str, **_):
        self.calls.append(url)
        return HttpResponse(status=self._status, headers={}, body_bytes=b"ok", final_url=url)


def _ok_env(result):
    return {"success": True, "errors": [], "result": result}


def _existing_rule(source="https://example.com/old", target="https://example.com/new", rule_id="rule-existing"):
    return {
        "id": rule_id,
        "expression": f'(http.request.full_uri eq "{source}")',
        "action": "redirect",
        "action_parameters": {"from_value": {"target_url": {"value": target}, "status_code": 301, "preserve_query_string": True}},
    }


def test_list_redirects_shapes_rules(make_config, make_cf_client, cf_payloads):
    responses = dict(cf_payloads)
    responses["redirect_entrypoint"] = _ok_env({"id": "rs-redir-1", "rules": [_existing_rule()]})
    client = make_cf_client(responses)
    result = cf_tools.cf_list_redirects({}, _cfg(make_config), {"cf": client})
    assert result["ok"] is True
    assert result["data"]["count"] == 1
    r = result["data"]["single_redirects"][0]
    assert r["source"] == "https://example.com/old"
    assert r["target"] == "https://example.com/new"
    assert r["status_code"] == 301


def test_create_redirect_blocked_when_destructive_off_zero_calls(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_create_redirect(
        {"source": "https://example.com/a", "target": "https://example.com/b"},
        _cfg(make_config),
        {"cf": client, "http": _FakeHttp(200)},
    )
    assert result["error"]["code"] == "DESTRUCTIVE_DISABLED"
    assert client._transport.calls == []  # zero network


def test_create_redirect_succeeds_appends_rule(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)  # entrypoint exists, empty rules
    http = _FakeHttp(200)
    result = cf_tools.cf_create_redirect(
        {"source": "https://example.com/old", "target": "https://example.com/new"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client, "http": http},
    )
    assert result["ok"] is True
    assert result["data"]["created"] is True
    assert http.calls == ["https://example.com/new"]  # target was pre-flighted
    add_call = next(c for c in client._transport.calls if c[1].endswith("/rules"))
    assert add_call[0] == "POST"
    assert add_call[2]["action"] == "redirect"


def test_create_redirect_creates_ruleset_when_none(make_config, make_cf_client, cf_payloads):
    responses = dict(cf_payloads)
    responses["redirect_entrypoint"] = ApiError(ErrorCode.NOT_FOUND, "no ruleset yet")
    client = make_cf_client(responses)
    result = cf_tools.cf_create_redirect(
        {"source": "https://example.com/old", "target": "https://example.com/new"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client, "http": _FakeHttp(200)},
    )
    assert result["ok"] is True
    # No entrypoint -> POST to /rulesets to create it.
    assert any(c[1].endswith("/rulesets") and c[0] == "POST" for c in client._transport.calls)


def test_create_redirect_rejects_loop_zero_calls(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_create_redirect(
        {"source": "https://example.com/x", "target": "https://example.com/x"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client, "http": _FakeHttp(200)},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "loop" in result["error"]["message"].lower()
    assert client._transport.calls == []


def test_create_redirect_rejects_duplicate_source(make_config, make_cf_client, cf_payloads):
    responses = dict(cf_payloads)
    responses["redirect_entrypoint"] = _ok_env({"id": "rs-redir-1", "rules": [_existing_rule()]})
    client = make_cf_client(responses)
    result = cf_tools.cf_create_redirect(
        {"source": "https://example.com/old", "target": "https://example.com/elsewhere"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client, "http": _FakeHttp(200)},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["details"]["existing_rule_id"] == "rule-existing"


def test_create_redirect_blocks_dead_target(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_create_redirect(
        {"source": "https://example.com/old", "target": "https://example.com/missing"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client, "http": _FakeHttp(404)},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["details"]["preflight_status"] == 404
    # blocked before any redirect write
    assert not any(c[1].endswith("/rules") for c in client._transport.calls)


def test_create_redirect_dry_run_writes_nothing(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_create_redirect(
        {"source": "https://example.com/old", "target": "https://example.com/new", "dry_run": True},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client, "http": _FakeHttp(200)},
    )
    assert result["ok"] is True
    assert result["data"]["dry_run"] is True
    assert result["data"]["would_create"]["target"] == "https://example.com/new"
    assert not any(c[0] == "POST" and "rule" in c[1] for c in client._transport.calls)


def test_create_redirect_skip_preflight_does_not_fetch(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    http = _FakeHttp(404)  # would block, but skipped
    result = cf_tools.cf_create_redirect(
        {"source": "https://example.com/old", "target": "https://example.com/new", "skip_preflight": True},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client, "http": http},
    )
    assert result["ok"] is True
    assert http.calls == []  # pre-flight skipped


def test_create_redirect_302_advisory(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_create_redirect(
        {"source": "https://example.com/old", "target": "https://example.com/new", "status_code": 302},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client, "http": _FakeHttp(200)},
    )
    assert result["ok"] is True
    assert any("301" in a for a in result["data"]["advisories"])


def test_delete_redirect_succeeds(make_config, make_cf_client, cf_payloads):
    responses = dict(cf_payloads)
    responses["redirect_entrypoint"] = _ok_env({"id": "rs-redir-1", "rules": [_existing_rule(rule_id="rule-existing")]})
    client = make_cf_client(responses)
    result = cf_tools.cf_delete_redirect(
        {"rule_id": "rule-existing"}, _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"), {"cf": client}
    )
    assert result["ok"] is True
    assert result["data"]["deleted_rule_id"] == "rule-existing"
    assert any(c[0] == "DELETE" and c[1].endswith("/rules/rule-existing") for c in client._transport.calls)


def test_delete_redirect_unknown_rule_not_found(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)  # entrypoint has no rules
    result = cf_tools.cf_delete_redirect(
        {"rule_id": "nope"}, _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"), {"cf": client}
    )
    assert result["error"]["code"] == "NOT_FOUND"


def test_delete_redirect_blocked_when_destructive_off(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_delete_redirect({"rule_id": "x"}, _cfg(make_config), {"cf": client})
    assert result["error"]["code"] == "DESTRUCTIVE_DISABLED"
    assert client._transport.calls == []


# --- bulk redirects (cf_bulk_redirect_upsert) -----------------------------

_ITEMS = [
    {"source": "https://example.com/old1", "target": "https://example.com/new1"},
    {"source": "https://example.com/old2", "target": "https://example.com/new2", "status_code": 308},
]


def test_bulk_upsert_blocked_when_destructive_off_zero_calls(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_bulk_redirect_upsert(
        {"items": _ITEMS, "list_name": "mylist", "confirm": "mylist"}, _cfg(make_config), {"cf": client}
    )
    assert result["error"]["code"] == "DESTRUCTIVE_DISABLED"
    assert client._transport.calls == []


def test_bulk_upsert_rejects_whole_batch_on_bad_item(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    items = [{"source": "https://example.com/a", "target": "not-a-url"}]
    result = cf_tools.cf_bulk_redirect_upsert(
        {"items": items, "list_name": "mylist", "confirm": "mylist"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["details"]["reject_count"] == 1
    assert client._transport.calls == []  # validated before any network


def test_bulk_upsert_rejects_duplicate_source_in_batch(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    items = [
        {"source": "https://example.com/dup", "target": "https://example.com/a"},
        {"source": "https://example.com/dup", "target": "https://example.com/b"},
    ]
    result = cf_tools.cf_bulk_redirect_upsert(
        {"items": items, "list_name": "mylist", "confirm": "mylist"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "duplicate source" in str(result["error"]["details"]["rejects"])


def test_bulk_upsert_requires_confirm(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_bulk_redirect_upsert(
        {"items": _ITEMS, "list_name": "mylist", "confirm": "wrong"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    # No write happened (no items POST).
    assert not any("/items" in c[1] for c in client._transport.calls)


def test_bulk_upsert_dry_run_writes_nothing(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_bulk_redirect_upsert(
        {"items": _ITEMS, "list_name": "mylist", "dry_run": True},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["ok"] is True
    assert result["data"]["dry_run"] is True
    assert result["data"]["would_upsert_count"] == 2
    assert not any("/items" in c[1] for c in client._transport.calls)


def test_bulk_upsert_succeeds_creates_list_items_and_wires_ruleset(make_config, make_cf_client, cf_payloads):
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_bulk_redirect_upsert(
        {"items": _ITEMS, "list_name": "seomonster_redirects", "confirm": "seomonster_redirects"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["ok"] is True
    assert result["data"]["upserted_count"] == 2
    assert result["data"]["operation_status"] == "completed"
    assert result["data"]["ruleset_wired"] is True
    paths = [c[1] for c in client._transport.calls]
    assert any(p.endswith("/rules/lists") for p in paths)       # created the list
    assert any("/items" in p for p in paths)                    # appended items
    assert any("bulk_operations" in p for p in paths)           # polled the op


def test_bulk_upsert_rejects_invalid_list_name(make_config, make_cf_client, cf_payloads):
    # CF list names allow only [A-Za-z0-9_]; a hyphen must be caught up front
    # with a clear message, not a cryptic CF 10029 (FEEDBACK §22 B-FIND-1).
    client = make_cf_client(cf_payloads)
    result = cf_tools.cf_bulk_redirect_upsert(
        {"items": _ITEMS, "list_name": "site-migration-2026", "confirm": "site-migration-2026"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "underscores" in result["error"]["message"]
    assert client._transport.calls == []  # rejected before any network


def test_list_redirects_includes_bulk_lists(make_config, make_cf_client, cf_payloads):
    responses = dict(cf_payloads)
    responses["bulk_lists"] = _ok_env([{"name": "mylist", "id": "list-1", "num_items": 42, "kind": "redirect"}])
    client = make_cf_client(responses)
    result = cf_tools.cf_list_redirects({}, _cfg(make_config), {"cf": client})
    assert result["ok"] is True
    assert result["data"]["bulk_redirect_lists"][0]["name"] == "mylist"
    assert result["data"]["bulk_redirect_lists"][0]["num_items"] == 42


# --- cf_settings_update (close the audit -> remediate loop) ---------------

_HSTS_OFF = [
    {"id": "ssl", "value": "flexible"},
    {"id": "security_header", "value": {"strict_transport_security": {"enabled": False, "max_age": 0}}},
]
_HSTS_ON = [
    {"id": "ssl", "value": "flexible"},
    {"id": "security_header", "value": {"strict_transport_security": {"enabled": True, "max_age": 31536000, "include_subdomains": True}}},
]


def _make_settings_update_client(before, after):
    from seo_mcp.clients.cloudflare import CfClient

    client = CfClient(token="testtoken")
    state = {"patched": False}
    calls = []

    def _raw(method, path, body=None):
        calls.append((method, path, body))
        if "/settings/" in path and method == "PATCH":
            state["patched"] = True
            return {"success": True, "errors": [], "result": {"id": path.rsplit("/", 1)[1], "value": (body or {}).get("value")}}
        if "/settings" in path:
            return _settings_envelope(after if state["patched"] else before)
        return _zone_envelope()

    client._raw_request = _raw
    client._calls = calls
    return client


def test_settings_update_blocked_when_destructive_off(make_config):
    client = _make_settings_update_client(_HSTS_OFF, _HSTS_ON)
    result = cf_tools.cf_settings_update({"settings": {"brotli": "on"}}, _cfg(make_config), {"cf": client})
    assert result["error"]["code"] == "DESTRUCTIVE_DISABLED"
    assert client._calls == []


def test_settings_update_unknown_key_rejected(make_config):
    client = _make_settings_update_client(_HSTS_OFF, _HSTS_ON)
    result = cf_tools.cf_settings_update(
        {"settings": {"nope": "x"}}, _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"), {"cf": client}
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "Unknown setting" in result["error"]["message"]


def test_settings_update_hsts_raise_requires_confirm(make_config):
    client = _make_settings_update_client(_HSTS_OFF, _HSTS_ON)
    result = cf_tools.cf_settings_update(
        {"settings": {"hsts": {"enabled": True, "max_age": 31536000, "include_subdomains": True}}},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert not any(c[0] == "PATCH" for c in client._calls)


def test_settings_update_hsts_raise_requires_ack(make_config):
    client = _make_settings_update_client(_HSTS_OFF, _HSTS_ON)
    result = cf_tools.cf_settings_update(
        {"settings": {"hsts": {"enabled": True, "max_age": 31536000, "include_subdomains": True}}, "confirm": "example.com"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert "acknowledge_hsts_risk" in result["error"]["remediation"]
    assert not any(c[0] == "PATCH" for c in client._calls)


def test_settings_update_preload_requires_subdomains_and_year(make_config):
    client = _make_settings_update_client(_HSTS_OFF, _HSTS_ON)
    result = cf_tools.cf_settings_update(
        {"settings": {"hsts": {"enabled": True, "max_age": 86400, "preload": True, "include_subdomains": False}}},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "preload" in result["error"]["message"]
    assert client._calls == []  # rejected before any CF call


def test_settings_update_dry_run_writes_nothing(make_config):
    client = _make_settings_update_client(_HSTS_OFF, _HSTS_ON)
    result = cf_tools.cf_settings_update(
        {"settings": {"brotli": "on"}, "dry_run": True},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["ok"] is True
    assert result["data"]["dry_run"] is True
    assert "before" in result["data"] and "after" in result["data"]
    assert not any(c[0] == "PATCH" for c in client._calls)


def test_settings_update_low_risk_no_confirm_needed(make_config):
    client = _make_settings_update_client(_HSTS_OFF, _HSTS_ON)
    result = cf_tools.cf_settings_update(
        {"settings": {"brotli": "on"}}, _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"), {"cf": client}
    )
    assert result["ok"] is True
    assert "brotli" in result["data"]["updated"]
    assert any(c[0] == "PATCH" and c[1].endswith("/settings/brotli") for c in client._calls)


def test_settings_update_ssl_mode_requires_confirm(make_config):
    client = _make_settings_update_client(_HSTS_OFF, _HSTS_ON)
    result = cf_tools.cf_settings_update(
        {"settings": {"ssl_mode": "strict"}}, _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"), {"cf": client}
    )
    assert result["error"]["code"] == "CONFIRM_REQUIRED"


def test_settings_update_hsts_happy_path_clears_finding(make_config):
    client = _make_settings_update_client(_HSTS_OFF, _HSTS_ON)
    result = cf_tools.cf_settings_update(
        {
            "settings": {"hsts": {"enabled": True, "max_age": 31536000, "include_subdomains": True}},
            "confirm": "example.com",
            "acknowledge_hsts_risk": True,
        },
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["ok"] is True
    assert "security_header" in result["data"]["updated"]
    # After the write, the audit no longer flags HSTS -> finding cleared.
    assert all(f["rule_id"] != "cf.hsts" for f in result["data"]["post_update_findings"])
    assert any(c[0] == "PATCH" and c[1].endswith("/settings/security_header") for c in client._calls)


def test_audit_findings_carry_machine_readable_fix_hint(make_config):
    client = _make_settings_client(_BAD_SETTINGS)
    result = cf_tools.cf_settings_audit({}, _cfg(make_config), {"cf": client})
    by_rule = {f["rule_id"]: f for f in result["data"]["findings"]}
    assert by_rule["cf.ssl_mode"]["fix"] == {"setting": "ssl_mode", "recommended": "strict"}
    assert by_rule["cf.hsts"]["fix"]["setting"] == "hsts"
    assert by_rule["cf.hsts"]["fix"]["recommended"]["max_age"] == 31536000


def test_cf_9109_remediation_is_scope_specific():
    # FEEDBACK R19-FIND-2 #1: a CF 9109 (valid token, no access to this zone)
    # gets a scope-specific remediation, not the generic "check the API key".
    from seo_mcp.clients.cloudflare import CfClient

    body = '{"success": false, "errors": [{"code": 9109, "message": "Unauthorized to access requested resource"}]}'
    err = CfClient._error_from_http(403, body)
    assert str(err.code) == "AUTH_INVALID"
    assert "Zone Resources" in (err.remediation or "")


# --- cf_managed_robots (part B: managed robots.txt / Content-Signals) ------

_MR_BEFORE = {
    "fight_mode": False,
    "is_robots_txt_managed": False,
    "cf_robots_variant": "off",
    "ai_bots_protection": "disabled",
    "content_bots_protection": "disabled",
    "crawler_protection": "disabled",
    "stale_zone_configuration": {"derived": True},
}


def _make_mr_client(before=None):
    """Real CfClient driven through a fake transport that models the
    GET -> merge -> PUT round-trip for /bot_management (PUT echoes back the
    merged config that was sent)."""
    from seo_mcp.clients.cloudflare import CfClient

    client = CfClient(token="testtoken")
    state = {"config": dict(before if before is not None else _MR_BEFORE)}
    calls = []

    def _raw(method, path, body=None):
        calls.append((method, path, body))
        if "bot_management" in path and method == "PUT":
            state["config"] = dict(body or {})
            return {"success": True, "errors": [], "result": dict(state["config"])}
        if "bot_management" in path:
            return {"success": True, "errors": [], "result": dict(state["config"])}
        return _zone_envelope()

    client._raw_request = _raw
    client._calls = calls
    return client


def test_managed_robots_get_is_read_only_and_ungated(make_config):
    client = _make_mr_client()
    # destructive OFF: get must still work (it is read-only)
    result = cf_tools.cf_managed_robots({"action": "get"}, _cfg(make_config), {"cf": client})
    assert result["ok"] is True
    assert result["data"]["managed_robots"]["is_robots_txt_managed"] is False
    assert "caveat" in result["data"]
    assert not any(c[0] == "PUT" for c in client._calls)


def test_managed_robots_configure_blocked_when_destructive_off(make_config):
    client = _make_mr_client()
    result = cf_tools.cf_managed_robots(
        {"action": "configure", "managed_robots": True}, _cfg(make_config), {"cf": client}
    )
    assert result["error"]["code"] == "DESTRUCTIVE_DISABLED"
    assert client._calls == []  # gated before any client call


def test_managed_robots_configure_requires_confirm(make_config):
    client = _make_mr_client()
    result = cf_tools.cf_managed_robots(
        {"action": "configure", "managed_robots": True},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert not any(c[0] == "PUT" for c in client._calls)


def test_managed_robots_configure_happy_path_merges_and_writes(make_config):
    client = _make_mr_client()
    # The valid "enable managed robots.txt" combo is managed=true + variant='off'
    # (CF rejects managed=true + policy_only as mutually exclusive; see §28-FIND-1).
    result = cf_tools.cf_managed_robots(
        {
            "action": "configure",
            "managed_robots": True,
            "cf_robots_variant": "off",
            "ai_bots_protection": "block",
            "confirm": "example.com",
        },
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["ok"] is True
    assert result["data"]["after"]["is_robots_txt_managed"] is True
    assert result["data"]["after"]["cf_robots_variant"] == "off"
    assert result["data"]["after"]["ai_bots_protection"] == "block"
    # untouched field is preserved through the GET -> merge -> PUT round-trip
    put = next(c for c in client._calls if c[0] == "PUT")
    assert put[2]["fight_mode"] is False
    # the read-only/derived field is stripped from the PUT body
    assert "stale_zone_configuration" not in put[2]


def test_managed_robots_policy_only_enables_signals_policy(make_config):
    # The other valid combo: the Content-Signals policy on its own.
    client = _make_mr_client()
    result = cf_tools.cf_managed_robots(
        {"action": "configure", "managed_robots": False, "cf_robots_variant": "policy_only", "confirm": "example.com"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["ok"] is True
    assert result["data"]["after"]["is_robots_txt_managed"] is False
    assert result["data"]["after"]["cf_robots_variant"] == "policy_only"


def test_managed_robots_rejects_mutually_exclusive_combo_in_request(make_config):
    # §28-FIND-1: managed=true + policy_only is a CF-invalid combo; reject locally
    # with zero CF calls instead of surfacing a raw 400.
    client = _make_mr_client()
    result = cf_tools.cf_managed_robots(
        {"action": "configure", "managed_robots": True, "cf_robots_variant": "policy_only", "confirm": "example.com"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "mutually" in result["error"]["message"].lower() or "cannot be combined" in result["error"]["message"]
    assert client._calls == []  # rejected before any CF call


def test_managed_robots_rejects_cross_state_conflict(make_config):
    # The zone already has the Content-Signals policy on; turning managed robots on
    # (without also clearing the variant) would create the invalid combo. Reject
    # after the GET but before any PUT.
    client = _make_mr_client({**_MR_BEFORE, "cf_robots_variant": "policy_only"})
    result = cf_tools.cf_managed_robots(
        {"action": "configure", "managed_robots": True, "confirm": "example.com"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert not any(c[0] == "PUT" for c in client._calls)


def test_managed_robots_disable_turns_off_managed_and_policy(make_config):
    client = _make_mr_client({**_MR_BEFORE, "is_robots_txt_managed": True, "cf_robots_variant": "policy_only"})
    result = cf_tools.cf_managed_robots(
        {"action": "disable", "confirm": "example.com"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["ok"] is True
    assert result["data"]["after"]["is_robots_txt_managed"] is False
    assert result["data"]["after"]["cf_robots_variant"] == "off"


def test_managed_robots_dry_run_writes_nothing(make_config):
    client = _make_mr_client()
    result = cf_tools.cf_managed_robots(
        {"action": "configure", "managed_robots": True, "dry_run": True},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["ok"] is True
    assert result["data"]["dry_run"] is True
    assert result["data"]["after"]["is_robots_txt_managed"] is True  # proposed
    assert not any(c[0] == "PUT" for c in client._calls)


def test_managed_robots_configure_rejects_bad_enum(make_config):
    client = _make_mr_client()
    result = cf_tools.cf_managed_robots(
        {"action": "configure", "cf_robots_variant": "bogus", "confirm": "example.com"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "cf_robots_variant" in result["error"]["message"]
    assert not any(c[0] == "PUT" for c in client._calls)


def test_managed_robots_configure_requires_a_field(make_config):
    client = _make_mr_client()
    result = cf_tools.cf_managed_robots(
        {"action": "configure", "confirm": "example.com"},
        _cfg(make_config, SEO_MCP_ALLOW_DESTRUCTIVE="true"),
        {"cf": client},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "at least one field" in result["error"]["message"]


def test_managed_robots_bad_action_rejected(make_config):
    client = _make_mr_client()
    result = cf_tools.cf_managed_robots({"action": "nope"}, _cfg(make_config), {"cf": client})
    assert result["error"]["code"] == "INVALID_INPUT"
    assert client._calls == []
