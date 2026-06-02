"""Offline tests for the two IndexNow tools and the IndexNowClient. The
single network seam (IndexNowClient._http_request) is monkeypatched to
inject canned responses or raise ApiError."""

from __future__ import annotations

import pytest

from seo_mcp.clients.errors import ApiError
from seo_mcp.clients.http import HttpResponse
from seo_mcp.clients.indexnow import IndexNowClient, _host_of, build_indexnow_client
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import indexnow_tools


class _FakeHttp:
    """Maps any fetch to one canned (status, body), recording the URLs hit.
    Stands in for the shared HttpClient used by the key-file pre-flight."""

    def __init__(self, status: int, body: str) -> None:
        self._status = status
        self._body = body
        self.calls: list[str] = []

    def fetch(self, url: str, **_):
        self.calls.append(url)
        return HttpResponse(
            status=self._status,
            headers={"content-type": "text/plain"},
            body_bytes=self._body.encode("utf-8"),
            final_url=url,
        )


class _FakeHttpUnreachable:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch(self, url: str, **_):
        self.calls.append(url)
        raise ApiError(ErrorCode.UPSTREAM_ERROR, "host unreachable")


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
    # Configured key_location is on example.com but the URLs are on
    # www.example.com, so keyLocation is derived for the SUBMITTED host
    # (FEEDBACK §25: keyLocation must match the host or IndexNow 422s).
    assert body["keyLocation"] == "https://www.example.com/testkey1234.txt"


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


def test_bulk_submit_derives_key_location_from_host_when_not_configured(make_config):
    # No configured key_location -> derive the conventional host-root location
    # for the submitted host (FEEDBACK §25; was previously omitted entirely).
    client = IndexNowClient(key="k123")
    calls = []
    client._http_request = lambda m, u, b: calls.append((m, u, b)) or {"accepted": True, "status": 200}
    indexnow_tools.indexnow_bulk_submit(
        {"urls": ["https://x.com/a"]}, make_config(), {"indexnow": client}
    )
    _, _, body = calls[0]
    assert body["keyLocation"] == "https://x.com/k123.txt"


def test_submit_derives_key_location_per_host(make_config):
    # Single submit to a foreign host derives keyLocation for THAT host.
    client = _client_with_recorder()  # configured key_location on example.com
    indexnow_tools.indexnow_submit({"url": "https://www.zapinventory.com/p"}, make_config(), {"indexnow": client})
    _, url, _ = client._calls[0]
    assert "keyLocation=https%3A%2F%2Fwww.zapinventory.com%2Ftestkey1234.txt" in url


def test_submit_honors_same_host_configured_key_location(make_config):
    # A configured non-root key_location IS honored when on the submitted host.
    client = IndexNowClient(key="testkey1234", key_location="https://www.example.com/.well-known/testkey1234.txt")
    calls = []
    client._http_request = lambda m, u, b: calls.append((m, u, b)) or {"accepted": True, "status": 200}
    indexnow_tools.indexnow_submit({"url": "https://www.example.com/a"}, make_config(), {"indexnow": client})
    _, url, _ = calls[0]
    assert "keyLocation=https%3A%2F%2Fwww.example.com%2F.well-known%2Ftestkey1234.txt" in url


# --- key-file pre-flight (FEEDBACK §20 §3c/§3d) ---------------------------

_KEY = "testkey1234"
# Same host as the www.example.com submit URLs below, so the configured
# key_location is honored (per-host match) and the preflight fetches exactly it.
_LOC = "https://www.example.com/testkey1234.txt"


def _cfg_with_key(make_config, location=_LOC):
    kw = {"SEO_MCP_INDEXNOW_KEY": _KEY}
    if location is not None:
        kw["SEO_MCP_INDEXNOW_KEY_LOCATION"] = location
    return make_config(**kw)


