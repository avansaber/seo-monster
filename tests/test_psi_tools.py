"""Offline tests for psi_analyze and the PSI client shaping. The single network
seam (PsiClient._http_get) is monkeypatched to inject canned JSON or raise."""

from __future__ import annotations

import pytest

from seo_mcp.clients.errors import ApiError, map_http_status
from seo_mcp.clients.psi import PsiClient
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import psi_tools


PSI_WITH_FIELD = {
    "lighthouseResult": {
        "categories": {
            "performance": {"score": 0.87},
            "accessibility": {"score": 0.95},
            "best-practices": {"score": 0.92},
            "seo": {"score": 1.0},
        },
        "audits": {
            "largest-contentful-paint": {"displayValue": "2.1 s"},
            "cumulative-layout-shift": {"displayValue": "0.02"},
            "total-blocking-time": {"displayValue": "120 ms"},
            "speed-index": {"displayValue": "3.0 s"},
            "interactive": {"displayValue": "3.5 s"},
            "first-contentful-paint": {"displayValue": "1.4 s"},
        },
    },
    "loadingExperience": {
        "overall_category": "FAST",
        "metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2100, "category": "FAST"},
            "INTERACTION_TO_NEXT_PAINT": {"percentile": 180, "category": "FAST"},
            "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 2, "category": "FAST"},
            "FIRST_CONTENTFUL_PAINT_MS": {"percentile": 1400, "category": "FAST"},
            "EXPERIMENTAL_TIME_TO_FIRST_BYTE": {"percentile": 600, "category": "AVERAGE"},
        },
    },
}

PSI_NO_FIELD = {
    "lighthouseResult": {
        "categories": {"performance": {"score": 0.5}, "seo": {"score": 0.9}},
        "audits": {"largest-contentful-paint": {"displayValue": "4.0 s"}},
    },
    "loadingExperience": {},  # no CrUX field data (low-traffic site)
}


def _client_returning(payload, capture=None):
    client = PsiClient(api_key="testkey")

    def _fake_get(url):
        if capture is not None:
            capture.append(url)
        return payload

    client._http_get = _fake_get
    return client


def _client_raising(error):
    client = PsiClient(api_key="testkey")

    def _fake_get(url):
        raise error

    client._http_get = _fake_get
    return client


