"""Tests for content_opportunities (Layer 1 content intelligence).

Drives the real handler with a fake GscClient (conftest make_gsc_client backed
by FakeGscService). The handler makes three search_analytics calls in order
(current query rows, prior query rows, current query x page rows), so the
"search" response queue holds three canned payloads."""

from __future__ import annotations

from seo_mcp.tools import content_tools


def _q(query, clicks, impressions, ctr, position):
    return {"keys": [query], "clicks": clicks, "impressions": impressions, "ctr": ctr, "position": position}


def _qp(query, page, impressions):
    return {"keys": [query, page], "clicks": 0, "impressions": impressions, "ctr": 0.0, "position": 1.0}


def _current_rows():
    rows = [
        # Striking-distance star: pos 5, big impressions, very low CTR -> top upside.
        _q("seo audit", clicks=50, impressions=5000, ctr=0.01, position=5.0),
        # High CTR already (above expected) -> ~no upside.
        _q("mcp server", clicks=300, impressions=6000, ctr=0.05, position=5.0),
        # Five queries in bucket 8 to calibrate the CTR curve: total 85 clicks /
        # 5000 impressions -> curve[8] = 0.017. The last one (low CTR) is itself
        # a candidate whose expected_ctr should be the calibrated 0.017.
        _q("p8 a", clicks=20, impressions=1000, ctr=0.02, position=8.0),
        _q("p8 b", clicks=20, impressions=1000, ctr=0.02, position=8.1),
        _q("p8 c", clicks=20, impressions=1000, ctr=0.02, position=7.9),
        _q("p8 d", clicks=20, impressions=1000, ctr=0.02, position=8.0),
        _q("p8 opportunity", clicks=5, impressions=1000, ctr=0.005, position=8.0),
        # Cannibalized query (2 ranking pages, see query x page payload below).
        _q("cannibal query", clicks=10, impressions=1500, ctr=0.0067, position=9.0),
        # Filtered out: below impressions_min.
        _q("low volume thing", clicks=0, impressions=50, ctr=0.0, position=6.0),
        # Filtered out: position past the band ceiling.
        _q("page 3 term", clicks=1, impressions=2000, ctr=0.0005, position=25.0),
    ]
    return {"rows": rows}


def _prior_rows():
    # "seo audit" jumped 1000 -> 5000 (momentum 0.8 -> emerging). Others flat.
    return {
        "rows": [
            _q("seo audit", clicks=10, impressions=1000, ctr=0.01, position=6.0),
            _q("mcp server", clicks=300, impressions=6000, ctr=0.05, position=5.0),
            _q("cannibal query", clicks=10, impressions=1500, ctr=0.0067, position=9.0),
        ]
    }


def _query_page_rows():
    # "cannibal query" served by two pages -> consolidate. Others single page.
    return {
        "rows": [
            _qp("cannibal query", "https://example.com/a", 900),
            _qp("cannibal query", "https://example.com/b", 600),
            _qp("seo audit", "https://example.com/audit", 5000),
        ]
    }


def _clients(make_gsc_client):
    client = make_gsc_client({"search": [_current_rows(), _prior_rows(), _query_page_rows()]})
    return {"gsc": client}


def _run(make_gsc_client, make_config, **args):
    cfg = make_config(SEO_MCP_GSC_DEFAULT_SITE="sc-domain:example.com")
    return content_tools.content_opportunities(args, cfg, _clients(make_gsc_client))


def test_happy_path_ranks_and_shapes(make_gsc_client, make_config):
    res = _run(make_gsc_client, make_config, days=28, count=10, impressions_min=100)
    assert res["ok"] is True
    d = res["data"]
    assert d["site_url"] == "sc-domain:example.com"
    qs = [c["target_query"] for c in d["candidates"]]
    # The pos-5, high-impression, low-CTR query is the strongest opportunity.
    assert qs[0] == "seo audit"
    # Candidates are sorted by score descending.
    scores = [c["score"] for c in d["candidates"]]
    assert scores == sorted(scores, reverse=True)
    # Every candidate exposes its score components (transparency, SIM-4).
    for c in d["candidates"]:
        assert set(c["components"]) == {
            "ctr_gap_upside_norm", "striking_distance", "demand_norm", "momentum",
            "effort_multiplier", "value_multiplier",
        }
    assert d["weights"]["ctr_gap"] == 0.40
    assert d["notes"]


def test_filters_drop_low_volume_and_out_of_band(make_gsc_client, make_config):
    d = _run(make_gsc_client, make_config, impressions_min=100)["data"]
    qs = {c["target_query"] for c in d["candidates"]}
    assert "low volume thing" not in qs   # below impressions_min
    assert "page 3 term" not in qs        # position 25 > ceiling 20


