"""Lexical matching must not present unsegmented text as complete evidence.

CJK, Thai, Lao, Khmer and Myanmar runs become single lexical tokens, so the
sibling, proximity and relevance matching in gsc_keyword_expand,
content_opportunities and internal_link_recommend cannot see words inside
them. Unsupported values are reported as unavailable, supported values from a
corpus that contains such text are marked as partial coverage, and inputs
without such text keep their existing output exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from seo_mcp.clients.http import HttpResponse
from seo_mcp.tools import content_tools, gsc_keyword_tools, linkrec_tools

SITE = "sc-domain:example.com"
HOME, A, B, C, TARGET = ("https://example.com/", "https://example.com/a", "https://example.com/b",
                         "https://example.com/c", "https://example.com/target")
COVERAGE_KEYS = ('"lexical_coverage"', '"coverage"', '"source_coverage"', '"unavailable_components"', '"status": "unavailable"')


def _q(query, impressions, position=5.0, clicks=1):
    return {"keys": [query], "clicks": clicks, "impressions": impressions, "ctr": clicks / impressions, "position": position}


def _no_coverage_fields(res):
    text = json.dumps(res, ensure_ascii=False)
    return not any(key in text for key in COVERAGE_KEYS)


# --- shared predicate ---------------------------------------------------------


def test_segmentation_predicate_matches_text_measurement_scripts():
    from seo_mcp.tools._html import segmentation_required

    assert segmentation_required("東京ホテル予約")
    assert segmentation_required("iPhoneケース")
    assert segmentation_required("เคสโทรศัพท์")
    assert segmentation_required("བོད་ཡིག")  # Tibetan is covered by the same check
    assert not segmentation_required("blue widgets")
    assert not segmentation_required("чехол для телефона")
    assert not segmentation_required("")


# --- gsc_keyword_expand ---------------------------------------------------------


def _expand(make_config, make_gsc_client, candidates, rows):
    return gsc_keyword_tools.gsc_keyword_expand(
        {"candidates": candidates, "site_url": SITE}, make_config(), {"gsc": make_gsc_client({"search": {"rows": rows}})}
    )


def _term(res, term):
    return next(c for c in res["data"]["candidates"] if c["term"] == term)


def test_expand_unsupported_candidate_confidence_is_unavailable(make_config, make_gsc_client):
    res = _expand(make_config, make_gsc_client, ["東京ホテル予約"],
                  [_q("東京ホテル予約方法", 900), _q("東京ホテル安い", 700), _q("hotel tokyo", 50)])
    entry = _term(res, "東京ホテル予約")
    assert entry["confidence"]["band"] is None
    assert entry["confidence"]["sibling_impressions"] is None
    assert entry["confidence"]["sibling_query_count"] is None
    assert entry["confidence"]["status"] == "unavailable"
    assert entry["confidence"]["reason"] == "segmentation_required"
    # Observed footprint data is unchanged (substring footprint works on raw text).
    assert entry["footprint"] == "thin"
    assert res["data"]["lexical_coverage"]["candidates_unavailable"] == {"count": 1, "items": ["東京ホテル予約"]}


def test_expand_partial_owned_corpus_marks_supported_candidates(make_config, make_gsc_client):
    rows = [_q("iphone charger", 100), _q("iphoneケース", 5000), _q("iphoneケース 人気", 3000), _q("чехол купить", 200), _q("best phone", 50)]
    res = _expand(make_config, make_gsc_client, ["iphone case", "чехол для телефона"], rows)
    latin = _term(res, "iphone case")["confidence"]
    cyrillic = _term(res, "чехол для телефона")["confidence"]
    assert latin["sibling_impressions"] == 100 and latin["coverage"] == "partial" and latin["band"] is not None
    assert cyrillic["sibling_impressions"] == 200 and cyrillic["coverage"] == "partial"
    cov = res["data"]["lexical_coverage"]
    assert cov["status"] == "partial" and cov["reason"] == "segmentation_required"
    assert cov["owned_queries_unsupported"] == {"count": 2, "items": ["iphoneケース", "iphoneケース 人気"], "impressions": 8000}
    assert cov["candidates_unavailable"] == {"count": 0, "items": []}
    assert res["data"]["visible_impressions"] == 8350
    assert res["data"]["owned_query_count"] == 5


def test_expand_mixed_candidates_keep_observed_values_and_sort_safely(make_config, make_gsc_client):
    cands = ["blue widgets", "東京ホテル予約", "iphone ケース", "กระเป๋าหนัง", "widget guide"]
    rows = [_q("blue widgets", 500), _q("blue widget price", 120), _q("widget guide", 80), _q("東京ホテル安い", 700)]
    res = _expand(make_config, make_gsc_client, cands, rows)
    data = res["data"]
    assert data["candidate_count"] == 5
    for term in ("東京ホテル予約", "iphone ケース", "กระเป๋าหนัง"):
        assert _term(res, term)["confidence"]["band"] is None
    assert _term(res, "blue widgets")["footprint"] == "covered"
    # Siblings exclude the candidate's own exact owned query (released behaviour).
    assert _term(res, "blue widgets")["confidence"]["sibling_impressions"] == 120
    assert _term(res, "widget guide")["confidence"]["sibling_impressions"] == 120
    # Unavailable net-new entries follow the available ones, in input order.
    order = [r["term"] for r in data["net_new"]]
    unavailable = [t for t in order if _term(res, t)["confidence"]["band"] is None]
    assert order[-len(unavailable):] == unavailable
    assert unavailable == [t for t in cands if t in unavailable]
    assert data["lexical_coverage"]["candidates_unavailable"]["count"] == 3


def test_expand_all_unsupported_and_empty_rows(make_config, make_gsc_client):
    res = _expand(make_config, make_gsc_client, ["東京ホテル予約", "กระเป๋าหนัง"], [_q("東京ホテル安い", 700)])
    assert res["ok"] is True
    assert res["data"]["lexical_coverage"]["candidates_unavailable"]["count"] == 2
    assert all(c["confidence"]["band"] is None for c in res["data"]["candidates"])
    empty = _expand(make_config, make_gsc_client, ["blue widgets", "東京ホテル予約"], [])
    assert empty["ok"] is True
    assert empty["data"]["visible_impressions"] == 0
    assert empty["data"]["lexical_coverage"]["owned_queries_unsupported"] == {"count": 0, "items": [], "impressions": 0}


def test_expand_bounded_items_keep_exact_totals(make_config, make_gsc_client):
    rows = [_q(f"東京ホテル{i}号", 10 + i) for i in range(25)] + [_q("blue widget price", 120)]
    res = _expand(make_config, make_gsc_client, ["blue widgets"], rows)
    group = res["data"]["lexical_coverage"]["owned_queries_unsupported"]
    assert group["count"] == 25
    assert len(group["items"]) == 20
    assert group["items"] == [f"東京ホテル{i}号" for i in range(20)]
    assert group["impressions"] == sum(10 + i for i in range(25))


def test_expand_supported_only_output_has_no_coverage_fields(make_config, make_gsc_client):
    english = _expand(make_config, make_gsc_client, ["blue widgets", "red widget guide"], [_q("blue widgets", 500), _q("widget guide", 80)])
    cyrillic = _expand(make_config, make_gsc_client, ["купить чехол"], [_q("чехол купить", 300), _q("телефон чехол", 150)])
    assert _no_coverage_fields(english) and _no_coverage_fields(cyrillic)


# --- content_opportunities ------------------------------------------------------


def _opportunities(make_config, make_gsc_client, rows):
    prior = {"rows": [dict(r, impressions=r["impressions"] // 2) for r in rows]}
    qp = {"rows": [{"keys": [r["keys"][0], "https://example.com/p"], "clicks": 0, "impressions": r["impressions"], "ctr": 0.0, "position": 1.0}
                   for r in rows]}
    gsc = make_gsc_client({"search": [{"rows": rows}, prior, qp]})
    cfg = make_config(SEO_MCP_GSC_DEFAULT_SITE=SITE)
    return content_tools.content_opportunities({"days": 28, "count": 20, "impressions_min": 100}, cfg, {"gsc": gsc})


def _topic(res, query):
    return next(c for c in res["data"]["candidates"] if c["target_query"] == query)


def test_opportunities_unsupported_query_omits_topical_proximity(make_config, make_gsc_client):
    rows = [_q("東京ホテル予約", 1000, 8.0, 5), _q("東京ホテル安い", 2000, 6.0, 30), _q("東京ホテル 比較", 800, 12.0, 10)]
    res = _opportunities(make_config, make_gsc_client, rows)
    win = _topic(res, "東京ホテル予約")["winnability"]
    assert "topical_proximity" not in win["components"]
    assert win["unavailable_components"] == ["topical_proximity"]
    assert win["applied_weight"] == 0.5
    assert _topic(res, "東京ホテル予約")["score"] > 0
    cov = res["data"]["lexical_coverage"]
    assert cov["queries_unavailable"]["count"] == 3
    assert cov["proximity_queries_unsupported"]["impressions"] == 3800


def test_opportunities_partial_proximity_corpus_marks_supported_queries(make_config, make_gsc_client):
    rows = [_q("iphone case", 1000, 8.0, 5), _q("iphone charger", 900, 9.0, 20), _q("iphoneケース", 5000, 5.0, 100),
            _q("iphoneケース 人気", 3000, 7.0, 50)]
    res = _opportunities(make_config, make_gsc_client, rows)
    win = _topic(res, "iphone case")["winnability"]
    assert win["components"]["topical_proximity"] == 0.3333
    assert win["coverage"] == "partial"
    cov = res["data"]["lexical_coverage"]
    assert cov["proximity_queries_unsupported"] == {"count": 2, "items": ["iphoneケース", "iphoneケース 人気"], "impressions": 8000}
    assert cov["queries_unavailable"] == {"count": 2, "items": ["iphoneケース", "iphoneケース 人気"]}
    assert res["data"]["total_in_band"] == 4


def test_opportunities_supported_only_output_has_no_coverage_fields(make_config, make_gsc_client):
    rows = [_q("blue widgets", 1000, 8.0, 5), _q("blue widget price", 900, 9.0, 20), _q("widget guide", 600, 7.0, 10)]
    assert _no_coverage_fields(_opportunities(make_config, make_gsc_client, rows))


# --- internal_link_recommend ----------------------------------------------------


@dataclass
class FakeHttp:
    pages: dict[str, HttpResponse]
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str, **_: Any) -> HttpResponse:
        self.calls.append(url)
        return self.pages[url]


def _page(url, body, links=()):
    anchors = "".join(f'<a href="{h}">{t}</a>' for h, t in links)
    return HttpResponse(200, {"content-type": "text/html"}, f"<html><body><p>{body}</p>{anchors}</body></html>".encode(), url)


def _recommend(make_config, make_gsc_client, query, pages, extra_rows=()):
    rows = [{"keys": [TARGET, query], "clicks": 1, "impressions": 200, "ctr": 0.005, "position": 12.0}, *extra_rows]
    gsc = make_gsc_client({"search": {"rows": rows}})
    return linkrec_tools.internal_link_recommend({"start_url": HOME, "site_url": SITE}, make_config(), {"http": FakeHttp(pages), "gsc": gsc})


def test_recommend_unsupported_target_is_listed_not_silently_dropped(make_config, make_gsc_client):
    pages = {HOME: _page(HOME, "ようこそ", [(A, "a"), (B, "b"), (TARGET, "t")]), A: _page(A, "東京ホテル予約の方法と料金比較のガイド"),
             B: _page(B, "東京の観光情報"), TARGET: _page(TARGET, "東京ホテル予約")}
    res = _recommend(make_config, make_gsc_client, "東京ホテル予約", pages)
    data = res["data"]
    assert res["ok"] is True and data["targets_found"] == 1 and data["recommendation_count"] == 0
    group = data["lexical_coverage"]["targets_unavailable"]
    assert group["count"] == 1
    assert group["items"] == [{"target_url": TARGET, "target_query": "東京ホテル予約", "target_position": 12.0, "target_impressions": 200}]


def test_recommend_partial_sources_are_not_zero_evidence(make_config, make_gsc_client):
    pages = {HOME: _page(HOME, "welcome to the store", [(A, "a"), (B, "b"), (C, "c"), (TARGET, "t")]),
             A: _page(A, "iPhoneケースの選び方と人気ランキング"), B: _page(B, "เคสiPhoneราคาถูกคุณภาพดี"),
             C: _page(C, "best iphone case reviews and deals"), TARGET: _page(TARGET, "iphone case product")}
    res = _recommend(make_config, make_gsc_client, "iphone case", pages)
    data = res["data"]
    assert [r["source_url"] for r in data["recommendations"]] == [C]
    assert "source_coverage" not in data["recommendations"][0]
    cov = data["lexical_coverage"]
    assert cov["sources_partial"] == {"count": 2, "items": [A, B]}
    assert cov["unscored_pairs"] == 2
    assert cov["targets_unavailable"] == {"count": 0, "items": []}


def test_recommend_partial_source_list_is_independent_of_crawl_order(make_config, make_gsc_client, monkeypatch):
    pages = {HOME: _page(HOME, "welcome to the store", [(A, "a"), (B, "b"), (C, "c"), (TARGET, "t")]),
             A: _page(A, "iPhoneケースの選び方と人気ランキング"), B: _page(B, "เคสiPhoneราคาถูกคุณภาพดี"),
             C: _page(C, "best iphone case reviews and deals"), TARGET: _page(TARGET, "iphone case product")}
    forward = _recommend(make_config, make_gsc_client, "iphone case", pages)
    real_crawl = linkrec_tools._crawl
    monkeypatch.setattr(linkrec_tools, "_crawl", lambda *args: dict(reversed(list(real_crawl(*args).items()))))
    reversed_order = _recommend(make_config, make_gsc_client, "iphone case", pages)
    assert forward["data"]["lexical_coverage"] == reversed_order["data"]["lexical_coverage"]
    assert reversed_order["data"]["lexical_coverage"]["sources_partial"]["items"] == [A, B]


def test_recommend_valid_match_on_partial_source_is_kept_and_marked(make_config, make_gsc_client):
    pages = {HOME: _page(HOME, "welcome", [(A, "a"), (TARGET, "t")]), A: _page(A, "iphone case の選び方と人気ランキング"),
             TARGET: _page(TARGET, "iphone case product")}
    res = _recommend(make_config, make_gsc_client, "iphone case", pages)
    recs = res["data"]["recommendations"]
    assert [r["source_url"] for r in recs] == [A]
    assert recs[0]["source_coverage"] == "partial" and recs[0]["relevance"] == 1.0


def test_recommend_all_targets_unavailable_returns_coverage_not_empty_reason(make_config, make_gsc_client):
    pages = {HOME: _page(HOME, "welcome", [(TARGET, "t")]), TARGET: _page(TARGET, "product")}
    res = _recommend(make_config, make_gsc_client, "東京ホテル予約", pages,
                     extra_rows=[{"keys": [TARGET, "กระเป๋าหนัง"], "clicks": 1, "impressions": 150, "ctr": 0.01, "position": 10.0}])
    data = res["data"]
    assert res["ok"] is True and data["recommendations"] == [] and data["targets_found"] == 2
    assert data["lexical_coverage"]["targets_unavailable"]["count"] == 2
    assert "note" not in data


def test_recommend_bounded_target_items_keep_exact_count(make_config, make_gsc_client):
    pages = {HOME: _page(HOME, "welcome", [(TARGET, "t")]), TARGET: _page(TARGET, "product")}
    extra = [{"keys": [TARGET, f"東京ホテル{i}号"], "clicks": 1, "impressions": 100 + i, "ctr": 0.01, "position": 10.0} for i in range(24)]
    res = _recommend(make_config, make_gsc_client, "東京ホテル予約", pages, extra_rows=extra)
    group = res["data"]["lexical_coverage"]["targets_unavailable"]
    assert group["count"] == 25 and len(group["items"]) == 20


def test_recommend_supported_only_output_has_no_coverage_fields(make_config, make_gsc_client):
    pages = {HOME: _page(HOME, "blue widgets welcome", [(A, "go a"), (B, "go b"), (TARGET, "go target")]),
             A: _page(A, "blue widgets guide for everyone"), B: _page(B, "red apples and oranges"), TARGET: _page(TARGET, "blue widgets product")}
    assert _no_coverage_fields(_recommend(make_config, make_gsc_client, "blue widgets", pages))
    cyr = {HOME: _page(HOME, "синие виджеты добро пожаловать", [(A, "а"), (TARGET, "цель")]),
           A: _page(A, "синие виджеты руководство для всех"), TARGET: _page(TARGET, "синие виджеты продукт")}
    assert _no_coverage_fields(_recommend(make_config, make_gsc_client, "синие виджеты", cyr))
