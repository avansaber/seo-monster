"""Offline tests for serp_adjacency_expand (roadmap Track B, Wave 3)."""

from __future__ import annotations

import json
from typing import Any

from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import serp_adjacency_tools as sat


class FakeAutocompleteHttp:
    def __init__(self, suggestions, *, ok=True):
        self.suggestions = suggestions
        self.ok = ok

    def fetch(self, url: str, **_: Any) -> HttpResponse:
        assert "suggestqueries" in url
        body = json.dumps(["seed", self.suggestions]) if self.ok else "not json"
        return HttpResponse(200, {"content-type": "application/json"}, body.encode("utf-8"), url)


class FakeDFS:
    def __init__(self, paa, related):
        self._r = {"paa": paa, "related": related}

    def serp(self, kw, **_):
        return self._r


def test_free_autocomplete_core(make_config):
    http = FakeAutocompleteHttp(["blue widgets", "blue widgets cheap", "blue widgets review"])
    res = sat.serp_adjacency_expand({"seeds": ["blue widgets"]}, make_config(), {"http": http})
    d = res["data"]
    assert d["per_seed"][0]["autocomplete_status"] == "ok"
    assert set(d["net_new_terms"]) == {"blue widgets cheap", "blue widgets review"}
    assert "autocomplete" in d["source_status"]


def test_dataforseo_paa_enrichment(make_config):
    http = FakeAutocompleteHttp(["blue widgets"])
    dfs = FakeDFS(paa=["how big are widgets?"], related=["widget sizes"])
    res = sat.serp_adjacency_expand({"seeds": ["blue widgets"]}, make_config(), {"http": http, "dataforseo": dfs})
    d = res["data"]
    assert d["per_seed"][0]["paa"] == ["how big are widgets?"]
    assert "how big are widgets?" in d["net_new_terms"]
    assert d["source_status"]["paa_related"] == "dataforseo"


def test_degraded_autocomplete(make_config):
    http = FakeAutocompleteHttp([], ok=False)
    res = sat.serp_adjacency_expand({"seeds": ["x"]}, make_config(), {"http": http})
    assert res["data"]["per_seed"][0]["autocomplete_status"] == "degraded"


def test_empty_seeds_invalid(make_config):
    res = sat.serp_adjacency_expand({"seeds": []}, make_config(), {"http": FakeAutocompleteHttp([])})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_no_http_auth_missing(make_config):
    res = sat.serp_adjacency_expand({"seeds": ["x"]}, make_config(), {})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.AUTH_MISSING