def test_ctr_curve_self_calibrates(make_gsc_client, make_config):
    d = _run(make_gsc_client, make_config, impressions_min=100)["data"]
    # Bucket 8 had >= 5 queries: 85 clicks / 5000 impressions = 0.017.
    assert d["ctr_curve_calibrated"]["8"] == 0.017
    # A pos-8 candidate's expected CTR uses the calibrated value, not the
    # reference curve (which would be 0.029 at position 8).
    p8 = next(c for c in d["candidates"] if c["target_query"] == "p8 opportunity")
    assert p8["expected_ctr"] == 0.017
    assert p8["click_upside"] == 12.0  # 1000 * (0.017 - 0.005)


def test_cannibalization_flagged_as_consolidate(make_gsc_client, make_config):
    d = _run(make_gsc_client, make_config, impressions_min=100)["data"]
    cannibal = next(c for c in d["candidates"] if c["target_query"] == "cannibal query")
    assert cannibal["action"] == "consolidate"
    assert cannibal["ranking_pages"] == 2
    assert cannibal["components"]["effort_multiplier"] == content_tools._EFFORT_CONSOLIDATE
    # A single-page query is an optimize.
    seo = next(c for c in d["candidates"] if c["target_query"] == "seo audit")
    assert seo["action"] == "optimize"


def test_momentum_marks_emerging(make_gsc_client, make_config):
    d = _run(make_gsc_client, make_config, impressions_min=100)["data"]
    seo = next(c for c in d["candidates"] if c["target_query"] == "seo audit")
    assert seo["emerging"] is True  # 1000 -> 5000 impressions


def test_auth_missing_when_no_gsc_client(make_config):
    cfg = make_config(SEO_MCP_GSC_DEFAULT_SITE="sc-domain:example.com")
    res = content_tools.content_opportunities({}, cfg, {})  # no "gsc" client
    assert res["ok"] is False
    assert res["error"]["code"] == "AUTH_MISSING"


def test_ga4_value_weighting_reranks(make_gsc_client, make_ga4_client, ga4_response, make_config):
    # Two queries with identical SEO signals; the only differentiator is the GA4
    # conversions of their top ranking page, so value weighting must break the tie.
    cur = {"rows": [
        _q("alpha", clicks=50, impressions=5000, ctr=0.01, position=5.0),
        _q("beta", clicks=50, impressions=5000, ctr=0.01, position=5.0),
    ]}
    prior = {"rows": [
        _q("alpha", clicks=50, impressions=5000, ctr=0.01, position=5.0),
        _q("beta", clicks=50, impressions=5000, ctr=0.01, position=5.0),
    ]}
    qp = {"rows": [
        _qp("alpha", "https://example.com/alpha", 5000),
        _qp("beta", "https://example.com/beta", 5000),
    ]}
    gsc = make_gsc_client({"search": [cur, prior, qp]})
    ga4 = make_ga4_client(ga4_response(["landingPage"], ["conversions"], [(["/alpha"], ["100"]), (["/beta"], ["0"])]))
    cfg = make_config(SEO_MCP_GSC_DEFAULT_SITE="sc-domain:example.com", SEO_MCP_GA4_PROPERTY_ID="properties/1")
    d = content_tools.content_opportunities({"impressions_min": 100}, cfg, {"gsc": gsc, "ga4": ga4})["data"]
    assert d["filters_applied"]["ga4_value_weighted"] is True
    by_q = {c["target_query"]: c for c in d["candidates"]}
    assert by_q["alpha"]["components"]["value_multiplier"] == 1.5  # 1.0 + 0.5*(100/100)
    assert by_q["beta"]["components"]["value_multiplier"] == 1.0   # 0 conversions
    assert d["candidates"][0]["target_query"] == "alpha"          # weighting broke the tie


def test_no_ga4_property_means_no_value_weighting(make_gsc_client, make_config):
    # GSC-only (no GA4 property) -> value weighting skipped, all multipliers 1.0.
    d = _run(make_gsc_client, make_config, impressions_min=100)["data"]
    assert d["filters_applied"]["ga4_value_weighted"] is False
    assert all(c["components"]["value_multiplier"] == 1.0 for c in d["candidates"])


def test_missing_site_returns_invalid_input(make_gsc_client, make_config):
    cfg = make_config()  # no SEO_MCP_GSC_DEFAULT_SITE
    res = content_tools.content_opportunities({}, cfg, _clients(make_gsc_client))
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_INPUT"
