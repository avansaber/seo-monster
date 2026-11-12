"""Offline contracts for the optional SerpBase client and routing."""

from __future__ import annotations

import json
from urllib.parse import quote, unquote

import pytest

from seo_mcp.clients import serpbase
from seo_mcp.clients.errors import ApiError
from seo_mcp.config import load_config
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import ai_citation_tools, serp_adjacency_tools


@pytest.fixture(autouse=True)
def reset_process_limit(monkeypatch):
    monkeypatch.setattr(serpbase, "_remaining", 1)


def _cfg(**env):
    return load_config(env=env, config_path="/nonexistent-serpbase-config")


def _response(**overrides):
    value = {"status": 0, "query": "query", "page": 1, "credits_charged": 1, "organic": [], "people_also_ask": [], "related_searches": []}
    value.update(overrides)
    return json.dumps(value).encode()


def test_selection_is_explicit_and_secret_repr_is_redacted():
    cfg = _cfg(SERPBASE_API_KEY="synthetic-secret")
    assert serpbase.selected_serp_client(cfg, {"dataforseo": "existing"}) == ("dataforseo", "existing")
    assert "synthetic-secret" not in repr(cfg)
    assert serpbase.selected_serp_client(_cfg(SEO_MCP_SERP_PROVIDER="serpbase", SERPBASE_API_KEY="x"), {"serpbase": "new"}) == ("serpbase", "new")
    with pytest.raises(ApiError):
        serpbase.selected_serp_client(_cfg(SEO_MCP_SERP_PROVIDER="unknown"), {})
    with pytest.raises(ApiError):
        serpbase.selected_serp_client(_cfg(SEO_MCP_SERP_PROVIDER="serpbase"), {})


@pytest.mark.parametrize("selection", ["dataforseo", "DATAFORSEO"])
def test_explicit_dataforseo_selection_matches_default(selection):
    cfg = _cfg(SEO_MCP_SERP_PROVIDER=selection)
    assert serpbase.selected_serp_client(cfg, {"dataforseo": "existing"}) == ("dataforseo", "existing")
    assert serpbase.selected_serp_client(cfg, {}) == ("dataforseo", None)


def test_selector_env_precedence_and_empty_file_fallback(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[serp]\nprovider = "serpbase"\n[serpbase]\napi_key = "synthetic-file-key"\n')
    cfg = load_config(env={"SEO_MCP_SERP_PROVIDER": " ", "SERPBASE_API_KEY": "synthetic-serp-key"}, config_path=str(path))
    assert cfg.serp_provider == "serpbase" and cfg.serpbase_api_key == "synthetic-serp-key"


def test_unresolved_optional_host_fields_do_not_override_file_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[serp]\nprovider = "serpbase"\n[serpbase]\napi_key = "synthetic-file-key"\n')
    env = {"SEO_MCP_SERP_PROVIDER": "${user_config.serp_provider}",
           "SERPBASE_API_KEY": "${user_config.serpbase_api_key}"}
    cfg = load_config(env=env, config_path=str(path))
    assert cfg.serp_provider == "serpbase" and cfg.serpbase_api_key == "synthetic-file-key"
    empty = load_config(env=env, config_path=str(tmp_path / "missing.toml"))
    assert empty.serp_provider is None and empty.serpbase_api_key is None
    selected = load_config(env={**env, "SEO_MCP_SERP_PROVIDER": "serpbase"}, config_path=str(tmp_path / "missing.toml"))
    assert selected.serpbase_api_key is None
    with pytest.raises(ApiError):
        serpbase.selected_serp_client(selected, {})
    invalid = _cfg(SEO_MCP_SERP_PROVIDER="${user_config.other}")
    with pytest.raises(ApiError):
        serpbase.selected_serp_client(invalid, {})


def test_default_and_explicit_dataforseo_degrade_equally_on_client_failure(monkeypatch):
    from seo_mcp.tools import onpage_serp_gap_tools

    class Clients(dict):
        def get(self, key, default=None):
            if key == "dataforseo":
                raise RuntimeError("synthetic provider initialization failure")
            return super().get(key, default)

    clients = Clients(http=object())
    monkeypatch.setattr(serp_adjacency_tools, "_autocomplete", lambda http, seed: (["suggestion"], True))
    monkeypatch.setattr(onpage_serp_gap_tools, "_fetch_parsed", lambda *args: (None, {}))
    cases = [
        (serp_adjacency_tools.serp_adjacency_expand, {"seeds": ["one"]}),
        (ai_citation_tools.ai_citation_track, {"prompts": ["one"], "brand": "Example"}),
        (onpage_serp_gap_tools.onpage_serp_gap, {"target_url": "https://example.test/", "query": "one"}),
    ]
    for tool, args in cases:
        default = tool(args, _cfg(), clients)
        explicit = tool(args, _cfg(SEO_MCP_SERP_PROVIDER="dataforseo"), clients)
        assert explicit == default


def test_success_normalizes_only_safe_urls_and_preserves_unknown(monkeypatch):
    data = _response(query="widgets", organic=[{"rank": 1, "url": "https://example.test/a", "title": "Example"},
                              {"rank": 2, "url": "http://127.0.0.1/private"}], ai_overview=None)
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, data))
    result = serpbase.SerpBaseClient("synthetic-secret").serp("widgets")
    assert result["organic"][0]["url"] == "https://example.test/a"
    assert result["provider_meta"]["unsafe_urls_skipped"] == 1
    assert result["ai_overview_present"] is None
    assert result["provider_meta"]["credits_charged"] == 1
    assert "synthetic-secret" not in str(result)


