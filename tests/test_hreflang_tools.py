"""Offline tests for hreflang_consistency_check."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


from seo_mcp.clients.errors import ApiError
from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import hreflang_tools


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


def _page(url: str, alternates: list[tuple[str, str]], *, status: int = 200) -> HttpResponse:
    """Build an HTML page declaring the given (hreflang, href) alternates."""
    link_tags = "\n".join(
        f'<link rel="alternate" hreflang="{lang}" href="{href}">' for lang, href in alternates
    )
    body = f"<html><head>{link_tags}</head></html>"
    return HttpResponse(
        status=status,
        headers={"content-type": "text/html"},
        body_bytes=body.encode("utf-8"),
        final_url=url,
    )


def _head_ok(url: str, status: int = 200) -> HttpResponse:
    return HttpResponse(status=status, headers={}, body_bytes=b"", final_url=url)


def _clients(http: FakeHttpClient) -> dict[str, Any]:
    return {"http": http}


# --- happy path -----------------------------------------------------------


def test_hreflang_reciprocal_pair_passes(make_config):
    en = "https://example.com/en"
    fr = "https://example.com/fr"
    http = FakeHttpClient({
        en: _page(en, [("en", en), ("fr", fr)]),
        fr: _page(fr, [("en", en), ("fr", fr)]),
    })
    result = hreflang_tools.hreflang_consistency_check({"urls": [en, fr]}, make_config(), _clients(http))
    d = result["data"]
    assert d["fetch_errors"] == []
    assert d["reciprocity_misses"] == []
    assert d["broken_targets"] == []
    # Both pages contain a self-link and 2 entries -> no missing_x_default flag.
    for finding in d["findings"]:
        assert finding["flags"] == []


# --- reciprocity failures -------------------------------------------------


def test_hreflang_one_way_link_flagged(make_config):
    en = "https://example.com/en"
    fr = "https://example.com/fr"
    http = FakeHttpClient({
        en: _page(en, [("en", en), ("fr", fr)]),
        # fr is missing the back-link to en
        fr: _page(fr, [("fr", fr)]),
    })
    result = hreflang_tools.hreflang_consistency_check({"urls": [en, fr]}, make_config(), _clients(http))
    misses = result["data"]["reciprocity_misses"]
    assert len(misses) == 1
    assert misses[0]["from"] == en
    assert misses[0]["to"] == fr
    assert misses[0]["hreflang"] == "fr"


# --- duplicate hreflang ---------------------------------------------------


def test_hreflang_duplicate_value_within_page(make_config):
    a = "https://example.com/a"
    b = "https://example.com/b"
    http = FakeHttpClient({
        a: _page(a, [("en", a), ("en", b)]),
        b: _page(b, [("en", a), ("en", b)]),
    })
    result = hreflang_tools.hreflang_consistency_check({"urls": [a, b]}, make_config(), _clients(http))
    a_finding = next(f for f in result["data"]["findings"] if f["url"] == a)
    assert "duplicate_hreflang" in a_finding["flags"]


# --- self-link missing ----------------------------------------------------


def test_hreflang_missing_self_link(make_config):
    a = "https://example.com/a"
    b = "https://example.com/b"
    http = FakeHttpClient({
        a: _page(a, [("fr", b)]),  # no self link to a itself
        b: _page(b, [("en", a), ("fr", b)]),
    })
    result = hreflang_tools.hreflang_consistency_check({"urls": [a, b]}, make_config(), _clients(http))
    a_finding = next(f for f in result["data"]["findings"] if f["url"] == a)
    assert "missing_self_link" in a_finding["flags"]


# --- missing x-default with 3+ variants -----------------------------------


def test_hreflang_missing_x_default_flag(make_config):
    a = "https://example.com/a"
    b = "https://example.com/b"
    c = "https://example.com/c"
    http = FakeHttpClient({
        a: _page(a, [("en", a), ("fr", b), ("de", c)]),
        b: _page(b, [("en", a), ("fr", b), ("de", c)]),
        c: _page(c, [("en", a), ("fr", b), ("de", c)]),
    })
    result = hreflang_tools.hreflang_consistency_check({"urls": [a, b, c]}, make_config(), _clients(http))
    for finding in result["data"]["findings"]:
        assert "missing_x_default" in finding["flags"]


def test_hreflang_x_default_present_no_flag(make_config):
    a = "https://example.com/a"
    b = "https://example.com/b"
    c = "https://example.com/c"
    alts = [("en", a), ("fr", b), ("de", c), ("x-default", a)]
    http = FakeHttpClient({
        a: _page(a, alts),
        b: _page(b, alts),
        c: _page(c, alts),
    })
    result = hreflang_tools.hreflang_consistency_check({"urls": [a, b, c]}, make_config(), _clients(http))
    for finding in result["data"]["findings"]:
        assert "missing_x_default" not in finding["flags"]


# --- broken targets -------------------------------------------------------


def test_hreflang_external_broken_target(make_config):
    a = "https://example.com/a"
    b = "https://example.com/b"
    external_dead = "https://other.com/dead"
    http = FakeHttpClient({
        a: _page(a, [("en", a), ("fr", b), ("de", external_dead)]),
        b: _page(b, [("en", a), ("fr", b)]),
        external_dead: _head_ok(external_dead, status=404),
    })
    result = hreflang_tools.hreflang_consistency_check({"urls": [a, b]}, make_config(), _clients(http))
    broken = result["data"]["broken_targets"]
    assert any(t["url"] == external_dead and t["status"] == 404 for t in broken)


# --- input validation -----------------------------------------------------


def test_hreflang_rejects_single_url(make_config):
    http = FakeHttpClient({})
    result = hreflang_tools.hreflang_consistency_check({"urls": ["https://example.com/"]}, make_config(), _clients(http))
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.INVALID_INPUT


def test_hreflang_skips_unreachable_url(make_config):
    a = "https://example.com/a"
    b = "https://example.com/b"
    http = FakeHttpClient({
        a: _page(a, [("en", a), ("fr", b)]),
        b: ApiError(ErrorCode.UPSTREAM_ERROR, "DNS failure"),
    })
    result = hreflang_tools.hreflang_consistency_check({"urls": [a, b]}, make_config(), _clients(http))
    d = result["data"]
    assert d["fetched"] == [a]
    assert any(e["url"] == b for e in d["fetch_errors"])
