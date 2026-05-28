"""Offline tests for inspect_schema and validate_schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from seo_mcp.clients.errors import ApiError
from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import schema_tools


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


def _html_response(body: str, *, status: int = 200, url: str = "https://example.com/") -> HttpResponse:
    return HttpResponse(
        status=status,
        headers={"content-type": "text/html; charset=utf-8"},
        body_bytes=body.encode("utf-8"),
        final_url=url,
    )


def _wrap(jsonld: str) -> str:
    return f'<html><head><script type="application/ld+json">{jsonld}</script></head></html>'


def _clients(http: FakeHttpClient) -> dict[str, Any]:
    return {"http": http}


# --- extraction -----------------------------------------------------------


def test_inspect_schema_extracts_single_block(make_config):
    body = _wrap('{"@context":"https://schema.org","@type":"Article","headline":"Hello"}')
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.inspect_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    assert result["ok"] is True
    d = result["data"]
    assert d["block_count"] == 1
    assert d["type_counts"] == {"Article": 1}
    assert d["samples"]["Article"]["headline"] == "Hello"


def test_inspect_schema_flattens_graph_wrapper(make_config):
    body = _wrap('{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"X"},{"@type":"WebSite","name":"Y"}]}')
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.inspect_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    d = result["data"]
    assert d["block_count"] == 2
    assert d["type_counts"] == {"Organization": 1, "WebSite": 1}


def test_inspect_schema_flattens_array_block(make_config):
    body = _wrap('[{"@type":"Product","name":"A"},{"@type":"Product","name":"B"}]')
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.inspect_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    assert result["data"]["type_counts"] == {"Product": 2}
    assert result["data"]["block_count"] == 2


def test_inspect_schema_records_parse_errors(make_config):
    body = _wrap('{"@type":"Article", oops not json}')
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.inspect_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    d = result["data"]
    assert d["parse_errors"] == 1
    assert d["block_count"] == 1


def test_inspect_schema_handles_multiple_script_tags(make_config):
    body = (
        '<html><head>'
        '<script type="application/ld+json">{"@type":"Article","headline":"A"}</script>'
        '<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":[1,2,3]}</script>'
        '</head></html>'
    )
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.inspect_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    d = result["data"]
    assert d["block_count"] == 2
    assert set(d["type_counts"]) == {"Article", "BreadcrumbList"}


def test_inspect_schema_ignores_non_jsonld_script(make_config):
    body = '<html><head><script type="text/javascript">var x = 1;</script></head></html>'
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.inspect_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    assert result["data"]["block_count"] == 0


def test_inspect_schema_non_2xx_returns_upstream_error(make_config):
    http = FakeHttpClient({"https://example.com/": _html_response("not found", status=404)})
    result = schema_tools.inspect_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.UPSTREAM_ERROR


# --- validate_schema ------------------------------------------------------


def test_validate_schema_article_passes_with_required(make_config):
    body = _wrap('{"@type":"Article","headline":"X","author":{"@type":"Person","name":"A"},"datePublished":"2026-05-01"}')
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.validate_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    d = result["data"]
    assert d["summary"]["pass"] == 1
    assert d["summary"]["fail"] == 0
    finding = d["findings"][0]
    assert finding["verdict"] == "pass"
    assert finding["missing_required"] == []
    # Recommended fields image and publisher are missing
    assert "image" in finding["missing_recommended"]


def test_validate_schema_product_fails_without_name(make_config):
    body = _wrap('{"@type":"Product","description":"missing name"}')
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.validate_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    d = result["data"]
    assert d["summary"]["fail"] == 1
    assert d["findings"][0]["missing_required"] == ["name"]


def test_validate_schema_unknown_type(make_config):
    body = _wrap('{"@type":"FleetTracker","trackerId":"abc"}')
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.validate_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    d = result["data"]
    assert d["summary"]["unknown_type"] == 1
    assert d["findings"][0]["verdict"] == "unknown_type"


def test_validate_schema_recipe_full_required_set(make_config):
    body = _wrap('{"@type":"Recipe","name":"X","recipeIngredient":["a"],"recipeInstructions":"do it"}')
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.validate_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    assert result["data"]["summary"]["pass"] == 1


def test_validate_schema_type_filter(make_config):
    body = (
        '<html><head>'
        '<script type="application/ld+json">{"@type":"Article","headline":"X"}</script>'
        '<script type="application/ld+json">{"@type":"Product","description":"no name"}</script>'
        '</head></html>'
    )
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    # Only check Article; the Product missing-name should not appear as a fail.
    result = schema_tools.validate_schema(
        {"url": "https://example.com/", "types": ["Article"]},
        make_config(),
        _clients(http),
    )
    d = result["data"]
    assert d["summary"]["pass"] == 1
    assert d["summary"]["fail"] == 0
    # The Product entity gets classified as "unknown_type" relative to the filter,
    # since the filter excluded it from the recognized set.
    assert d["summary"]["unknown_type"] == 1


def test_validate_schema_empty_string_required_field_counts_as_missing(make_config):
    body = _wrap('{"@type":"Article","headline":""}')
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.validate_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    finding = result["data"]["findings"][0]
    assert finding["verdict"] == "fail"
    assert finding["missing_required"] == ["headline"]


def test_validate_schema_parse_error_in_block(make_config):
    body = _wrap('{"@type":"Article", broken')
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.validate_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    d = result["data"]
    assert d["summary"]["parse_error"] == 1
    assert d["findings"][0]["verdict"] == "parse_error"


def test_validate_schema_array_type_field(make_config):
    # schema.org allows @type to be a list; Article + BlogPosting should both match.
    body = _wrap('{"@type":["Article","BlogPosting"],"headline":"H"}')
    http = FakeHttpClient({"https://example.com/": _html_response(body)})
    result = schema_tools.validate_schema({"url": "https://example.com/"}, make_config(), _clients(http))
    types_seen = [f.get("type") for f in result["data"]["findings"]]
    assert "Article" in types_seen
    assert "BlogPosting" in types_seen
