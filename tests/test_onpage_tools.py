"""Offline tests for inspect_meta, check_canonical, mixed_content_check.

The HTTP seam is the shared HttpClient. We build a FakeHttpClient whose
``fetch`` returns canned HttpResponse objects without touching the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


from seo_mcp.clients.errors import ApiError
from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import onpage_tools


@dataclass
class FakeHttpClient:
    """Maps URL -> HttpResponse or Exception. Records fetch calls.

    A URL not in ``responses`` raises AssertionError so missing fixtures are
    surfaced as test failures rather than silent network attempts."""

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


def _html_response(body: str, *, status: int = 200, final_url: str | None = None, url: str = "https://example.com/") -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"content-type": "text/html; charset=utf-8"},
        body_bytes=body.encode("utf-8"),
        final_url=final_url or url,
    )


def _clients_with(http: FakeHttpClient) -> dict[str, Any]:
    return {"http": http}


# --- inspect_meta ---------------------------------------------------------


def test_inspect_meta_extracts_full_head_surface(make_config):
    body = """
    <html><head>
      <title>Example Page</title>
      <meta name="description" content="A short description.">
      <meta name="robots" content="index,follow">
      <link rel="canonical" href="https://example.com/canonical">
      <link rel="alternate" hreflang="en" href="https://example.com/en">
      <link rel="alternate" hreflang="fr" href="https://example.com/fr">
      <meta property="og:title" content="OG Title">
      <meta property="og:image" content="https://example.com/og.png">
      <meta name="twitter:card" content="summary">
    </head><body>
      <h1>Heading One</h1>
      <h1>Second H1</h1>
    </body></html>
    """
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = onpage_tools.inspect_meta({"url": "https://example.com/"}, make_config(), _clients_with(http))
    assert result["ok"] is True
    d = result["data"]
    assert d["title"] == "Example Page"
    assert d["title_length"] == len("Example Page")
    assert d["meta_description"] == "A short description."
    assert d["meta_robots"] == "index,follow"
    assert d["canonical"] == "https://example.com/canonical"
    assert d["h1_count"] == 2
    assert d["open_graph"] == {"og:title": "OG Title", "og:image": "https://example.com/og.png"}
    assert d["twitter"] == {"twitter:card": "summary"}
    assert {h["hreflang"] for h in d["hreflang"]} == {"en", "fr"}


def test_inspect_meta_missing_url_returns_invalid_input(make_config):
    http = FakeHttpClient({})
    result = onpage_tools.inspect_meta({}, make_config(), _clients_with(http))
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.INVALID_INPUT


def test_inspect_meta_non_2xx_returns_upstream_error(make_config):
    http = FakeHttpClient({"https://example.com/": _html_response("404 not found", status=404)})
    result = onpage_tools.inspect_meta({"url": "https://example.com/"}, make_config(), _clients_with(http))
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.UPSTREAM_ERROR


def test_inspect_meta_handles_no_head_gracefully(make_config):
    http = FakeHttpClient({"https://example.com/": _html_response("<html><body>plain</body></html>")})
    result = onpage_tools.inspect_meta({"url": "https://example.com/"}, make_config(), _clients_with(http))
    assert result["ok"] is True
    d = result["data"]
    assert d["title"] is None
    assert d["meta_description"] is None
    assert d["canonical"] is None


# --- check_canonical ------------------------------------------------------


def test_check_canonical_self_referential(make_config):
    body = '<html><head><link rel="canonical" href="https://example.com/page"></head></html>'
    http = FakeHttpClient({
        "https://example.com/page": _html_response(body, final_url="https://example.com/page"),
    })
    result = onpage_tools.check_canonical({"url": "https://example.com/page"}, make_config(), _clients_with(http))
    assert result["ok"] is True
    d = result["data"]
    assert d["is_self_referential"] is True
    assert d["findings"] == []
    assert d["canonical_status"] is None or d["canonical_status"] == 200  # depending on whether seen twice


def test_check_canonical_flags_cross_host(make_config):
    body = '<html><head><link rel="canonical" href="https://other.com/page"></head></html>'
    http = FakeHttpClient({
        "https://example.com/page": _html_response(body, final_url="https://example.com/page"),
        "https://other.com/page": _html_response("<html></html>", final_url="https://other.com/page"),
    })
    result = onpage_tools.check_canonical({"url": "https://example.com/page"}, make_config(), _clients_with(http))
    d = result["data"]
    assert d["is_self_referential"] is False
    assert "cross_url" in d["findings"]
    assert "cross_host" in d["findings"]


def test_check_canonical_flags_protocol_mismatch(make_config):
    body = '<html><head><link rel="canonical" href="http://example.com/page"></head></html>'
    http = FakeHttpClient({
        "https://example.com/page": _html_response(body, final_url="https://example.com/page"),
        "http://example.com/page": _html_response("<html></html>", final_url="http://example.com/page"),
    })
    result = onpage_tools.check_canonical({"url": "https://example.com/page"}, make_config(), _clients_with(http))
    assert "protocol_mismatch" in result["data"]["findings"]


def test_check_canonical_missing_link(make_config):
    http = FakeHttpClient({"https://example.com/page": _html_response("<html><head></head></html>")})
    result = onpage_tools.check_canonical({"url": "https://example.com/page"}, make_config(), _clients_with(http))
    d = result["data"]
    assert d["canonical_declared"] is None
    assert "no_canonical" in d["findings"]


def test_check_canonical_target_non_2xx(make_config):
    body = '<html><head><link rel="canonical" href="https://example.com/dead"></head></html>'
    http = FakeHttpClient({
        "https://example.com/page": _html_response(body, final_url="https://example.com/page"),
        "https://example.com/dead": _html_response("gone", status=404, final_url="https://example.com/dead"),
    })
    result = onpage_tools.check_canonical({"url": "https://example.com/page"}, make_config(), _clients_with(http))
    d = result["data"]
    assert d["canonical_status"] == 404
    assert "canonical_target_non_2xx" in d["findings"]


def test_check_canonical_target_unreachable(make_config):
    body = '<html><head><link rel="canonical" href="https://example.com/dead"></head></html>'
    http = FakeHttpClient({
        "https://example.com/page": _html_response(body, final_url="https://example.com/page"),
        "https://example.com/dead": ApiError(ErrorCode.UPSTREAM_ERROR, "network down"),
    })
    result = onpage_tools.check_canonical({"url": "https://example.com/page"}, make_config(), _clients_with(http))
    assert "canonical_target_unreachable" in result["data"]["findings"]


# --- mixed_content_check --------------------------------------------------


def test_mixed_content_clean(make_config):
    body = """
    <html><body>
      <img src="https://cdn.example.com/a.png">
      <script src="https://cdn.example.com/a.js"></script>
    </body></html>
    """
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = onpage_tools.mixed_content_check({"url": "https://example.com/"}, make_config(), _clients_with(http))
    d = result["data"]
    assert d["verdict"] == "clean"
    assert d["total_violations"] == 0


def test_mixed_content_finds_violations(make_config):
    body = """
    <html><body>
      <img src="http://cdn.example.com/a.png" srcset="http://cdn.example.com/2x.png 2x, https://cdn.example.com/3x.png 3x">
      <script src="http://cdn.example.com/a.js"></script>
      <iframe src="http://embed.example.com/"></iframe>
      <form action="http://example.com/submit" method="post"></form>
    </body></html>
    """
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = onpage_tools.mixed_content_check({"url": "https://example.com/"}, make_config(), _clients_with(http))
    d = result["data"]
    assert d["verdict"] == "mixed_content_found"
    # img (src + 1 of 2 srcset entries) + script + iframe + form_action = 5
    assert d["total_violations"] == 5
    assert any("a.js" in v for v in d["violations"]["script"])
    assert any("embed" in v for v in d["violations"]["iframe"])


def test_mixed_content_skipped_for_http_pages(make_config):
    http = FakeHttpClient({})  # no fetch should happen
    result = onpage_tools.mixed_content_check({"url": "http://example.com/"}, make_config(), _clients_with(http))
    assert result["ok"] is True
    assert result["data"]["verdict"] == "not_https"
    assert http.calls == []