def test_provider_text_does_not_expose_percent_encoded_api_key(monkeypatch):
    key = "synthetic+key"
    encoded = quote(key, safe="")
    data = _response(
        organic=[{"url": "https://example.test/a", "title": f"title {encoded}"},
                 {"url": "https://example.test/b", "title": "keep %20 unchanged"}],
        people_also_ask=[{"question": f"question {encoded}"}],
        related_searches=[f"related {encoded}"],
        ai_overview={"summary": f"summary {encoded}", "sources": [
            {"link": "https://example.test/c", "title": f"citation {encoded}"}
        ]},
    )
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, data))
    result = serpbase.SerpBaseClient(key).serp("query")
    assert key not in unquote(json.dumps(result))
    assert result["organic"][1]["title"] == "keep %20 unchanged"


@pytest.mark.parametrize("depth", [2, 9])
def test_provider_values_do_not_expose_nested_encoded_api_key(monkeypatch, depth):
    key = "synthetic+key"
    encoded = key
    for _ in range(depth):
        encoded = quote(encoded, safe="")
    data = _response(
        organic=[{"url": f"https://example.test/?value={encoded}", "title": "example"},
                 {"url": "https://example.test/safe", "title": "keep %20 unchanged"}],
        people_also_ask=[{"question": f"question {encoded}"}],
        ai_overview={"summary": f"summary {encoded}", "sources": [
            {"link": f"https://example.test/?value={encoded}", "title": "citation"}
        ]},
    )
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, data))
    result = serpbase.SerpBaseClient(key).serp("query")
    rendered = json.dumps(result)
    for _ in range(depth + 1):
        rendered = unquote(rendered)
        assert key not in rendered
    assert result["organic"][0]["title"] == "keep %20 unchanged"


def test_missing_modules_remain_unknown(monkeypatch):
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, b"{\"status\":0,\"query\":\"query\",\"page\":1}"))
    result = serpbase.SerpBaseClient("x").serp("query")
    assert result["organic"] is None and result["paa"] is None and result["ai_overview_present"] is None
    assert result["provider_meta"]["charge"] == "unknown"
    assert result["provider_meta"]["organic_incomplete"] is None


@pytest.mark.parametrize(("status", "code"), [
    (400, ErrorCode.INVALID_INPUT), (401, ErrorCode.AUTH_INVALID),
    (403, ErrorCode.AUTH_INVALID), (404, ErrorCode.NOT_FOUND),
    (429, ErrorCode.RATE_LIMITED), (500, ErrorCode.UPSTREAM_ERROR),
])
def test_http_failure_maps_status_and_keeps_charge_unknown(monkeypatch, status, code):
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (status, b"synthetic-secret"))
    with pytest.raises(ApiError) as error:
        serpbase.SerpBaseClient("synthetic-secret").serp("query")
    assert error.value.code == code
    assert error.value.details["charge"] == "unknown"
    assert "synthetic-secret" not in str(error.value.to_envelope("technical"))


