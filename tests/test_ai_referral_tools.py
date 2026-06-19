"""Offline tests for ai_referral_overview (roadmap Track A, Wave 1)."""

from __future__ import annotations

from typing import Any

from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import ai_referral_tools


class FakeRobotsHttp:
    """Returns one robots.txt body for any robots.txt URL (incl. cache-bust)."""

    def __init__(self, body: str) -> None:
        self.body = body

    def fetch(self, url: str, **_: Any) -> HttpResponse:
        assert "robots.txt" in url
        return HttpResponse(
            status=200,
            headers={"content-type": "text/plain"},
            body_bytes=self.body.encode("utf-8"),
            final_url=url,
        )


def test_ga4_referral_classifies_and_shares(make_config, make_ga4_client, ga4_response):
    report = ga4_response(
        ["sessionSource", "sessionMedium"],
        ["sessions", "conversions"],
        [
            (["chatgpt.com", "referral"], [50, 2]),
            (["google", "organic"], [1000, 10]),
            (["perplexity.ai", "ai-assistant"], [20, 1]),
        ],
    )
    totals = ga4_response([], ["sessions"], [([], [1070])])
    ga4 = make_ga4_client([report, totals])
    res = ai_referral_tools.ai_referral_overview(
        {"property_id": "properties/123"}, make_config(), {"ga4": ga4}
    )
    d = res["data"]
    sec = d["ai_referral"]
    engines = {r["engine"]: r for r in sec["by_source"]}
    assert "ChatGPT" in engines and engines["ChatGPT"]["sessions"] == 50
    assert "Perplexity" in engines
    assert "google" not in {e.lower() for e in engines}  # organic excluded
    assert sec["ai_sessions"] == 70
    assert sec["total_sessions"] == 1070
    assert round(sec["share_of_traffic"], 4) == round(70 / 1070, 4)
    # no site_url -> coverage not run
    assert d["crawl_coverage"] is None


def test_crawl_coverage_flags_blocked_search_crawler(make_config):
    robots = "User-agent: PerplexityBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    clients = {"http": FakeRobotsHttp(robots)}
    res = ai_referral_tools.ai_referral_overview(
        {"site_url": "https://example.com/"}, make_config(), clients
    )
    d = res["data"]
    assert d["ai_referral"] is None  # no GA4
    cov = {c["crawler"]: c for c in d["crawl_coverage"]}
    assert cov["PerplexityBot"]["allowed"] is False
    assert cov["GPTBot"]["allowed"] is True
    assert any("blocks AI search crawlers" in c for c in d["caveats"])


def test_no_robots_means_all_allowed(make_config):
    class No404Http:
        def fetch(self, url: str, **_: Any) -> HttpResponse:
            return HttpResponse(404, {"content-type": "text/plain"}, b"", url)

    res = ai_referral_tools.ai_referral_overview(
        {"site_url": "https://example.com/"}, make_config(), {"http": No404Http()}
    )
    cov = res["data"]["crawl_coverage"]
    assert cov and all(c["allowed"] for c in cov)


def test_requires_property_or_site(make_config):
    res = ai_referral_tools.ai_referral_overview({}, make_config(), {})
    assert res["ok"] is False
    assert res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_sc_domain_default_site_derives_root(make_config):
    robots = "User-agent: *\nAllow: /\n"
    cfg = make_config(SEO_MCP_GSC_DEFAULT_SITE="sc-domain:example.com")
    res = ai_referral_tools.ai_referral_overview({}, cfg, {"http": FakeRobotsHttp(robots)})
    assert res["data"]["site_root"] == "https://example.com/"
    assert res["data"]["crawl_coverage"] is not None
