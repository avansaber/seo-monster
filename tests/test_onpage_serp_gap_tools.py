"""Offline tests for onpage_serp_gap (roadmap Track D, Wave 3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import onpage_serp_gap_tools as osg

TARGET = "https://example.com/p"
A = "https://a.com/x"
B = "https://b.com/y"
REDDIT = "https://reddit.com/r/widgets"

_TARGET_HTML = "<html><body><h1>Widgets</h1><h2>Intro</h2><p>widgets are blue and useful for homes</p></body></html>"
_FAQ = '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
_A_HTML = f"<html><body>{_FAQ}<h2>Pricing</h2><p>widgets cost money with premium plans</p></body></html>"
_B_HTML = f"<html><body>{_FAQ}<h2>Pricing</h2><p>widgets premium plans cost comparison</p></body></html>"
_REDDIT_HTML = "<html><body><h2>discussion</h2><p>widgets thread comments</p></body></html>"


@dataclass
class FakeHttp:
    pages: dict[str, str]
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str, **_: Any) -> HttpResponse:
        self.calls.append(url)
        if url not in self.pages:
            raise AssertionError(f"no page for {url!r}")
        return HttpResponse(200, {"content-type": "text/html"}, self.pages[url].encode("utf-8"), url)


class FakeDFS:
    def __init__(self, serp):
        self._serp = serp

    def serp(self, q, **_):
        return self._serp


class FakeOPR:
    def __init__(self, ranks):
        self._ranks = ranks

    def domain_rank(self, domains):
        return {d: self._ranks.get(d) for d in domains}


def test_provided_competitor_urls(make_config):
    http = FakeHttp({TARGET: _TARGET_HTML, A: _A_HTML, B: _B_HTML})
    res = osg.onpage_serp_gap({"target_url": TARGET, "competitor_urls": [A, B]}, make_config(), {"http": http})
    d = res["data"]
    assert d["source"] == "provided"
    assert "Pricing" in d["gaps"]["headings"]
    assert "FAQPage" in d["gaps"]["schema_types"]
    assert any(t in d["gaps"]["entities"] for t in ("pricing", "premium", "plans"))
    assert any("Pricing" in a for a in d["actions"])
    assert any("information gain" in a.lower() for a in d["actions"])
    assert d["serp_composition"] is None  # not auto-fetched


def test_dataforseo_serp_autofetch_with_composition_and_authority(make_config):
    http = FakeHttp({TARGET: _TARGET_HTML, A: _A_HTML, REDDIT: _REDDIT_HTML})
    serp = {
        "organic": [{"url": A, "domain": "a.com"}, {"url": REDDIT, "domain": "reddit.com"}],
        "ai_overview_present": True,
        "result_types": ["organic", "ai_overview"],
    }
    dfs = FakeDFS(serp)
    opr = FakeOPR({"a.com": 4.0, "reddit.com": 9.0})
    res = osg.onpage_serp_gap(
        {"target_url": TARGET, "query": "blue widgets"}, make_config(), {"http": http, "dataforseo": dfs, "openpagerank": opr}
    )
    d = res["data"]
    assert d["source"] == "dataforseo_serp"
    assert d["serp_composition"]["ai_overview_present"] is True
    assert d["serp_composition"]["ugc_results"] >= 1   # reddit
    assert d["serp_composition"]["zero_click_risk"] is True
    domains = {a["domain"] for a in d["competitor_authority"]}
    assert "reddit.com" in domains


def test_chrome_headings_excluded_from_gaps_and_actions(make_config):
    # F1: nav/CTA headings must not become gaps or actions.
    chrome_comp = ("<html><body><h2>Table of contents</h2><h2>Subscribe to our newsletter</h2>"
                   "<h2>Pricing</h2><p>widgets premium plans cost</p></body></html>")
    http = FakeHttp({TARGET: _TARGET_HTML, A: chrome_comp})
    res = osg.onpage_serp_gap({"target_url": TARGET, "competitor_urls": [A]}, make_config(), {"http": http})
    d = res["data"]
    assert "Pricing" in d["gaps"]["headings"]
    assert "Table of contents" not in d["gaps"]["headings"]
    assert "Subscribe to our newsletter" not in d["gaps"]["headings"]
    assert not any("Table of contents" in a for a in d["actions"])


def test_open_pagerank_queried_with_registrable_domain(make_config):
    # F6: strip www. before the OPR lookup so the score resolves.
    www_url = "https://www.bcg.com/insights"
    serp = {"organic": [{"url": www_url, "domain": "www.bcg.com"}], "ai_overview_present": False, "result_types": ["organic"]}
    http = FakeHttp({TARGET: _TARGET_HTML, www_url: _A_HTML})
    opr = FakeOPR({"bcg.com": 3.0})  # bare domain scores; www.bcg.com would be null
    res = osg.onpage_serp_gap(
        {"target_url": TARGET, "query": "consulting"}, make_config(),
        {"http": http, "dataforseo": FakeDFS(serp), "openpagerank": opr},
    )
    auth = {a["domain"]: a["page_rank"] for a in res["data"]["competitor_authority"]}
    assert auth.get("bcg.com") == 3.0
    assert "www.bcg.com" not in auth


def test_missing_target_invalid(make_config):
    res = osg.onpage_serp_gap({}, make_config(), {"http": FakeHttp({})})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_no_competitors_and_no_query_invalid(make_config):
    http = FakeHttp({TARGET: _TARGET_HTML})
    res = osg.onpage_serp_gap({"target_url": TARGET}, make_config(), {"http": http})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_query_without_dataforseo_auth_missing(make_config):
    http = FakeHttp({TARGET: _TARGET_HTML})
    res = osg.onpage_serp_gap({"target_url": TARGET, "query": "x"}, make_config(), {"http": http})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.AUTH_MISSING


def test_no_http_auth_missing(make_config):
    res = osg.onpage_serp_gap({"target_url": TARGET}, make_config(), {})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.AUTH_MISSING