def test_submit_blocks_when_key_file_unreachable(make_config):
    client = _client_with_recorder()
    http = _FakeHttpUnreachable()
    result = indexnow_tools.indexnow_submit(
        {"url": "https://www.example.com/a"}, _cfg_with_key(make_config), {"indexnow": client, "http": http}
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"
    assert http.calls == [_LOC]
    assert client._calls == []  # never forwarded to IndexNow


def test_submit_blocks_when_key_file_body_mismatch(make_config):
    client = _client_with_recorder()
    http = _FakeHttp(200, "someothervalue")  # 200 but wrong body
    result = indexnow_tools.indexnow_submit(
        {"url": "https://www.example.com/a"}, _cfg_with_key(make_config), {"indexnow": client, "http": http}
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert "does not contain your key" in result["error"]["message"]
    assert client._calls == []


def test_submit_blocks_when_key_file_404(make_config):
    client = _client_with_recorder()
    http = _FakeHttp(404, "Not Found")
    result = indexnow_tools.indexnow_submit(
        {"url": "https://www.example.com/a"}, _cfg_with_key(make_config), {"indexnow": client, "http": http}
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["details"]["preflight_status"] == 404
    assert client._calls == []


def test_submit_proceeds_when_key_file_valid(make_config):
    client = _client_with_recorder()
    http = _FakeHttp(200, _KEY + "\n")  # trailing newline tolerated
    result = indexnow_tools.indexnow_submit(
        {"url": "https://www.example.com/a"}, _cfg_with_key(make_config), {"indexnow": client, "http": http}
    )
    assert result["ok"] is True
    assert http.calls == [_LOC]
    assert client._calls  # forwarded to IndexNow


def test_submit_skip_preflight_bypasses_key_file_check(make_config):
    client = _client_with_recorder()
    http = _FakeHttp(404, "")  # would block, but skipped
    result = indexnow_tools.indexnow_submit(
        {"url": "https://www.example.com/a", "skip_preflight": True},
        _cfg_with_key(make_config),
        {"indexnow": client, "http": http},
    )
    assert result["ok"] is True
    assert http.calls == []  # pre-flight entirely skipped
    assert client._calls


def test_submit_uses_default_key_file_url_when_no_location(make_config):
    client = _client_with_recorder()
    http = _FakeHttp(200, _KEY)
    indexnow_tools.indexnow_submit(
        {"url": "https://www.example.com/a"}, _cfg_with_key(make_config, location=None), {"indexnow": client, "http": http}
    )
    # No SEO_MCP_INDEXNOW_KEY_LOCATION -> default https://<host>/<key>.txt
    assert http.calls == ["https://www.example.com/testkey1234.txt"]


def test_verify_key_file_preflights_submitted_host_not_configured(make_config):
    # Configured key_location is on example.com, but we submit to a foreign host.
    # The preflight must check the SUBMITTED host's key file, not the configured
    # one — otherwise it validates the wrong file, passes, and waves a doomed
    # submit through to an IndexNow 422 (FEEDBACK §25, the masked-by-preflight bug).
    client = _client_with_recorder()
    http = _FakeHttp(200, _KEY)
    cfg = make_config(SEO_MCP_INDEXNOW_KEY=_KEY, SEO_MCP_INDEXNOW_KEY_LOCATION="https://www.example.com/testkey1234.txt")
    indexnow_tools.indexnow_submit({"url": "https://www.zapinventory.com/p"}, cfg, {"indexnow": client, "http": http})
    assert http.calls == ["https://www.zapinventory.com/testkey1234.txt"]


def test_submit_skips_preflight_when_no_http_client(make_config):
    # Minimal call (no "http" wired) must still work: pre-flight degrades to skip.
    client = _client_with_recorder()
    result = indexnow_tools.indexnow_submit(
        {"url": "https://www.example.com/a"}, _cfg_with_key(make_config), {"indexnow": client}
    )
    assert result["ok"] is True
    assert client._calls


def test_bulk_submit_blocks_when_key_file_unreachable(make_config):
    client = _client_with_recorder()
    http = _FakeHttpUnreachable()
    result = indexnow_tools.indexnow_bulk_submit(
        {"urls": ["https://www.example.com/a", "https://www.example.com/b"]},
        _cfg_with_key(make_config),
        {"indexnow": client, "http": http},
    )
    assert result["error"]["code"] == "INVALID_INPUT"
    assert client._calls == []


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
