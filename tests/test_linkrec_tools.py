"""Offline tests for internal_link_recommend (roadmap Track D, Wave 1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import linkrec_tools

HOME = "https://example.com/"
A = "https://example.com/a"
B = "https://example.com/b"
TARGET = "https://example.com/target"


@dataclass
class FakeHttp:
    pages: dict[str, HttpResponse]
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str, **_: Any) -> HttpResponse:
        self.calls.append(url)
        if url not in self.pages:
            raise AssertionError(f"no canned page for {url!r}")
        return self.pages[url]


def _page(url: str, body: str, links: list[tuple[str, str]]) -> HttpResponse:
    anchors = "".join(f'<a href="{h}">{t}</a>' for h, t in links)
    html = f"<html><body><p>{body}</p>{anchors}</body></html>"
    return HttpResponse(200, {"content-type": "text/html"}, html.encode("utf-8"), url)


def _site() -> FakeHttp:
    return FakeHttp({
        HOME: _page(HOME, "blue widgets welcome", [(A, "go a"), (B, "go b"), (TARGET, "go target")]),
        A: _page(A, "blue widgets guide for everyone", []),
        B: _page(B, "red apples and oranges", []),
        TARGET: _page(TARGET, "blue widgets product", []),
    })


def _gsc(make_gsc_client, rows):
    return make_gsc_client({"search": {"rows": rows}})


def _striking_row(page=TARGET, query="blue widgets", position=12.0, impressions=200):
    return {"keys": [page, query], "clicks": 1, "impressions": impressions, "ctr": 0.005, "position": position}


def test_recommends_relevant_authority_source(make_config, make_gsc_client):
    clients = {"http": _site(), "gsc": _gsc(make_gsc_client, [_striking_row()])}
    res = linkrec_tools.internal_link_recommend(
        {"start_url": HOME, "site_url": "sc-domain:example.com"}, make_config(), clients
    )
    d = res["data"]
    assert d["recommendation_count"] >= 1
    rec = d["recommendations"][0]
    assert rec["source_url"] == A
    assert rec["target_url"] == TARGET
    assert rec["target_query"] == "blue widgets"
    assert rec["relevance"] == 1.0
    assert rec["anchor_type"] == "exact_match"


def test_skips_source_that_already_links_target(make_config, make_gsc_client):
    # HOME contains "blue widgets" too, but already links TARGET -> must be excluded.
    clients = {"http": _site(), "gsc": _gsc(make_gsc_client, [_striking_row()])}
    res = linkrec_tools.internal_link_recommend(
        {"start_url": HOME, "site_url": "sc-domain:example.com"}, make_config(), clients
    )
    assert all(r["source_url"] != HOME for r in res["data"]["recommendations"])


def test_relevance_floor_excludes_weak_matches(make_config, make_gsc_client):
    # 3-term query; /a matches 2/3 = 0.67 < floor 0.9 -> excluded.
    rows = [_striking_row(query="blue widgets premium")]
    clients = {"http": _site(), "gsc": _gsc(make_gsc_client, rows)}
    res = linkrec_tools.internal_link_recommend(
        {"start_url": HOME, "site_url": "sc-domain:example.com", "relevance_floor": 0.9},
        make_config(),
        clients,
    )
    assert res["data"]["recommendation_count"] == 0


def test_no_targets_in_band_returns_note(make_config, make_gsc_client):
    rows = [_striking_row(position=3.0)]  # too good, out of striking band
    clients = {"http": _site(), "gsc": _gsc(make_gsc_client, rows)}
    res = linkrec_tools.internal_link_recommend(
        {"start_url": HOME, "site_url": "sc-domain:example.com"}, make_config(), clients
    )
    d = res["data"]
    assert d["recommendation_count"] == 0
    assert "note" in d


def test_missing_start_url_invalid(make_config, make_gsc_client):
    clients = {"http": _site(), "gsc": _gsc(make_gsc_client, [_striking_row()])}
    res = linkrec_tools.internal_link_recommend({"site_url": "sc-domain:example.com"}, make_config(), clients)
    assert res["ok"] is False
    assert res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_no_http_client_auth_missing(make_config):
    res = linkrec_tools.internal_link_recommend({"start_url": HOME}, make_config(), {})
    assert res["ok"] is False
    assert res["error"]["code"] == ErrorCode.AUTH_MISSING


def test_no_gsc_client_auth_missing(make_config):
    res = linkrec_tools.internal_link_recommend({"start_url": HOME}, make_config(), {"http": _site()})
    assert res["ok"] is False
    assert res["error"]["code"] == ErrorCode.AUTH_MISSING
