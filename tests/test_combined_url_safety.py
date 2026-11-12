"""Offline regressions for URL handling in the combined candidate."""

import pytest

from seo_mcp.clients.errors import ApiError
from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools.linkgraph_tools import internal_link_graph
from seo_mcp.tools.onpage_serp_gap_tools import _fetch_parsed, onpage_serp_gap
from seo_mcp.tools.robots_tools import robots_txt_validate


class NeverFetch:
    def fetch(self, *args, **kwargs):
        pytest.fail("malformed URL must not reach the HTTP client")


@pytest.mark.parametrize("handler,key", [(robots_txt_validate, "site_url"), (internal_link_graph, "start_url")])
@pytest.mark.parametrize("url", [
    "https://[bad/canary-9821", "https://example.test:99999/?key=canary-9821",
    "https://user:canary-9821@example.test/", "https://example.test/\r\ncanary-9821",
    "https://[fe80::1%25en0]/", 123,
])
def test_malformed_authority_returns_safe_error_without_fetch(handler, key, url):
    result = handler({key: url}, None, {"http": NeverFetch()})
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.INVALID_INPUT
    assert "canary-9821" not in str(result)


class NonSuccessHttp:
    def fetch(self, url, **kwargs):
        return HttpResponse(403, {}, b"", url)


def test_serp_gap_target_status_error_does_not_echo_url():
    url = "https://example.test/canary-path?token=canary-9821"
    result = onpage_serp_gap({"target_url": url}, None, {"http": NonSuccessHttp()})
    assert result["ok"] is False
    assert result["error"]["details"]["status"] == 403
    assert "canary-path" not in str(result) and "canary-9821" not in str(result)


def test_serp_gap_competitor_status_error_does_not_echo_url():
    url = "https://example.test/canary-path?token=canary-9821"
    with pytest.raises(ApiError) as error:
        _fetch_parsed(NonSuccessHttp(), url, public_only=True)
    result = error.value.to_envelope("technical")
    assert result["error"]["details"]["status"] == 403
    assert "canary-path" not in str(result) and "canary-9821" not in str(result)


def test_linkgraph_skips_malformed_page_links():
    class Page:
        def fetch(self, url, **kwargs):
            return HttpResponse(200, {}, b"<a href=\"http://[bad\">bad</a><a href=\"/ok\">ok</a>", url)

    result = internal_link_graph({"start_url": "https://example.test/", "max_pages": 1}, None, {"http": Page()})
    assert result["ok"] is True
    assert result["data"]["pages_discovered"] == 2
    assert any(page["url"] == "https://example.test/ok" for page in result["data"]["pages"])
