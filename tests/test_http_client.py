"""Tests for the generic HttpClient using a mocked pinned transport."""

from __future__ import annotations

import pytest

from seo_mcp.clients.errors import ApiError
from seo_mcp.clients.http import HttpClient, build_http_client
from seo_mcp.errors import ErrorCode


@pytest.fixture(autouse=True)
def mock_dns(monkeypatch):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {"8.8.8.8"})
    monkeypatch.setattr("urllib.request.getproxies", dict)


def _stub(responses):
    """Return a fake pinned transport that pops one canned response per call.

    Each canned response is (status, headers_dict, body_bytes). Call records
    appended to ``calls``."""
    calls: list[tuple[str, str]] = []
    queue = list(responses)

    def fake(method, url, *, max_bytes, extra_headers, target):
        calls.append((method, url))
        if not queue:
            raise AssertionError(f"unexpected extra call: {method} {url}")
        return queue.pop(0)

    return fake, calls


def test_build_returns_client():
    assert isinstance(build_http_client(), HttpClient)


def test_fetch_rejects_non_http_url():
    c = HttpClient()
    with pytest.raises(ApiError) as ei:
        c.fetch("file:///etc/passwd")
    assert ei.value.code == ErrorCode.INVALID_INPUT


def test_fetch_returns_terminal_response():
    c = HttpClient()
    fake, calls = _stub([(200, {"content-type": "text/html; charset=utf-8"}, b"<html><body>hi</body></html>")])
    c._http_request_public = fake
    resp = c.fetch("https://example.com/")
    assert resp.status == 200
    assert resp.final_url == "https://example.com/"
    assert resp.body_text.startswith("<html>")
    assert resp.redirect_chain == []
    assert calls == [("GET", "https://example.com/")]


def test_fetch_follows_redirect_and_records_chain():
    c = HttpClient()
    fake, calls = _stub([
        (301, {"location": "/new"}, b""),
        (200, {"content-type": "text/html"}, b"final"),
    ])
    c._http_request_public = fake
    resp = c.fetch("https://example.com/old")
    assert resp.status == 200
    assert resp.final_url == "https://example.com/new"
    assert len(resp.redirect_chain) == 1
    assert resp.redirect_chain[0].status == 301
    assert resp.redirect_chain[0].location == "https://example.com/new"
    assert [c[1] for c in calls] == ["https://example.com/old", "https://example.com/new"]


def test_fetch_no_follow_returns_redirect():
    c = HttpClient()
    fake, _ = _stub([(301, {"location": "/new"}, b"")])
    c._http_request_public = fake
    resp = c.fetch("https://example.com/", follow_redirects=False)
    assert resp.status == 301
    assert resp.redirect_chain == []


def test_fetch_detects_redirect_loop():
    c = HttpClient()
    fake, _ = _stub([
        (302, {"location": "https://example.com/a"}, b""),
        (302, {"location": "https://example.com/a"}, b""),
    ])
    c._http_request_public = fake
    with pytest.raises(ApiError) as ei:
        c.fetch("https://example.com/a")
    assert ei.value.code == ErrorCode.UPSTREAM_ERROR
    assert "loop" in ei.value.message.lower()


def test_fetch_caps_redirect_chain():
    c = HttpClient()
    fake, _ = _stub([
        (301, {"location": f"https://example.com/{i+1}"}, b"") for i in range(20)
    ])
    c._http_request_public = fake
    with pytest.raises(ApiError) as ei:
        c.fetch("https://example.com/0", max_redirects=3)
    assert ei.value.code == ErrorCode.UPSTREAM_ERROR
    assert "max_redirects" in ei.value.message


def test_redirect_loop_reports_redacted_hop_diagnostics():
    client = HttpClient()
    client._http_request_public, _ = _stub([(302, {"location": "/secret-path?key=canary-9821"}, b"")])
    with pytest.raises(ApiError) as error:
        client.fetch("https://example.com/secret-path?key=canary-9821")
    assert error.value.details == {"chain": ["https://[redacted]"], "statuses": [302]}
    assert "canary-9821" not in str(error.value.to_envelope("technical"))


def test_denied_redirect_reports_redacted_prior_hops(monkeypatch):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {"8.8.8.8"} if host == "example.com" else {"127.0.0.1"})
    client = HttpClient()
    client._http_request_public, _ = _stub([(302, {"location": "http://localhost/canary-9821"}, b"")])
    with pytest.raises(ApiError) as error:
        client.fetch("https://example.com/private-path")
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert error.value.details == {"chain": ["https://[redacted]"], "statuses": [302]}
    assert "canary-9821" not in str(error.value.to_envelope("technical"))


def test_body_text_decodes_with_declared_charset():
    c = HttpClient()
    fake, _ = _stub([(200, {"content-type": "text/html; charset=iso-8859-1"}, b"caf\xe9")])
    c._http_request_public = fake
    resp = c.fetch("https://example.com/")
    assert resp.body_text == "café"