def test_analyze_happy_path_with_field_data(make_config):
    client = _client_returning(PSI_WITH_FIELD)
    result = psi_tools.psi_analyze({"url": "https://www.example.com/"}, make_config(), {"psi": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["strategy"] == "mobile"
    assert data["lighthouse_scores"] == {"performance": 87, "accessibility": 95, "best-practices": 92, "seo": 100}
    assert data["lab_core_web_vitals"]["LCP"] == "2.1 s"
    assert data["field_data_available"] is True
    field = data["field_core_web_vitals"]
    assert field["overall_category"] == "FAST"
    assert field["LCP"] == {"p75_ms": 2100, "category": "FAST"}
    # CLS percentile is reported x100 by CrUX; exposed as a ratio.
    assert field["CLS"] == {"p75": 0.02, "category": "FAST"}


def test_analyze_without_field_data(make_config):
    client = _client_returning(PSI_NO_FIELD)
    result = psi_tools.psi_analyze({"url": "https://newsite.com/"}, make_config(), {"psi": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["field_data_available"] is False
    assert data["field_core_web_vitals"] is None
    # Missing categories surface as None rather than raising.
    assert data["lighthouse_scores"]["accessibility"] is None
    assert data["lighthouse_scores"]["performance"] == 50


def test_analyze_passes_strategy_and_categories_into_url(make_config):
    captured: list[str] = []
    client = _client_returning(PSI_WITH_FIELD, capture=captured)
    psi_tools.psi_analyze(
        {"url": "https://www.example.com/page?x=1", "strategy": "desktop", "categories": ["performance", "seo"]},
        make_config(),
        {"psi": client},
    )
    url = captured[0]
    assert "strategy=desktop" in url
    assert "category=performance" in url
    assert "category=seo" in url
    assert "key=testkey" in url
    # The page URL is percent-encoded into the url= param.
    assert "url=https%3A%2F%2Fwww.example.com%2Fpage%3Fx%3D1" in url


def test_analyze_requires_url(make_config):
    client = _client_returning(PSI_WITH_FIELD)
    result = psi_tools.psi_analyze({}, make_config(), {"psi": client})
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


def test_analyze_maps_upstream_error(make_config):
    client = _client_raising(ApiError(ErrorCode.AUTH_INVALID, "bad key"))
    result = psi_tools.psi_analyze({"url": "https://x.com"}, make_config(), {"psi": client})
    assert result["ok"] is False
    assert result["error"]["code"] == "AUTH_INVALID"
    assert result["error"]["service"] == "psi"


def test_analyze_missing_key_still_works(make_config):
    # No PSI_API_KEY: client built with key=None, anonymous endpoint. The url
    # must omit the key param but the call still succeeds.
    captured: list[str] = []
    client = PsiClient(api_key=None)
    client._http_get = lambda url: (captured.append(url) or PSI_WITH_FIELD)
    result = psi_tools.psi_analyze({"url": "https://x.com"}, make_config(), {"psi": client})
    assert result["ok"] is True
    assert "key=" not in captured[0]


# --- PSI client url building + http-status mapper --------------------------


def test_build_url_repeats_category_param():
    client = PsiClient(api_key="k")
    url = client._build_url("https://x.com", "mobile", ["performance", "seo"])
    assert url.count("category=") == 2


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (403, "forbidden", "AUTH_INVALID"),
        (400, "API key not valid. Please pass a valid API key.", "AUTH_INVALID"),
        (400, "bad url", "INVALID_INPUT"),
        (429, "rate limited", "RATE_LIMITED"),
        (500, "boom", "UPSTREAM_ERROR"),
    ],
)
def test_map_http_status(status, body, expected):
    error = map_http_status(status, body, service="PageSpeed Insights")
    assert str(error.code) == expected


def test_psi_429_has_actionable_remediation():
    # Round-2 feedback 2h: every other error envelope carries a remediation;
    # the PSI 429 used to return None and tell users "retry later" without
    # explaining why a key matters.
    error = map_http_status(429, "Quota exceeded", service="PageSpeed Insights")
    assert str(error.code) == "RATE_LIMITED"
    assert error.remediation is not None
    assert "PSI_API_KEY" in error.remediation


def test_psi_429_envelope_through_tool(make_config):
    # End-to-end: a 429 from analyze() reaches the tool envelope with the
    # remediation field populated.
    client = _client_raising(ApiError(ErrorCode.RATE_LIMITED, "PSI rate-limited",
                                      remediation="Set PSI_API_KEY for per-project quota."))
    result = psi_tools.psi_analyze({"url": "https://example.com"}, make_config(), {"psi": client})
    assert result["error"]["code"] == "RATE_LIMITED"
    assert "PSI_API_KEY" in result["error"]["remediation"]


def test_cf_429_keeps_generic_remediation():
    # Only PSI gets the key-specific remediation; CF's 429 still uses the
    # generic retry hint.
    error = map_http_status(429, "Rate limited", service="Cloudflare")
    assert error.remediation == "Retry after a short delay."


# --- psi_opportunities -----------------------------------------------------


PSI_OPPORTUNITIES = {
    "lighthouseResult": {
        "categories": {
            "performance": {"score": 0.6},
            "seo": {
                "score": 0.85,
                "auditRefs": [
                    {"id": "is-crawlable", "weight": 1},
                    {"id": "viewport", "weight": 1},
                    {"id": "document-title", "weight": 1},
                    {"id": "meta-description", "weight": 1},
                    {"id": "image-alt", "weight": 0},
                    {"id": "font-size", "weight": 1},
                    {"id": "unknown-future-audit", "weight": 0},
                    {"id": "structured-data", "weight": 0},  # manual -> dropped
                ],
            },
        },
        "audits": {
            "is-crawlable": {"title": "Page isn't blocked from indexing", "score": 1},
            "viewport": {"title": "Has a <meta name=viewport>", "score": 0},
            "document-title": {"title": "Document has a <title>", "score": 1},
            "meta-description": {"title": "Document has a meta description", "score": 0},
            "image-alt": {"title": "Image elements have [alt]", "score": 1},
            "font-size": {"title": "Legible font sizes", "score": 1},
            "unknown-future-audit": {"title": "Some new SEO audit", "score": 0},
            "structured-data": {"title": "Structured data is valid", "score": None, "scoreDisplayMode": "manual"},
            "render-blocking-resources": {
                "title": "Eliminate render-blocking resources",
                "displayValue": "Potential savings of 450 ms",
                "details": {"type": "opportunity", "overallSavingsMs": 450},
            },
            "unused-javascript": {
                "title": "Reduce unused JavaScript",
                "displayValue": "Potential savings of 1,200 ms",
                "details": {"type": "opportunity", "overallSavingsMs": 1200},
            },
            "uses-long-cache-ttl": {
                "title": "Serve static assets with an efficient cache policy",
                "details": {"type": "table"},  # not an opportunity
            },
        },
    },
}


def _sev(audits, audit_id):
    return next(a for a in audits if a["id"] == audit_id)


def test_opportunities_extracts_perf_and_seo(make_config):
    client = _client_returning(PSI_OPPORTUNITIES)
    result = psi_tools.psi_opportunities({"url": "https://www.example.com/"}, make_config(), {"psi": client})
    assert result["ok"] is True
    data = result["data"]
    assert data["strategy"] == "mobile"

    # Opportunities: only details.type == "opportunity", sorted by savings desc.
    opp_ids = [o["id"] for o in data["opportunities"]]
    assert opp_ids == ["unused-javascript", "render-blocking-resources"]
    assert data["opportunities"][0]["overall_savings_ms"] == 1200
    assert data["opportunities"][0]["display_value"] == "Potential savings of 1,200 ms"
    assert "uses-long-cache-ttl" not in opp_ids  # table type excluded

    # SEO audits graded by RULESETS §4 severity.
    seo = data["seo_audits"]
    seo_ids = {a["id"] for a in seo}
    assert "structured-data" not in seo_ids  # manual audit dropped
    assert _sev(seo, "is-crawlable")["severity"] == "critical"
    assert _sev(seo, "viewport")["severity"] == "critical"
    assert _sev(seo, "document-title")["severity"] == "high"
    assert _sev(seo, "meta-description")["severity"] == "high"
    assert _sev(seo, "image-alt")["severity"] == "medium"
    assert _sev(seo, "font-size")["severity"] == "low"
    # Unmapped audit still surfaces, graded info (not dropped).
    assert _sev(seo, "unknown-future-audit")["severity"] == "info"

    # passed reflects score == 1.
    assert _sev(seo, "is-crawlable")["passed"] is True
    assert _sev(seo, "viewport")["passed"] is False

    # Honest-bound note present.
    assert data["notes"]
    assert "ranking predictor" in data["notes"][0]


def test_opportunities_requires_url(make_config):
    client = _client_returning(PSI_OPPORTUNITIES)
    result = psi_tools.psi_opportunities({}, make_config(), {"psi": client})
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


def test_opportunities_maps_upstream_error(make_config):
    client = _client_raising(ApiError(ErrorCode.RATE_LIMITED, "rate"))
    result = psi_tools.psi_opportunities({"url": "https://x.com"}, make_config(), {"psi": client})
    assert result["ok"] is False
    assert result["error"]["code"] == "RATE_LIMITED"
