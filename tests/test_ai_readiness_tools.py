"""Offline tests for ai_citation_readiness (roadmap Track A, Wave 1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from seo_mcp.clients.errors import ApiError
from seo_mcp.clients.http import HttpResponse
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import ai_readiness_tools


@dataclass
class FakeHttp:
    body: str
    status: int = 200
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str, **_: Any) -> HttpResponse:
        self.calls.append(url)
        if isinstance(self.body, Exception):
            raise self.body
        return HttpResponse(
            status=self.status,
            headers={"content-type": "text/html"},
            body_bytes=self.body.encode("utf-8"),
            final_url=url,
        )


def _clients(body: Any, status: int = 200) -> dict[str, Any]:
    return {"http": FakeHttp(body, status)}


_GOOD = """
<html><head><title>Widgets</title>
<script type="application/ld+json">{"@type":"FAQPage"}</script></head>
<body>
<h1>Blue Widgets</h1>
<p>In 2026, 45% of buyers chose blue, across 1,200 tests and 30 vendors.</p>
<h2>Evidence</h2>
<blockquote>"Blue widgets win," said the lab.</blockquote>
<p>Sources: <a href="https://a.org/1">a</a> <a href="https://b.org/2">b</a>
<a href="https://c.org/3">c</a> <a href="https://d.org/4">d</a>
<a href="https://e.org/5">e</a></p>
<h2>More</h2><p>Some balanced prose about widgets and their many varied uses.</p>
</body></html>
"""


def test_good_page_scores_well_and_reports_schema_separately(make_config):
    res = ai_readiness_tools.ai_citation_readiness({"url": "https://x.com/p"}, make_config(), _clients(_GOOD))
    d = res["data"]
    assert d["rendered_blind"] is False
    assert d["readiness"]["evidence_tier"] == "A"
    assert d["readiness"]["band"] in ("moderate", "high")
    # schema/FAQ reported as informational only, NOT in the scored components.
    assert "FAQPage" in d["informational"]["schema_types"]
    assert d["informational"]["faq_detected"] is True
    assert "schema" not in d["readiness"]["components"]
    assert d["readiness"]["components"]["cited_sources"] == 1.0  # 5 external links


def test_render_blind_page_flagged(make_config):
    spa = "<html><body><div id=root></div>" + "<script>1</script>" * 5 + "</body></html>"
    res = ai_readiness_tools.ai_citation_readiness({"url": "https://x.com/spa"}, make_config(), _clients(spa))
    d = res["data"]
    assert d["rendered_blind"] is True
    assert d["readiness"]["components"]["extractable"] == 0.0
    assert any("RENDER-BLIND" in c for c in d["readiness"]["caveats"])


def test_keyword_stuffing_penalized(make_config):
    stuffed = "<html><body><h1>x</h1><p>" + ("widgets " * 80) + "</p></body></html>"
    res = ai_readiness_tools.ai_citation_readiness({"url": "https://x.com/s"}, make_config(), _clients(stuffed))
    d = res["data"]
    assert d["stuffing"]["flagged"] is True
    assert d["readiness"]["components"]["no_keyword_stuffing"] < 0.5


def test_missing_url_is_invalid_input(make_config):
    res = ai_readiness_tools.ai_citation_readiness({}, make_config(), _clients(_GOOD))
    assert res["ok"] is False
    assert res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_non_2xx_is_upstream_error(make_config):
    res = ai_readiness_tools.ai_citation_readiness({"url": "https://x.com/404"}, make_config(), _clients("nope", status=404))
    assert res["ok"] is False
    assert res["error"]["code"] == ErrorCode.UPSTREAM_ERROR


def test_no_http_client_is_auth_missing(make_config):
    res = ai_readiness_tools.ai_citation_readiness({"url": "https://x.com/p"}, make_config(), {})
    assert res["ok"] is False
    assert res["error"]["code"] == ErrorCode.AUTH_MISSING


def test_fetch_failure_envelope(make_config):
    res = ai_readiness_tools.ai_citation_readiness(
        {"url": "https://x.com/p"}, make_config(), _clients(ApiError(ErrorCode.UPSTREAM_ERROR, "dns"))
    )
    assert res["ok"] is False
