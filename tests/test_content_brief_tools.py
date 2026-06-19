"""Offline tests for content_brief_data (roadmap Track C, Wave 2)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import content_brief_tools as cbt

U1 = "https://comp.com/1"
U2 = "https://comp.com/2"


@dataclass
class FakeHttp:
    pages: dict[str, str]
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str, **_: Any) -> HttpResponse:
        self.calls.append(url)
        if url not in self.pages:
            raise AssertionError(f"no page for {url!r}")
        return HttpResponse(200, {"content-type": "text/html"}, self.pages[url].encode("utf-8"), url)


_P1 = """<html><head><title>Blue Widgets Guide</title>
<script type="application/ld+json">{"@type":"Article"}</script></head>
<body><h1>Blue Widgets</h1><h2>Why blue</h2><p>Blue widgets are great for many widget users.</p>
<h2>Pricing</h2><p>Widgets cost money and provide value.</p></body></html>"""

_P2 = """<html><head><title>Widgets 101</title></head>
<body><h1>Widgets</h1><h2>Why blue</h2><p>""" + ("widget " * 60) + """</p>
<h3>Setup</h3><p>Install your widgets carefully and thoroughly today.</p></body></html>"""


def test_competitor_urls_brief(make_config):
    http = FakeHttp({U1: _P1, U2: _P2})
    res = cbt.content_brief_data(
        {"target_query": "blue widgets", "competitor_urls": [U1, U2]}, make_config(), {"http": http}
    )
    d = res["data"]
    assert d["source"] == "competitor_urls"
    assert len(d["analyzed_pages"]) == 2
    wcs = [p["word_count"] for p in d["analyzed_pages"]]
    assert d["evidence"]["target_word_count_floor"] == int(statistics.median(wcs))
    # heading union dedupes "Why blue" and keeps "Pricing" + "Setup"
    union_lower = {h.lower() for h in d["evidence"]["heading_union"]}
    assert "why blue" in union_lower and "pricing" in union_lower and "setup" in union_lower
    assert "Article" in d["evidence"]["schema_types_seen"]
    assert d["evidence"]["entities_to_cover"]
    assert d["geo_directives"] and d["validation_rules"]


def test_own_pages_fallback_via_gsc(make_config, make_gsc_client):
    http = FakeHttp({U1: _P1})
    gsc = make_gsc_client({"search": {"rows": [{"keys": [U1], "clicks": 5, "impressions": 100, "position": 4.0}]}})
    res = cbt.content_brief_data(
        {"target_query": "blue widgets", "site_url": "sc-domain:example.com"},
        make_config(),
        {"http": http, "gsc": gsc},
    )
    d = res["data"]
    assert d["source"] == "own_ranking_pages"
    assert any("OWN ranking pages" in c for c in d["caveats"])
    assert d["analyzed_pages"][0]["url"] == U1


def test_missing_target_query_invalid(make_config):
    res = cbt.content_brief_data({}, make_config(), {"http": FakeHttp({})})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_no_competitors_no_gsc_invalid(make_config):
    # target_query present, http present, but no competitor_urls and no GSC default site.
    res = cbt.content_brief_data({"target_query": "x"}, make_config(), {"http": FakeHttp({})})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_no_http_client_auth_missing(make_config):
    res = cbt.content_brief_data({"target_query": "x", "competitor_urls": [U1]}, make_config(), {})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.AUTH_MISSING
