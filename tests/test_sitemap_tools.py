"""Offline tests for sitemap_validate and sitemap_health."""

from __future__ import annotations

import gzip
from dataclasses import dataclass, field
from typing import Any


from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import sitemap_tools


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


def _xml_response(body: str, *, url: str = "https://example.com/sitemap.xml", status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"content-type": "application/xml"},
        body_bytes=body.encode("utf-8"),
        final_url=url,
    )


def _gz_response(body: str, url: str = "https://example.com/sitemap.xml.gz") -> HttpResponse:
    return HttpResponse(
        status=200,
        headers={"content-type": "application/gzip"},
        body_bytes=gzip.compress(body.encode("utf-8")),
        final_url=url,
    )


_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/a</loc><lastmod>2026-05-01</lastmod></url>
  <url><loc>https://example.com/b</loc></url>
  <url><loc>https://other.com/c</loc><lastmod>2026-05-02</lastmod></url>
</urlset>"""

_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-1.xml</loc><lastmod>2026-05-01</lastmod></sitemap>
  <sitemap><loc>https://example.com/sitemap-2.xml</loc></sitemap>
</sitemapindex>"""


# --- sitemap_validate -----------------------------------------------------


def test_sitemap_validate_urlset(make_config):
    http = FakeHttpClient({"https://example.com/sitemap.xml": _xml_response(_URLSET)})
    result = sitemap_tools.sitemap_validate({"sitemap_url": "https://example.com/sitemap.xml"}, make_config(), _clients_with(http))
    assert result["ok"] is True
    d = result["data"]
    assert d["kind"] == "urlset"
    assert d["entry_count"] == 3
    assert d["cross_host_count"] == 1
    assert d["missing_lastmod_count"] == 1
    assert "cross_host_entries" in d["findings"]
    assert "missing_lastmod" in d["findings"]


def test_sitemap_validate_sitemapindex(make_config):
    http = FakeHttpClient({"https://example.com/sitemap.xml": _xml_response(_INDEX)})
    result = sitemap_tools.sitemap_validate({"sitemap_url": "https://example.com/sitemap.xml"}, make_config(), _clients_with(http))
    d = result["data"]
    assert d["kind"] == "sitemapindex"
    assert d["entry_count"] == 2
    # Per Round-5 §10a.ii: missing_lastmod is now emitted on sitemap-index
    # too (one of the two entries in _INDEX has no lastmod). Sitemaps.org
    # uses sitemap-index lastmod to tell crawlers when the underlying
    # sub-sitemap changed; missing it IS a finding worth surfacing.
    assert "missing_lastmod" in d["findings"]
    assert d["missing_lastmod_count"] == 1


def test_sitemap_validate_handles_gzip(make_config):
    http = FakeHttpClient({"https://example.com/sitemap.xml.gz": _gz_response(_URLSET)})
    result = sitemap_tools.sitemap_validate({"sitemap_url": "https://example.com/sitemap.xml.gz"}, make_config(), _clients_with(http))
    assert result["ok"] is True
    assert result["data"]["entry_count"] == 3


def test_sitemap_validate_bad_xml(make_config):
    http = FakeHttpClient({"https://example.com/sitemap.xml": _xml_response("<not-valid")})
    result = sitemap_tools.sitemap_validate({"sitemap_url": "https://example.com/sitemap.xml"}, make_config(), _clients_with(http))
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.INVALID_INPUT


def test_sitemap_validate_rejects_wrong_root(make_config):
    body = '<?xml version="1.0"?><rss><channel/></rss>'
    http = FakeHttpClient({"https://example.com/sitemap.xml": _xml_response(body)})
    result = sitemap_tools.sitemap_validate({"sitemap_url": "https://example.com/sitemap.xml"}, make_config(), _clients_with(http))
    assert result["ok"] is False
    assert "expected" in result["error"]["message"]


# --- sitemap_health -------------------------------------------------------


def test_sitemap_health_samples_urlset(make_config):
    sub_a = HttpResponse(status=200, headers={}, body_bytes=b"", final_url="https://example.com/a")
    sub_b = HttpResponse(status=404, headers={}, body_bytes=b"", final_url="https://example.com/b")
    sub_c = HttpResponse(status=200, headers={}, body_bytes=b"", final_url="https://other.com/c")
    http = FakeHttpClient({
        "https://example.com/sitemap.xml": _xml_response(_URLSET),
        "https://example.com/a": sub_a,
        "https://example.com/b": sub_b,
        "https://other.com/c": sub_c,
    })
    result = sitemap_tools.sitemap_health({"sitemap_url": "https://example.com/sitemap.xml"}, make_config(), _clients_with(http))
    assert result["ok"] is True
    d = result["data"]
    assert d["sampled"] == 3
    assert d["status_histogram"] == {"200": 2, "404": 1}
    assert any(item["url"] == "https://example.com/b" and item["status"] == 404 for item in d["non_2xx_examples"])


def test_sitemap_health_descends_into_index(make_config):
    sub_urlset = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/x</loc><lastmod>2026-05-01</lastmod></url>
</urlset>"""
    ok_resp = HttpResponse(status=200, headers={}, body_bytes=b"", final_url="https://example.com/x")
    http = FakeHttpClient({
        "https://example.com/sitemap.xml": _xml_response(_INDEX),
        "https://example.com/sitemap-1.xml": _xml_response(sub_urlset, url="https://example.com/sitemap-1.xml"),
        "https://example.com/sitemap-2.xml": _xml_response(sub_urlset, url="https://example.com/sitemap-2.xml"),
        "https://example.com/x": ok_resp,
    })
    result = sitemap_tools.sitemap_health(
        {"sitemap_url": "https://example.com/sitemap.xml", "sample_size": 1},
        make_config(),
        _clients_with(http),
    )
    d = result["data"]
    assert d["kind"] == "sitemapindex"
    assert d["status_histogram"] == {"200": 1}