@pytest.mark.parametrize("request_id,accepted", [
    ("r-0123456789abcdef01234567", True),
    ("01234567-89ab-cdef-0123-456789abcdef", True),
    ("synthetic-secret", False),
    ("<untrusted-request>", False),
])
def test_request_id_accepts_only_safe_formats(monkeypatch, request_id, accepted):
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, _response(request_id=request_id)))
    result = serpbase.SerpBaseClient("synthetic-secret").serp("query")
    assert (result["provider_meta"].get("request_id") == request_id) is accepted
    assert "synthetic-secret" not in str(result)


@pytest.mark.parametrize(("source", "missing", "unsafe"), [
    ({"title": "No link"}, 1, 0),
    ({"title": "Private link", "link": "http://127.0.0.1/private"}, 0, 1),
])
def test_dropped_overview_citations_make_result_incomplete(monkeypatch, source, missing, unsafe):
    raw = _response(ai_overview={"summary": "No brand mentioned", "sources": [source], "truncated": False})
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, raw))
    result = serpbase.SerpBaseClient("synthetic-secret").serp("query")
    meta = result["provider_meta"]
    assert meta["missing_citation_urls"] == missing
    assert meta["unsafe_citation_urls"] == unsafe
    assert meta["overview_truncated"] is True
    assert result["ai_overview_citations"] == []


def test_dropped_citation_never_produces_confident_negative(monkeypatch):
    raw = _response(query="one", ai_overview={"summary": "No brand mentioned", "sources": [{"title": "No link"}]})
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, raw))
    cfg = _cfg(SEO_MCP_SERP_PROVIDER="serpbase", SERPBASE_API_KEY="synthetic-secret")
    result = ai_citation_tools.ai_citation_track(
        {"prompts": ["one"], "brand": "Example", "engines": ["google_aio"], "samples": 1},
        cfg, {"serpbase": serpbase.SerpBaseClient("synthetic-secret")},
    )
    row = result["data"]["by_engine"][0]
    assert row["ai_overview_presence"]["truncated"] == 1
    assert row["brand_visibility"]["value"] is None


@pytest.mark.parametrize("status", [1000, 1001, 1004, 1020, 1029, 1500, 1502, 1503, 1504, 9999])
def test_business_error_never_becomes_empty_success(monkeypatch, status):
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, _response(status=status, credits_charged=0)))
    with pytest.raises(ApiError) as error:
        serpbase.SerpBaseClient("synthetic-secret").serp("query")
    assert error.value.details["provider_status"] == status
    assert "synthetic-secret" not in str(error.value.to_envelope("technical"))


@pytest.mark.parametrize("raw", [b"not-json", b"{}", b"{\"status\":\"0\"}", b"{\"status\":0,\"organic\":{}}"])
def test_malformed_response_fails_closed(monkeypatch, raw):
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, raw))
    with pytest.raises(ApiError):
        serpbase.SerpBaseClient("x").serp("query")


def test_no_retry_and_process_limit_after_timeout(monkeypatch):
    calls = []
    def fail(self, body):
        calls.append(body)
        raise TimeoutError("synthetic-secret")
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", fail)
    client = serpbase.SerpBaseClient("x")
    with pytest.raises(ApiError) as first:
        client.serp("query")
    with pytest.raises(ApiError) as second:
        client.serp("query")
    assert len(calls) == 1
    assert first.value.details["charge"] == "unknown"
    assert second.value.details["dispatch"] == "not_dispatched"
    assert "synthetic-secret" not in str(first.value)


def test_redirect_and_oversized_responses_fail_without_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (calls.append(body) or (302, b"")))
    with pytest.raises(ApiError):
        serpbase.SerpBaseClient("x").serp("query")
    assert len(calls) == 1


def test_serp_adjacency_rejects_fanout_before_dispatch(monkeypatch):
    class Provider:
        def serp(self, query):
            pytest.fail("paid request attempted")
    cfg = _cfg(SEO_MCP_SERP_PROVIDER="serpbase", SERPBASE_API_KEY="x")
    result = serp_adjacency_tools.serp_adjacency_expand({"seeds": ["one", "two"], "include_paa": True}, cfg, {"serpbase": Provider(), "http": object()})
    assert result["ok"] is False and result["error"]["code"] == ErrorCode.INVALID_INPUT


