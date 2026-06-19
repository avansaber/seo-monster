"""Offline tests for topic_cluster_map (roadmap Track C, Wave 2)."""

from __future__ import annotations

from seo_mcp.errors import ErrorCode
from seo_mcp.tools import topic_cluster_tools as tct

A = "https://example.com/blog/widgets/a"
B = "https://example.com/blog/widgets/b"
OUT = "https://example.com/blog/other/z"


def _qp(page, query, impressions, position, clicks=0):
    return {"keys": [page, query], "clicks": clicks, "impressions": impressions, "ctr": 0.0, "position": position}


_ROWS = {
    "search": {
        "rows": [
            _qp(A, "blue widgets", 500, 2.0),          # defend (pos<=3, high demand)
            _qp(A, "widget repair", 300, 12.0),         # optimize (3<pos<=20)
            _qp(A, "widget alternatives", 400, 35.0),   # create (pos>20, high demand)
            _qp(A, "obscure widget thing", 10, 8.0),    # monitor (low demand)
            _qp(A, "widget guide", 200, 5.0),           # cannibalized (2 pages)
            _qp(B, "widget guide", 150, 9.0),
            _qp(OUT, "unrelated topic", 999, 1.0),      # out of cluster -> ignored
        ]
    }
}


def _run(make_gsc_client, make_config, **args):
    gsc = make_gsc_client(_ROWS)
    a = {"cluster_path": "/blog/widgets/", "site_url": "sc-domain:example.com"}
    a.update(args)
    return tct.topic_cluster_map(a, make_config(), {"gsc": gsc})


def test_quadrant_classification(make_gsc_client, make_config):
    d = _run(make_gsc_client, make_config)["data"]
    by_quadrant = {q: {e["query"] for e in d["quadrants"][q]} for q in d["quadrants"]}
    assert "blue widgets" in by_quadrant["defend"]
    assert "widget repair" in by_quadrant["optimize"]
    assert "widget alternatives" in by_quadrant["create"]
    assert "obscure widget thing" in by_quadrant["monitor"]
    # out-of-cluster query is excluded entirely
    assert "unrelated topic" not in {q for s in by_quadrant.values() for q in s}


def test_missing_subtopics_are_the_create_quadrant(make_gsc_client, make_config):
    d = _run(make_gsc_client, make_config)["data"]
    missing = {e["query"] for e in d["missing_subtopics"]}
    assert "widget alternatives" in missing
    assert "blue widgets" not in missing


def test_cannibalization_flagged(make_gsc_client, make_config):
    d = _run(make_gsc_client, make_config)["data"]
    cannibal = {e["query"]: e for e in d["cannibalization"]}
    assert "widget guide" in cannibal
    assert cannibal["widget guide"]["ranking_pages"] == 2


def test_cluster_pages_scoped(make_gsc_client, make_config):
    d = _run(make_gsc_client, make_config)["data"]
    assert set(d["cluster_pages"]) == {A, B}


def test_pillar_url_derives_prefix(make_gsc_client, make_config):
    d = _run(make_gsc_client, make_config, cluster_path=None, pillar_url="https://example.com/blog/widgets/pillar")["data"]
    assert d["cluster_prefix"] == "/blog/widgets/"


def test_requires_cluster_definition(make_gsc_client, make_config):
    gsc = make_gsc_client(_ROWS)
    res = tct.topic_cluster_map({"site_url": "sc-domain:example.com"}, make_config(), {"gsc": gsc})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_no_gsc_client_auth_missing(make_config):
    res = tct.topic_cluster_map({"cluster_path": "/blog/"}, make_config(), {})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.AUTH_MISSING
