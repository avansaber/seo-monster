"""Offline tests for crux_history. The single seam is
``CruxHistoryClient._http_post``."""

from __future__ import annotations

from typing import Any

import pytest

from seo_mcp.clients.crux import CruxHistoryClient, build_crux_client
from seo_mcp.clients.errors import ApiError
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import crux_tools


def _client_with(canned: Any) -> CruxHistoryClient:
    client = CruxHistoryClient(api_key="testkey")

    def fake(body):
        if isinstance(canned, Exception):
            raise canned
        client._last_body = body
        return canned

    client._http_post = fake
    return client


# --- client ---------------------------------------------------------------


def test_build_crux_reuses_psi_key(make_config):
    cfg = make_config(PSI_API_KEY="abc123")
    client = build_crux_client(cfg)
    assert client._key == "abc123"


def test_build_crux_without_key_is_still_ok(make_config):
    cfg = make_config()
    client = build_crux_client(cfg)
    assert isinstance(client, CruxHistoryClient)
    assert client._key is None


def test_query_requires_url_or_origin():
    client = _client_with({"record": None})
    with pytest.raises(ApiError) as ei:
        client.query()
    assert ei.value.code == ErrorCode.INVALID_INPUT


def test_query_rejects_both_url_and_origin():
    client = _client_with({"record": None})
    with pytest.raises(ApiError) as ei:
        client.query(url="https://example.com/p", origin="https://example.com")
    assert ei.value.code == ErrorCode.INVALID_INPUT


# --- tool: success path ---------------------------------------------------


_RECORD = {
    "record": {
        "collectionPeriods": [
            {"firstDate": {"year": 2026, "month": 5, "day": 1}, "lastDate": {"year": 2026, "month": 5, "day": 28}},
            {"firstDate": {"year": 2026, "month": 4, "day": 1}, "lastDate": {"year": 2026, "month": 4, "day": 28}},
        ],
        "metrics": {
            "largest_contentful_paint": {"percentilesTimeseries": {"p75s": ["2300", "2500"]}},
            "interaction_to_next_paint": {"percentilesTimeseries": {"p75s": ["180", None]}},
        },
    }
}


def test_crux_history_url_returns_trend(make_config):
    client = _client_with(_RECORD)
    result = crux_tools.crux_history(
        {"url": "https://example.com/page", "form_factor": "PHONE"},
        make_config(),
        {"crux": client},
    )
    assert result["ok"] is True
    d = result["data"]
    assert d["key"] == "https://example.com/page"
    assert d["form_factor"] == "PHONE"
    assert len(d["periods"]) == 2
    assert d["periods"][0]["first_date"] == "2026-05-01"
    lcp = d["metrics"]["largest_contentful_paint"]["p75"]
    assert lcp == [2300.0, 2500.0]
    inp = d["metrics"]["interaction_to_next_paint"]["p75"]
    assert inp == [180.0, None]
    assert d["no_data"] is False


def test_crux_history_origin_branch(make_config):
    client = _client_with(_RECORD)
    result = crux_tools.crux_history(
        {"origin": "https://example.com"},
        make_config(),
        {"crux": client},
    )
    assert result["data"]["key"] == "https://example.com"


def test_crux_history_no_data(make_config):
    client = _client_with({"record": None, "no_data": True})
    result = crux_tools.crux_history({"url": "https://obscure.example/"}, make_config(), {"crux": client})
    d = result["data"]
    assert d["no_data"] is True
    assert d["periods"] == []
    assert d["metrics"] == {}


def test_crux_history_requires_url_or_origin(make_config):
    client = _client_with({"record": None})
    result = crux_tools.crux_history({}, make_config(), {"crux": client})
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.INVALID_INPUT


def test_crux_history_rejects_both_url_and_origin(make_config):
    client = _client_with({"record": None})
    result = crux_tools.crux_history(
        {"url": "https://example.com/p", "origin": "https://example.com"},
        make_config(),
        {"crux": client},
    )
    assert result["ok"] is False
    assert "OR" in result["error"]["message"]


def test_crux_history_surfaces_upstream_error(make_config):
    client = _client_with(ApiError(ErrorCode.UPSTREAM_ERROR, "crux 503"))
    result = crux_tools.crux_history({"url": "https://example.com/"}, make_config(), {"crux": client})
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.UPSTREAM_ERROR


def test_crux_400_insufficient_data_returns_no_data():
    """The client's _is_no_data classifier turns 400 + 'insufficient data'
    into a clean empty result rather than an error."""
    assert CruxHistoryClient._is_no_data(400, '{"error":{"message":"insufficient data"}}')
    assert CruxHistoryClient._is_no_data(404, "anything") is True
    assert CruxHistoryClient._is_no_data(500, "x") is False


