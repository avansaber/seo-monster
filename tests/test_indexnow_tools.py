"""Offline tests for the two IndexNow tools and the IndexNowClient. The
single network seam (IndexNowClient._http_request) is monkeypatched to
inject canned responses or raise ApiError."""

from __future__ import annotations

import pytest

from seo_mcp.clients.errors import ApiError
from seo_mcp.clients.indexnow import IndexNowClient, _host_of, build_indexnow_client
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import indexnow_tools


def _client_with_recorder():
    """Return a real IndexNowClient with _http_request replaced by a recorder."""
    client = IndexNowClient(key="testkey1234", key_location="https://example.com/testkey1234.txt")
    calls: list[tuple[str, str, dict | None]] = []

    def fake(method, url, body):
        calls.append((method, url, body))
        return {"accepted": True, "status": 200}

    client._http_request = fake
    client._calls = calls  # expose for assertions
    return client


def _client_raising(error):
    client = IndexNowClient(key="testkey1234")

    def fake(method, url, body):
        raise error

    client._http_request = fake
    return client


# --- host extraction + builder --------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.example.com/path", "www.example.com"),
        ("http://example.com", "example.com"),
        ("https://Sub.Example.com/x", "sub.example.com"),
        ("not-a-url", None),
    ],
)
def test_host_of(url, expected):
    assert _host_of(url) == expected


def test_build_indexnow_returns_none_without_key(make_config):
    assert build_indexnow_client(make_config()) is None


def test_build_indexnow_uses_configured_key(make_config):
    cfg = make_config(SEO_MCP_INDEXNOW_KEY="mykey1234")
    client = build_indexnow_client(cfg)
    assert client is not None
    assert client._key == "mykey1234"


# --- auth gating ----------------------------------------------------------


def test_tools_return_auth_missing_when_unconfigured(make_config):
    cfg = make_config()  # no IndexNow key, no client wired
    for name, handler in indexnow_tools.HANDLERS.items():
        result = handler({"url": "https://x.com/a", "urls": ["https://x.com/a"]}, cfg, {})
        assert result["error"]["code"] == "AUTH_MISSING", name
        assert result["error"]["service"] == "indexnow"


# --- single-URL submit ----------------------------------------------------


def test_submit_single_url_builds_get_with_key(make_config):
    client = _client_with_recorder()
    result = indexnow_tools.indexnow_submit(
        {"url": "https://www.example.com/article"}, make_config(), {"indexnow": client}
    )
    assert result["ok"] is True
    assert result["data"]["accepted"] is True
    method, url, body = client._calls[0]
    assert method == "GET"
    assert "url=https%3A%2F%2Fwww.example.com%2Farticle" in url
    assert "key=testkey1234" in url
    assert "keyLocation=" in url
    assert body is None


def test_submit_requires_url(make_config):
    client = _client_with_recorder()
    result = indexnow_tools.indexnow_submit({}, make_config(), {"indexnow": client})
    assert result["error"]["code"] == "INVALID_INPUT"


def test_submit_propagates_api_errors(make_config):
    client = _client_raising(ApiError(ErrorCode.AUTH_INVALID, "key not verified",
                                      remediation="Host the key file..."))
    result = indexnow_tools.indexnow_submit(
        {"url": "https://x.com/a"}, make_config(), {"indexnow": client}
    )
    assert result["error"]["code"] == "AUTH_INVALID"
    assert result["error"]["service"] == "indexnow"


# --- bulk submit ----------------------------------------------------------


def test_bulk_submit_posts_with_host_and_url_list(make_config):
    client = _client_with_recorder()
    urls = ["https://www.example.com/a", "https://www.example.com/b"]
    result = indexnow_tools.indexnow_bulk_submit({"urls": urls}, make_config(), {"indexnow": client})
    assert result["ok"] is True
    assert result["data"]["submitted_count"] == 2
    method, url, body = client._calls[0]
    assert method == "POST"
    assert url == "https://api.indexnow.org/indexnow"
    assert body["host"] == "www.example.com"
    assert body["key"] == "testkey1234"
    assert body["urlList"] == urls
    assert body["keyLocation"] == "https://example.com/testkey1234.txt"


def test_bulk_submit_rejects_mixed_hosts(make_config):
    client = _client_with_recorder()
    result = indexnow_tools.indexnow_bulk_submit(
        {"urls": ["https://a.com/x", "https://b.com/x"]},
        make_config(),
        {"indexnow": client},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "same host" in result["error"]["message"].lower()
    # No HTTP call should have happened.
    assert client._calls == []


def test_bulk_submit_rejects_empty_list(make_config):
    client = _client_with_recorder()
    result = indexnow_tools.indexnow_bulk_submit({"urls": []}, make_config(), {"indexnow": client})
    assert result["error"]["code"] == "INVALID_INPUT"


def test_bulk_submit_omits_key_location_when_not_configured(make_config):
    # Client constructed with no key_location.
    client = IndexNowClient(key="k123")
    calls = []
    client._http_request = lambda m, u, b: calls.append((m, u, b)) or {"accepted": True, "status": 200}
    indexnow_tools.indexnow_bulk_submit(
        {"urls": ["https://x.com/a"]}, make_config(), {"indexnow": client}
    )
    _, _, body = calls[0]
    assert "keyLocation" not in body


# --- raw client error mapping ---------------------------------------------


@pytest.mark.parametrize(
    "status,expected_code",
    [
        (400, "INVALID_INPUT"),
        (403, "AUTH_INVALID"),
        (422, "INVALID_INPUT"),
        (429, "RATE_LIMITED"),
        (500, "UPSTREAM_ERROR"),
    ],
)
def test_map_error_table(status, expected_code):
    error = IndexNowClient._map_error(status, "{}")
    assert str(error.code) == expected_code


def test_probe_returns_true_when_endpoint_responds():
    client = IndexNowClient(key="k")
    # Even a 400 means the endpoint is reachable; probe should return True.
    client._http_request = lambda m, u, b: (_ for _ in ()).throw(
        ApiError(ErrorCode.INVALID_INPUT, "missing url")
    )
    assert client.probe() is True


def test_probe_returns_false_on_transport_error():
    client = IndexNowClient(key="k")
    client._http_request = lambda m, u, b: (_ for _ in ()).throw(
        ApiError(ErrorCode.UPSTREAM_ERROR, "network down")
    )
    assert client.probe() is False