def test_ai_overview_fanout_is_reported_without_paid_dispatch():
    class Provider:
        def serp(self, query):
            pytest.fail("paid request attempted")
    cfg = _cfg(SEO_MCP_SERP_PROVIDER="serpbase", SERPBASE_API_KEY="x")
    result = ai_citation_tools.ai_citation_track({"prompts": ["one", "two"], "brand": "Example", "engines": ["google_aio"], "samples": 1}, cfg, {"serpbase": Provider()})
    assert result["ok"] is True
    assert "one prompt" in result["data"]["by_engine"][0]["unavailable_reason"]


@pytest.mark.parametrize("status,charge", [(0, None), (0, 0), (1502, 1)])
def test_ambiguous_or_contradictory_charges_are_unknown(monkeypatch, status, charge):
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, _response(status=status, credits_charged=charge)))
    if status == 0:
        assert serpbase.SerpBaseClient("x").serp("query")["provider_meta"]["charge"] == "unknown"
    else:
        with pytest.raises(ApiError) as error:
            serpbase.SerpBaseClient("x").serp("query")
        assert error.value.details["charge"] == "unknown"
        assert error.value.details["credits_reported"] == 1
        assert error.value.details["charge_inconsistent"] is True


def test_malicious_provider_echo_cannot_return_configured_key(monkeypatch):
    raw = _response(organic=[{"url": "https://example.test/?key=synthetic-secret", "title": "synthetic-secret"}],
                    related_searches=["synthetic-secret"],
                    ai_overview={"summary": "synthetic-secret", "sources": []})
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, raw))
    result = serpbase.SerpBaseClient("synthetic-secret").serp("query")
    assert "synthetic-secret" not in str(result)
    assert result["provider_meta"]["unsafe_urls_skipped"] == 1


@pytest.mark.parametrize("field", ["organic", "citation"])
def test_provider_url_does_not_echo_raw_percent_sequence_in_key(monkeypatch, field):
    key = "synthetic%41secret"
    url = f"https://example.test/?token={key}"
    if field == "organic":
        raw = _response(organic=[{"url": url, "title": "Result"}])
    else:
        raw = _response(ai_overview={"summary": "Answer", "sources": [{"link": url, "title": "Result"}]})
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, raw))
    result = serpbase.SerpBaseClient(key).serp("query")
    assert key not in str(result)
    assert result["provider_meta"]["unsafe_urls_skipped"] == 1


def test_fixed_transport_does_not_follow_redirect(monkeypatch):
    calls = []
    class Response:
        status = 302
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit): return b""
    class Connection:
        def __init__(self, host, timeout):
            calls.append((host, timeout))
        def request(self, method, path, body, headers):
            calls.append((method, path, headers.get("X-API-Key")))
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(serpbase.http.client, "HTTPSConnection", Connection)
    with pytest.raises(ApiError):
        serpbase.SerpBaseClient("synthetic-secret").serp("query")
    assert len(calls) == 2 and calls[0][0] == "api.serpbase.dev"
    assert calls[1] == ("POST", "/google/search", "synthetic-secret")


def test_autocomplete_only_ignores_unavailable_selected_provider(monkeypatch):
    monkeypatch.setattr(serp_adjacency_tools, "_autocomplete", lambda http, seed: (["suggestion"], True))
    cfg = _cfg(SEO_MCP_SERP_PROVIDER="serpbase")
    result = serp_adjacency_tools.serp_adjacency_expand({"seeds": ["one"], "include_paa": False}, cfg, {"http": object()})
    assert result["ok"] is True and result["data"]["source_status"]["paa_related"] == "not requested"


def test_adjacency_retains_observed_module_when_other_missing(monkeypatch):
    monkeypatch.setattr(serp_adjacency_tools, "_autocomplete", lambda http, seed: ([], True))
    class Provider:
        def serp(self, query):
            return {"paa": ["What is it?"], "related": None, "provider_meta": {"provider": "serpbase"}}
    cfg = _cfg(SEO_MCP_SERP_PROVIDER="serpbase", SERPBASE_API_KEY="x")
    result = serp_adjacency_tools.serp_adjacency_expand({"seeds": ["one"], "include_paa": True}, cfg, {"http": object(), "serpbase": Provider()})
    assert result["data"]["per_seed"][0]["paa"] == ["What is it?"]
    assert result["data"]["per_seed"][0]["related"] is None
    assert result["data"]["per_seed"][0]["related_status"] == "unavailable"


