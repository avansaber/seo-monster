"""Offline tests for rank_change_attribution (roadmap Track D, Wave 2)."""

from __future__ import annotations

from seo_mcp.errors import ErrorCode
from seo_mcp.tools import rank_attribution_tools as rat

T = "https://example.com/t"
CLEAN_DATE = "2026-07-01"  # windows clear of the 2025-26 GSC data-regime breaks


def _resp(rows):
    return {"rows": [{"keys": [u], "clicks": c, "impressions": i, "position": p} for (u, c, i, p) in rows]}


def _run(make_gsc_client, make_config, search, **args):
    gsc = make_gsc_client({"search": search})
    a = {"url": T, "change_date": CLEAN_DATE, "site_url": "sc-domain:example.com", "control_scope": "site"}
    a.update(args)
    return rat.rank_change_attribution(a, make_config(), {"gsc": gsc})


def test_likely_positive(make_gsc_client, make_config):
    # Treated doubled (100->200); 4 controls flat -> peer trend 1.0 -> lift ~100.
    controls = [(f"https://example.com/c{i}", 50, 500, 10.0) for i in range(4)]
    search = [
        _resp([(T, 100, 1000, 12.0)] + controls),
        _resp([(T, 200, 1000, 8.0)] + controls),
    ]
    d = _run(make_gsc_client, make_config, search)["data"]
    assert d["verdict"] == "likely_positive"
    assert d["estimate"]["estimated_lift"] == 100.0
    assert d["confounders"]["data_regime_breaks"] == []
    assert d["confounders"]["position_reliable"] is True


def test_inconclusive_when_controls_move_together(make_gsc_client, make_config):
    # Treated doubled, but controls also doubled -> no excess lift.
    pre_controls = [(f"https://example.com/c{i}", 50, 500, 10.0) for i in range(4)]
    post_controls = [(f"https://example.com/c{i}", 100, 500, 10.0) for i in range(4)]
    search = [
        _resp([(T, 100, 1000, 12.0)] + pre_controls),
        _resp([(T, 200, 1000, 8.0)] + post_controls),
    ]
    d = _run(make_gsc_client, make_config, search)["data"]
    assert d["verdict"] == "inconclusive"


def test_data_regime_break_flagged(make_gsc_client, make_config):
    controls = [(f"https://example.com/c{i}", 50, 500, 10.0) for i in range(4)]
    search = [
        _resp([(T, 100, 1000, 12.0)] + controls),
        _resp([(T, 200, 1000, 8.0)] + controls),
    ]
    # change_date in May 2026: the 56-day pre window reaches into the impression-bug span.
    d = _run(make_gsc_client, make_config, search, change_date="2026-05-20")["data"]
    assert d["confounders"]["data_regime_breaks"]
    assert d["confounders"]["position_reliable"] is False


def test_insufficient_control(make_gsc_client, make_config):
    controls = [(f"https://example.com/c{i}", 50, 500, 10.0) for i in range(2)]  # < 3
    search = [
        _resp([(T, 100, 1000, 12.0)] + controls),
        _resp([(T, 200, 1000, 8.0)] + controls),
    ]
    d = _run(make_gsc_client, make_config, search)["data"]
    assert d["verdict"] == "insufficient_control"


def test_insufficient_treated_data(make_gsc_client, make_config):
    controls = [(f"https://example.com/c{i}", 50, 500, 10.0) for i in range(4)]
    search = [
        _resp([(T, 2, 50, 12.0)] + controls),   # treated pre clicks 2 < floor 5
        _resp([(T, 4, 50, 8.0)] + controls),
    ]
    d = _run(make_gsc_client, make_config, search)["data"]
    assert d["verdict"] == "insufficient_data"


def test_requires_change_date(make_gsc_client, make_config):
    gsc = make_gsc_client({"search": [_resp([]), _resp([])]})
    res = rat.rank_change_attribution({"url": T, "site_url": "sc-domain:example.com"}, make_config(), {"gsc": gsc})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_requires_url(make_gsc_client, make_config):
    gsc = make_gsc_client({"search": [_resp([]), _resp([])]})
    res = rat.rank_change_attribution({"change_date": CLEAN_DATE, "site_url": "sc-domain:example.com"}, make_config(), {"gsc": gsc})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_no_gsc_client_auth_missing(make_config):
    res = rat.rank_change_attribution({"url": T, "change_date": CLEAN_DATE}, make_config(), {})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.AUTH_MISSING
