"""Offline tests for the Open PageRank client (roadmap F6)."""

from __future__ import annotations

from seo_mcp.clients.openpagerank import OpenPageRankClient, build_openpagerank_client


def _client(payload):
    c = OpenPageRankClient("key")
    c._raw_request = lambda domains: payload
    return c


def test_domain_rank_parses():
    c = _client({"response": [
        {"domain": "a.com", "page_rank_decimal": 4.5, "rank": "100"},
        {"domain": "b.com", "page_rank_decimal": 2.1},
        {"domain": "c.com", "page_rank_decimal": None},  # skipped
    ]})
    out = c.domain_rank(["a.com", "b.com", "c.com"])
    assert out == {"a.com": 4.5, "b.com": 2.1}


def test_domain_rank_empty_input_short_circuits():
    called = []
    c = OpenPageRankClient("key")
    c._raw_request = lambda d: called.append(d) or {"response": []}
    assert c.domain_rank([]) == {}
    assert called == []  # no network call for empty input


def test_probe():
    c = _client({"response": [{"domain": "example.com", "page_rank_decimal": 5.0}]})
    assert c.probe() is True


def test_builder_requires_key(make_config):
    assert build_openpagerank_client(make_config()) is None
    assert isinstance(build_openpagerank_client(make_config(OPENPAGERANK_API_KEY="k")), OpenPageRankClient)
