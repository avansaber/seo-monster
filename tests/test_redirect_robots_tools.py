"""Offline tests for redirect_chain_audit and robots_txt_validate.

Both share the HttpClient seam. We reuse the FakeHttpClient pattern from
test_onpage_tools.py (declared locally here to keep tests independent)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from seo_mcp.clients.errors import ApiError
from seo_mcp.clients.http import HttpClient, HttpResponse, RedirectHop
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import redirect_tools, robots_tools


@dataclass
class FakeHttpClient:
    responses: dict[str, Any]
    calls: list[tuple[str, dict]] = field(default_factory=list)

    def fetch(self, url: str, **kwargs: Any) -> HttpResponse:
        self.calls.append((url, kwargs))
        if url not in self.responses:
            raise AssertionError(f"no canned response for {url!r}")
        spec = self.responses[url]
        if isinstance(spec, Exception):
            raise spec
        return spec


def _clients_with(http: FakeHttpClient) -> dict[str, Any]:
    return {"http": http}


# --- redirect_chain_audit --------------------------------------------------


def _resp_with_chain(final_url: str, status: int, chain: list[RedirectHop]) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"content-type": "text/html"},
        body_bytes=b"",
        final_url=final_url,
        redirect_chain=chain,
    )


def test_redirect_chain_audit_clean_no_redirects(make_config):
    http = FakeHttpClient({
        "https://example.com/": _resp_with_chain("https://example.com/", 200, []),
    })
    result = redirect_tools.redirect_chain_audit({"url": "https://example.com/"}, make_config(), _clients_with(http))
    assert result["ok"] is True
    d = result["data"]
    assert d["hop_count"] == 0
    assert d["findings"] == []


def test_redirect_chain_audit_long_chain_flagged(make_config):
    chain = [
        RedirectHop(url="https://example.com/a", status=301, location="https://example.com/b", elapsed_ms=5),
        RedirectHop(url="https://example.com/b", status=302, location="https://example.com/c", elapsed_ms=7),
    ]
    http = FakeHttpClient({
        "https://example.com/a": _resp_with_chain("https://example.com/c", 200, chain),
    })
    result = redirect_tools.redirect_chain_audit({"url": "https://example.com/a"}, make_config(), _clients_with(http))
    d = result["data"]
    assert d["hop_count"] == 2
    assert "long_chain" in d["findings"]


def test_redirect_chain_audit_flags_protocol_downgrade(make_config):
    chain = [
        RedirectHop(url="https://example.com/a", status=301, location="http://example.com/b", elapsed_ms=1),
    ]
    http = FakeHttpClient({
        "https://example.com/a": _resp_with_chain("http://example.com/b", 200, chain),
    })
    result = redirect_tools.redirect_chain_audit({"url": "https://example.com/a"}, make_config(), _clients_with(http))
    assert "protocol_downgrade" in result["data"]["findings"]


def test_redirect_chain_audit_flags_non_2xx_terminus(make_config):
    http = FakeHttpClient({
        "https://example.com/x": _resp_with_chain("https://example.com/x", 404, []),
    })
    result = redirect_tools.redirect_chain_audit({"url": "https://example.com/x"}, make_config(), _clients_with(http))
    assert "non_2xx_terminus" in result["data"]["findings"]


def test_redirect_chain_audit_surfaces_loop_error(make_config):
    http = FakeHttpClient({
        "https://example.com/loop": ApiError(ErrorCode.UPSTREAM_ERROR, "Redirect loop detected at 'https://example.com/loop'."),
    })
    result = redirect_tools.redirect_chain_audit({"url": "https://example.com/loop"}, make_config(), _clients_with(http))
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.UPSTREAM_ERROR
    assert "loop" in result["error"]["message"].lower()


# --- robots_txt_validate ---------------------------------------------------


def _robots(text: str, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"content-type": "text/plain"},
        body_bytes=text.encode("utf-8"),
        final_url="https://example.com/robots.txt",
    )


def test_robots_validate_parses_groups_and_sitemaps(make_config):
    text = """
    # comment
    User-agent: *
    Disallow: /private/
    Allow: /private/public
    Crawl-delay: 2

    User-agent: Googlebot
    Disallow: /no-google

    Sitemap: https://example.com/sitemap.xml
    Sitemap: https://example.com/sitemap-news.xml
    """
    http = FakeHttpClient({"https://example.com/robots.txt": _robots(text)})
    result = robots_tools.robots_txt_validate({"site_url": "https://example.com/page"}, make_config(), _clients_with(http))
    d = result["data"]
    assert d["status"] == 200
    assert len(d["groups"]) == 2
    star = next(g for g in d["groups"] if g["user_agents"] == ["*"])
    assert star["crawl_delay"] == 2
    assert {(r["type"], r["path"]) for r in star["rules"]} == {("disallow", "/private/"), ("allow", "/private/public")}
    assert d["sitemaps"] == ["https://example.com/sitemap.xml", "https://example.com/sitemap-news.xml"]


def test_robots_validate_handles_404(make_config):
    http = FakeHttpClient({"https://example.com/robots.txt": _robots("", status=404)})
    result = robots_tools.robots_txt_validate({"site_url": "https://example.com/"}, make_config(), _clients_with(http))
    d = result["data"]
    assert d["verdict"] == "no_robots_txt"
    assert d["groups"] == []
    assert "no_robots_txt" in d["findings"]


def test_robots_validate_probes_can_fetch(make_config):
    text = """
    User-agent: *
    Disallow: /admin/
    Allow: /admin/public
    """
    http = FakeHttpClient({"https://example.com/robots.txt": _robots(text)})
    result = robots_tools.robots_txt_validate(
        {
            "site_url": "https://example.com/",
            "probes": [
                {"user_agent": "Googlebot", "url": "https://example.com/admin/secret"},
                {"user_agent": "Googlebot", "url": "https://example.com/admin/public/page"},
                {"user_agent": "Googlebot", "url": "https://example.com/anywhere/else"},
            ],
        },
        make_config(),
        _clients_with(http),
    )
    by_url = {p["url"]: p for p in result["data"]["probes"]}
    assert by_url["https://example.com/admin/secret"]["allowed"] is False
    assert by_url["https://example.com/admin/secret"]["matched_rule"]["path"] == "/admin/"
    # RFC 9309 longest-match: "/admin/public" (13 chars) beats "/admin/" (7 chars).
    assert by_url["https://example.com/admin/public/page"]["allowed"] is True
    assert by_url["https://example.com/admin/public/page"]["matched_rule"]["path"] == "/admin/public"
    # No rule matches /anywhere/else, defaults to allow.
    assert by_url["https://example.com/anywhere/else"]["allowed"] is True
    assert by_url["https://example.com/anywhere/else"]["matched_rule"] is None


def test_robots_validate_rejects_relative_site_url(make_config):
    http = FakeHttpClient({})
    result = robots_tools.robots_txt_validate({"site_url": "/no-host"}, make_config(), _clients_with(http))
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.INVALID_INPUT