# --- crux_snapshot --------------------------------------------------------


def _client_current_with(canned: Any) -> CruxHistoryClient:
    """Fake the queryRecord (current) seam, mirroring _client_with."""
    client = CruxHistoryClient(api_key="testkey")

    def fake(body):
        if isinstance(canned, Exception):
            raise canned
        client._last_body = body
        return canned

    client._http_post_current = fake
    return client


_CURRENT_RECORD = {
    "record": {
        "key": {"url": "https://example.com/page"},
        "metrics": {
            "largest_contentful_paint": {"percentiles": {"p75": "2300"}},
            "interaction_to_next_paint": {"percentiles": {"p75": "260"}},
            "cumulative_layout_shift": {"percentiles": {"p75": "0.05"}},
            "first_contentful_paint": {"percentiles": {"p75": "1700"}},
            "experimental_time_to_first_byte": {"percentiles": {"p75": "2000"}},
        },
    }
}


def test_crux_snapshot_parses_p75_and_categories(make_config):
    client = _client_current_with(_CURRENT_RECORD)
    result = crux_tools.crux_snapshot(
        {"url": "https://example.com/page", "form_factor": "PHONE"},
        make_config(),
        {"crux": client},
    )
    assert result["ok"] is True
    d = result["data"]
    assert d["key"] == "https://example.com/page"
    assert d["form_factor"] == "PHONE"
    assert d["no_data"] is False
    m = d["metrics"]
    # LCP 2300ms <= 2500 -> GOOD
    assert m["LCP"] == {"p75_ms": 2300.0, "category": "GOOD"}
    # INP 260ms between 200 and 500 -> NEEDS_IMPROVEMENT
    assert m["INP"] == {"p75_ms": 260.0, "category": "NEEDS_IMPROVEMENT"}
    # CLS 0.05 <= 0.1 -> GOOD, exposed unitless
    assert m["CLS"] == {"p75": 0.05, "category": "GOOD"}
    # TTFB 2000ms >= 1800 -> POOR
    assert m["TTFB"] == {"p75_ms": 2000.0, "category": "POOR"}
    # Overall = worst of the three core vitals (LCP/INP/CLS) -> NEEDS_IMPROVEMENT
    assert d["overall_category"] == "NEEDS_IMPROVEMENT"
    # Snapshot uses the queryRecord seam with the requested metric list.
    assert "largest_contentful_paint" in client._last_body["metrics"]


def test_crux_snapshot_origin_branch(make_config):
    client = _client_current_with(_CURRENT_RECORD)
    result = crux_tools.crux_snapshot(
        {"origin": "https://example.com"},
        make_config(),
        {"crux": client},
    )
    assert result["data"]["key"] == "https://example.com"
    assert client._last_body["origin"] == "https://example.com"


def test_crux_snapshot_no_data(make_config):
    client = _client_current_with({"record": None, "no_data": True})
    result = crux_tools.crux_snapshot({"url": "https://obscure.example/"}, make_config(), {"crux": client})
    d = result["data"]
    assert d["no_data"] is True
    assert d["metrics"] == {}
    assert d["overall_category"] is None


def test_crux_snapshot_requires_url_or_origin(make_config):
    client = _client_current_with({"record": None})
    result = crux_tools.crux_snapshot({}, make_config(), {"crux": client})
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.INVALID_INPUT


def test_crux_snapshot_rejects_both(make_config):
    client = _client_current_with({"record": None})
    result = crux_tools.crux_snapshot(
        {"url": "https://example.com/p", "origin": "https://example.com"},
        make_config(),
        {"crux": client},
    )
    assert result["ok"] is False
    assert "OR" in result["error"]["message"]


def test_crux_snapshot_surfaces_upstream_error(make_config):
    client = _client_current_with(ApiError(ErrorCode.UPSTREAM_ERROR, "crux 503"))
    result = crux_tools.crux_snapshot({"url": "https://example.com/"}, make_config(), {"crux": client})
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.UPSTREAM_ERROR


def test_query_current_builds_origin_body():
    client = _client_current_with({"record": None})
    client.query_current(origin="https://example.com", form_factor="phone")
    assert client._last_body["origin"] == "https://example.com"
    assert client._last_body["formFactor"] == "PHONE"


def test_query_current_requires_url_or_origin():
    client = _client_current_with({"record": None})
    with pytest.raises(ApiError) as ei:
        client.query_current()
    assert ei.value.code == ErrorCode.INVALID_INPUT