def test_serpbase_competitor_fetch_uses_public_only():
    from seo_mcp.clients.http import HttpResponse
    from seo_mcp.tools.onpage_serp_gap_tools import onpage_serp_gap
    class Http:
        def __init__(self): self.calls = []
        def fetch(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return HttpResponse(200, {}, b"<html><body><h2>Example</h2>words</body></html>", url)
    class Provider:
        def serp(self, query):
            return {"organic": [{"url": "https://competitor.test/", "domain": "competitor.test"}],
                    "ai_overview_present": None, "result_types": None,
                    "provider_meta": {"provider": "serpbase", "charge": "unknown"}}
    http = Http()
    cfg = _cfg(SEO_MCP_SERP_PROVIDER="serpbase", SERPBASE_API_KEY="x")
    result = onpage_serp_gap({"target_url": "https://example.test/", "query": "example"}, cfg, {"http": http, "serpbase": Provider()})
    assert result["ok"] is True
    assert http.calls[1][1]["public_only"] is True
    assert result["data"]["source"] == "serpbase_serp"


def test_invalid_key_fails_before_dispatch():
    with pytest.raises(ApiError) as error:
        serpbase.SerpBaseClient("invalid\nkey")
    assert error.value.code == ErrorCode.AUTH_INVALID


def test_oversized_response_is_not_parsed(monkeypatch):
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, b"x" * (serpbase._MAX_BODY + 1)))
    with pytest.raises(ApiError) as error:
        serpbase.SerpBaseClient("x").serp("query")
    assert error.value.details["charge"] == "unknown"


def test_missing_ai_overview_is_not_counted_as_absent():
    class Provider:
        def serp(self, query):
            return {"ai_overview_present": None, "ai_overview_text": None,
                    "ai_overview_citations": None, "provider_meta": {"provider": "serpbase"}}
    cfg = _cfg(SEO_MCP_SERP_PROVIDER="serpbase", SERPBASE_API_KEY="x")
    result = ai_citation_tools.ai_citation_track(
        {"prompts": ["one"], "brand": "Example", "engines": ["google_aio"], "samples": 1},
        cfg, {"serpbase": Provider()},
    )
    row = result["data"]["by_engine"][0]
    assert row["ai_overview_presence"]["not_shown"] == 0
    assert row["ai_overview_presence"]["failed"] == 0
    assert row["ai_overview_presence"]["not_observed"] == 1
    assert row["brand_visibility"]["value"] is None


def test_mismatched_query_is_not_attributed_to_request(monkeypatch):
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, _response(query="unexpected-query-canary")))
    with pytest.raises(ApiError) as error:
        serpbase.SerpBaseClient("x").serp("query")
    assert "unexpected-query-canary" not in str(error.value.to_envelope("technical"))
    assert error.value.details["credits_charged"] == 1


def test_truncated_overview_is_not_scored_as_complete():
    class Provider:
        def serp(self, query):
            return {"ai_overview_present": True, "ai_overview_text": "partial", "ai_overview_citations": [],
                    "provider_meta": {"provider": "serpbase", "overview_truncated": True}}
    cfg = _cfg(SEO_MCP_SERP_PROVIDER="serpbase", SERPBASE_API_KEY="x")
    result = ai_citation_tools.ai_citation_track(
        {"prompts": ["one"], "brand": "Example", "engines": ["google_aio"], "samples": 1},
        cfg, {"serpbase": Provider()},
    )
    row = result["data"]["by_engine"][0]
    assert row["ai_overview_presence"]["truncated"] == 1
    assert row["brand_visibility"]["value"] is None


def test_query_echo_allows_equivalent_unicode_forms(monkeypatch):
    monkeypatch.setattr(serpbase.SerpBaseClient, "_request", lambda self, body: (200, _response(query="caf\u00e9")))
    result = serpbase.SerpBaseClient("x").serp("cafe\u0301")
    assert result["provider_meta"]["provider"] == "serpbase"
