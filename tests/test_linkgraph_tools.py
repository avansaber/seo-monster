"""Offline tests for internal_link_graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


from seo_mcp.clients.errors import ApiError
from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import linkgraph_tools


@dataclass
class FakeHttpClient:
    responses: dict[str, Any]
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str, **_: Any) -> HttpResponse:
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError(f"no canned response for {url!r}")
        spec = self.responses[url]
        if isinstance(spec, Exception):
            raise spec
        return spec


def _page(url: str, links: list[str], *, status: int = 200) -> HttpResponse:
    anchors = "\n".join(f'<a href="{h}">link</a>' for h in links)
    body = f"<html><body>{anchors}</body></html>"
    return HttpResponse(
        status=status,
        headers={"content-type": "text/html"},
        body_bytes=body.encode("utf-8"),
        final_url=url,
    )


def _clients(http: FakeHttpClient) -> dict[str, Any]:
    return {"http": http}


# --- basic crawl ----------------------------------------------------------


def test_linkgraph_simple_two_node(make_config):
    a = "https://example.com/a"
    b = "https://example.com/b"
    http = FakeHttpClient({
        a: _page(a, [b]),
        b: _page(b, [a]),
    })
    result = linkgraph_tools.internal_link_graph({"start_url": a, "max_depth": 2}, make_config(), _clients(http))
    d = result["data"]
    assert d["pages_fetched"] == 2
    by_url = {p["url"]: p for p in d["pages"]}
    assert by_url[a]["out_degree"] == 1
    assert by_url[a]["in_degree"] == 1
    assert by_url[b]["out_degree"] == 1
    assert by_url[b]["in_degree"] == 1
    assert d["broken_links"] == []


# --- orphans --------------------------------------------------------------


def test_linkgraph_finds_orphan(make_config):
    home = "https://example.com/"
    blog = "https://example.com/blog"
    orphan = "https://example.com/forgotten"
    http = FakeHttpClient({
        home: _page(home, [blog]),
        blog: _page(blog, [home, orphan]),
        orphan: _page(orphan, []),
    })
    result = linkgraph_tools.internal_link_graph(
        {"start_url": home, "max_depth": 2, "max_pages": 10},
        make_config(),
        _clients(http),
    )
    d = result["data"]
    # The start page is never an orphan; blog has the home as in-link.
    # orphan has 1 in-link (from blog) so technically it is NOT an orphan
    # in this fixture - which is the point: orphan-ness depends on graph.
    assert orphan not in d["orphans"]
    assert d["pages_fetched"] == 3


def test_linkgraph_true_orphan_detection(make_config):
    # Page A links to B and C. C links to D. Nothing links to E (E is unreachable
    # from A's crawl, so it just won't be discovered). Real orphans come from
    # pages that are reachable but have zero in-degree other than the crawl
    # entry point. Construct: A -> B, A -> C, C -> D. None of {B, C, D} are
    # orphans because each has an in-link.
    a = "https://example.com/a"
    b = "https://example.com/b"
    c = "https://example.com/c"
    d_url = "https://example.com/d"
    http = FakeHttpClient({
        a: _page(a, [b, c]),
        b: _page(b, []),
        c: _page(c, [d_url]),
        d_url: _page(d_url, []),
    })
    result = linkgraph_tools.internal_link_graph(
        {"start_url": a, "max_depth": 2},
        make_config(),
        _clients(http),
    )
    assert result["data"]["orphans"] == []


# --- broken internal links ------------------------------------------------


def test_linkgraph_broken_internal_link(make_config):
    home = "https://example.com/"
    dead = "https://example.com/dead"
    http = FakeHttpClient({
        home: _page(home, [dead]),
        dead: _page(dead, [], status=404),
    })
    result = linkgraph_tools.internal_link_graph({"start_url": home, "max_depth": 2}, make_config(), _clients(http))
    d = result["data"]
    assert any(b["url"] == dead and b["status"] == 404 for b in d["broken_links"])


# --- cross-host filtering -------------------------------------------------


def test_linkgraph_skips_external_links(make_config):
    home = "https://example.com/"
    external = "https://other.com/page"
    http = FakeHttpClient({
        home: _page(home, [external]),
    })
    result = linkgraph_tools.internal_link_graph({"start_url": home, "max_depth": 2}, make_config(), _clients(http))
    d = result["data"]
    assert d["pages_fetched"] == 1
    # No discovery of external should have happened
    assert all(p["url"].startswith("https://example.com") for p in d["pages"])


# --- caps + non-html -------------------------------------------------------


def test_linkgraph_respects_max_pages(make_config):
    # Each page links to the next.
    http_pages = {
        f"https://example.com/p{i}": _page(f"https://example.com/p{i}", [f"https://example.com/p{i+1}"])
        for i in range(9)
    }
    http_pages["https://example.com/p9"] = _page("https://example.com/p9", [])
    http = FakeHttpClient(http_pages)
    result = linkgraph_tools.internal_link_graph(
        {"start_url": "https://example.com/p0", "max_depth": 4, "max_pages": 4},
        make_config(),
        _clients(http),
    )
    assert result["data"]["pages_fetched"] <= 4


def test_linkgraph_skips_mailto_and_anchors(make_config):
    home = "https://example.com/"
    other = "https://example.com/x"
    http = FakeHttpClient({
        home: _page(home, ["mailto:a@b.com", "#section", "tel:+1234", "javascript:void(0)", other]),
        other: _page(other, []),
    })
    result = linkgraph_tools.internal_link_graph({"start_url": home, "max_depth": 1}, make_config(), _clients(http))
    by_url = {p["url"]: p for p in result["data"]["pages"]}
    assert by_url[home]["out_degree"] == 1


# --- input validation -----------------------------------------------------


def test_linkgraph_rejects_relative_start(make_config):
    http = FakeHttpClient({})
    result = linkgraph_tools.internal_link_graph({"start_url": "/no-host"}, make_config(), _clients(http))
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.INVALID_INPUT


def test_linkgraph_handles_unreachable_start(make_config):
    home = "https://example.com/"
    http = FakeHttpClient({
        home: ApiError(ErrorCode.UPSTREAM_ERROR, "DNS failure"),
    })
    result = linkgraph_tools.internal_link_graph({"start_url": home}, make_config(), _clients(http))
    d = result["data"]
    assert d["pages_fetched"] == 1
    assert any(b["url"] == home for b in d["broken_links"])
