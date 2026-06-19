"""Offline tests for keyword_universe (roadmap Track B, Wave 3)."""

from __future__ import annotations

from seo_mcp.errors import ErrorCode
from seo_mcp.tools import keyword_universe_tools as kut


class FakeDFS:
    def __init__(self, ranked, overview):
        self._ranked = ranked
        self._overview = overview

    def ranked_keywords(self, domain, **_):
        return self._ranked.get(domain, [])

    def keyword_overview(self, keywords, **_):
        return self._overview


def test_competitor_gap_and_volume(make_config):
    dfs = FakeDFS(
        ranked={
            "example.com": [{"keyword": "blue widgets", "search_volume": 1200, "position": 4}],
            "comp.com": [
                {"keyword": "blue widgets", "search_volume": 1200, "position": 2},  # we already rank -> not a gap
                {"keyword": "widget pricing", "search_volume": 500, "position": 3},  # gap
            ],
        },
        overview=[{"keyword": "widget pricing", "search_volume": 500, "keyword_difficulty": 20, "intent": "commercial"}],
    )
    res = kut.keyword_universe(
        {"target_domain": "example.com", "competitors": ["comp.com"], "keywords": ["widget pricing"]},
        make_config(),
        {"dataforseo": dfs},
    )
    d = res["data"]
    gap_kw = {g["keyword"] for g in d["competitor_gap"]}
    assert gap_kw == {"widget pricing"}
    assert d["providers"]["competitor_gap"] == "dataforseo"
    assert d["providers"]["volume"] == "dataforseo"
    assert d["volume"][0]["keyword"] == "widget pricing"


def test_gap_collapses_morphological_dupes(make_config):
    # F4: one-concept variants collapse before the limit; distinct concept stays.
    dfs = FakeDFS(
        ranked={
            "example.com": [],
            "comp.com": [
                {"keyword": "advertisement ethos", "search_volume": 100, "position": 3},
                {"keyword": "advertisements ethos", "search_volume": 90, "position": 4},
                {"keyword": "advertising ethos", "search_volume": 80, "position": 5},
                {"keyword": "ad using ethos", "search_volume": 70, "position": 6},
                {"keyword": "ads using ethos", "search_volume": 60, "position": 7},
                {"keyword": "social media marketing", "search_volume": 500, "position": 2},
            ],
        },
        overview=[],
    )
    res = kut.keyword_universe(
        {"target_domain": "example.com", "competitors": ["comp.com"], "limit": 25}, make_config(), {"dataforseo": dfs}
    )
    gap_kws = [g["keyword"] for g in res["data"]["competitor_gap"]]
    assert "social media marketing" in gap_kws
    assert len(gap_kws) <= 3  # advertis-group + ad-group + social media (was 6)
    # the highest-volume representative of the advertis group is kept
    assert "advertisement ethos" in gap_kws


def test_gap_collapses_plurals_and_short_forms(make_config):
    # F4 (Round 3): plural-stem must merge makes->make and ads->ad.
    dfs = FakeDFS(
        ranked={
            "example.com": [],
            "comp.com": [
                {"keyword": "ai make", "search_volume": 4400, "position": 3},
                {"keyword": "ai makes", "search_volume": 4400, "position": 4},
                {"keyword": "ppc ads", "search_volume": 800, "position": 5},
                {"keyword": "ppc ad", "search_volume": 700, "position": 6},
            ],
        },
        overview=[],
    )
    res = kut.keyword_universe(
        {"target_domain": "example.com", "competitors": ["comp.com"], "limit": 25}, make_config(), {"dataforseo": dfs}
    )
    gap_kws = [g["keyword"] for g in res["data"]["competitor_gap"]]
    assert len(gap_kws) == 2  # {ai make, ai makes} -> 1 ; {ppc ad, ppc ads} -> 1
    assert "ai make" in gap_kws


def test_no_provider_auth_missing(make_config):
    res = kut.keyword_universe({"keywords": ["x"]}, make_config(), {})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.AUTH_MISSING


def test_google_ads_only_volume_pending(make_config):
    cfg = make_config(GOOGLE_ADS_DEVELOPER_TOKEN="tok", GOOGLE_ADS_CUSTOMER_ID="123")
    res = kut.keyword_universe({"keywords": ["x"]}, cfg, {})  # no dataforseo client
    d = res["data"]
    assert d["providers"]["volume"] == "google_ads_pending"
    assert any("adwords-scope" in n for n in d["notes"])


def test_validation_requires_keywords_or_gap_inputs(make_config):
    dfs = FakeDFS(ranked={}, overview=[])
    res = kut.keyword_universe({}, make_config(), {"dataforseo": dfs})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.INVALID_INPUT
