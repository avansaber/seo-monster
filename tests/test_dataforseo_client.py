"""Offline tests for the DataForSEO client (roadmap F2/F3). The _raw_request
network seam is replaced with a canned router; no urllib."""

from __future__ import annotations

import pytest

from seo_mcp.clients.dataforseo import DataForSEOClient, build_dataforseo_client
from seo_mcp.clients.errors import ApiError


def _ok(result):
    return {"status_code": 20000, "tasks": [{"status_code": 20000, "result": result}]}


def _client(router):
    c = DataForSEOClient("login", "pass")
    c._raw_request = router  # (method, path, body=None) -> payload
    return c


def test_serp_parses_organic_paa_related_aio():
    payload = _ok([{"items": [
        {"type": "organic", "rank_absolute": 1, "url": "https://a.com/x", "title": "A", "domain": "a.com"},
        {"type": "people_also_ask", "items": [{"title": "how to widget?"}]},
        {"type": "related_searches", "items": ["widget tips", "widget guide"]},
        {"type": "ai_overview"},
    ]}])
    c = _client(lambda m, p, b=None: payload)
    out = c.serp("blue widgets")
    assert out["organic"][0]["url"] == "https://a.com/x"
    assert "how to widget?" in out["paa"]
    assert "widget guide" in out["related"]
    assert out["ai_overview_present"] is True
    assert "organic" in out["result_types"]


def test_serp_dedups_paa_related_and_captures_aio_text():
    # F5: duplicate PAA/related blocks must de-dup. F7: AIO answer text captured.
    payload = _ok([{"items": [
        {"type": "people_also_ask", "items": [{"title": "q1"}, {"title": "q1"}, {"title": "q2"}]},
        {"type": "related_searches", "items": ["r1", "r2", "r1"]},
        {"type": "ai_overview", "text": "Zapier and Make are top tools.",
         "references": [{"url": "https://zapier.com/x", "title": "Z"}]},
    ]}])
    c = _client(lambda m, p, b=None: payload)
    out = c.serp("automation")
    assert out["paa"] == ["q1", "q2"]
    assert out["related"] == ["r1", "r2"]
    assert "Zapier" in out["ai_overview_text"]
    assert out["ai_overview_present"] is True
    assert out["ai_overview_citations"][0]["domain"] == "zapier.com"


def test_keyword_overview_parses_metrics():
    payload = _ok([{"items": [
        {"keyword": "blue widgets", "keyword_info": {"search_volume": 1200},
         "keyword_properties": {"keyword_difficulty": 35}, "search_intent_info": {"main_intent": "informational"}},
    ]}])
    c = _client(lambda m, p, b=None: payload)
    out = c.keyword_overview(["blue widgets"])
    assert out == [{"keyword": "blue widgets", "search_volume": 1200, "keyword_difficulty": 35, "intent": "informational"}]


def test_ranked_keywords_parses():
    payload = _ok([{"items": [
        {"keyword_data": {"keyword": "blue widgets", "keyword_info": {"search_volume": 1200}},
         "ranked_serp_element": {"serp_item": {"rank_absolute": 5}}},
    ]}])
    c = _client(lambda m, p, b=None: payload)
    out = c.ranked_keywords("example.com")
    assert out == [{"keyword": "blue widgets", "search_volume": 1200, "position": 5}]


def test_unwrap_raises_on_top_level_error():
    c = _client(lambda m, p, b=None: {"status_code": 40000, "status_message": "auth"})
    with pytest.raises(ApiError):
        c.serp("x")


def test_unwrap_raises_on_task_error():
    c = _client(lambda m, p, b=None: {"status_code": 20000, "tasks": [{"status_code": 40501, "status_message": "bad"}]})
    with pytest.raises(ApiError):
        c.keyword_overview(["x"])


def test_probe_hits_user_data():
    calls = []

    def router(m, p, b=None):
        calls.append((m, p))
        return _ok([{}])

    c = _client(router)
    assert c.probe() is True
    assert ("GET", "/v3/appendix/user_data") in calls


def test_builder_requires_both_credentials(make_config):
    assert build_dataforseo_client(make_config()) is None
    cfg = make_config(DATAFORSEO_LOGIN="u", DATAFORSEO_PASSWORD="p")
    assert isinstance(build_dataforseo_client(cfg), DataForSEOClient)
    assert build_dataforseo_client(make_config(DATAFORSEO_LOGIN="u")) is None  # password missing
