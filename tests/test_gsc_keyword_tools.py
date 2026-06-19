"""Offline tests for gsc_keyword_expand (roadmap Track B, Wave 1)."""

from __future__ import annotations

from seo_mcp.errors import ErrorCode
from seo_mcp.tools import gsc_keyword_tools

_OWNED = {
    "search": {
        "rows": [
            {"keys": ["seo tools"], "clicks": 120, "impressions": 3400, "ctr": 0.035, "position": 4.2},
            {"keys": ["mcp server"], "clicks": 45, "impressions": 900, "ctr": 0.05, "position": 7.1},
        ]
    }
}


def test_footprint_and_net_new(make_config, make_gsc_client):
    gsc = make_gsc_client(_OWNED)
    res = gsc_keyword_tools.gsc_keyword_expand(
        {
            "candidates": ["seo tools", "seo software", "quantum gardening xyz"],
            "site_url": "sc-domain:example.com",
        },
        make_config(),
        {"gsc": gsc},
    )
    d = res["data"]
    by = {c["term"]: c for c in d["candidates"]}
    # exact owned -> covered
    assert by["seo tools"]["footprint"] == "covered"
    assert by["seo tools"]["footprint_impressions"] == 3400
    # shares the "seo" token with an owned winner -> net-new but high confidence
    assert by["seo software"]["footprint"] == "none"
    assert by["seo software"]["confidence"]["sibling_impressions"] == 3400
    assert by["seo software"]["confidence"]["band"] == "high"
    # no token overlap -> net-new, low confidence
    assert by["quantum gardening xyz"]["footprint"] == "none"
    assert by["quantum gardening xyz"]["confidence"]["sibling_impressions"] == 0
    assert by["quantum gardening xyz"]["confidence"]["band"] == "low"
    # net_new excludes the covered term and is sorted by sibling strength
    net_terms = [c["term"] for c in d["net_new"]]
    assert "seo tools" not in net_terms
    assert net_terms[0] == "seo software"


def test_thin_footprint_below_threshold(make_config, make_gsc_client):
    gsc = make_gsc_client(_OWNED)
    res = gsc_keyword_tools.gsc_keyword_expand(
        {"candidates": ["mcp server"], "site_url": "sc-domain:example.com", "impressions_min": 5000},
        make_config(),
        {"gsc": gsc},
    )
    by = {c["term"]: c for c in res["data"]["candidates"]}
    # exact match but impressions 900 < 5000 -> thin, not covered
    assert by["mcp server"]["footprint"] == "thin"


def test_anonymization_caveat_present(make_config, make_gsc_client):
    res = gsc_keyword_tools.gsc_keyword_expand(
        {"candidates": ["x"], "site_url": "sc-domain:example.com"}, make_config(), {"gsc": make_gsc_client(_OWNED)}
    )
    assert any("VISIBLE footprint" in c for c in res["data"]["caveats"])


def test_empty_candidates_invalid(make_config, make_gsc_client):
    res = gsc_keyword_tools.gsc_keyword_expand(
        {"candidates": [], "site_url": "sc-domain:example.com"}, make_config(), {"gsc": make_gsc_client(_OWNED)}
    )
    assert res["ok"] is False
    assert res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_missing_site_when_no_default(make_config, make_gsc_client):
    res = gsc_keyword_tools.gsc_keyword_expand(
        {"candidates": ["x"]}, make_config(), {"gsc": make_gsc_client(_OWNED)}
    )
    assert res["ok"] is False
    assert res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_no_gsc_client_is_auth_missing(make_config):
    res = gsc_keyword_tools.gsc_keyword_expand(
        {"candidates": ["x"], "site_url": "sc-domain:example.com"}, make_config(), {}
    )
    assert res["ok"] is False
    assert res["error"]["code"] == ErrorCode.AUTH_MISSING
