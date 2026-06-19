"""Offline tests for ai_citation_track (roadmap Track A, Wave 4). A fake engine
client makes the sampling deterministic so the SoV / CI / volatility math is
verifiable without live, non-deterministic APIs."""

from __future__ import annotations

from seo_mcp.clients.errors import ApiError
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import ai_citation_tools as act


class FakeEngines:
    def __init__(self, responses):  # {engine: {prompt: {answer_text, citations}}}
        self._r = responses

    def available_engines(self):
        return sorted(self._r)

    def query(self, engine, prompt):
        return self._r[engine][prompt]


class FakeDFS:
    def __init__(self, citations):
        self._c = citations

    def serp(self, prompt, **_):
        return {"ai_overview_citations": self._c}


class FakeDFSText:
    def __init__(self, text, citations=None):
        self._t = text
        self._c = citations or []

    def serp(self, prompt, **_):
        return {"ai_overview_text": self._t, "ai_overview_citations": self._c}


def test_visibility_sov_and_volatility(make_config):
    responses = {
        "perplexity": {
            "p1": {"answer_text": "Acme is best", "citations": [{"url": "https://acme.com/x", "domain": "acme.com"}]},
            "p2": {"answer_text": "Globex wins", "citations": [{"url": "https://globex.com/y", "domain": "globex.com"}]},
        }
    }
    res = act.ai_citation_track(
        {"prompts": ["p1", "p2"], "brand": "Acme", "brand_domains": ["acme.com"], "competitors": ["Globex"], "samples": 3},
        make_config(),
        {"ai_engines": FakeEngines(responses)},
    )
    d = res["data"]
    assert d["query_count"] == 6  # 1 engine x 2 prompts x 3 samples
    assert d["overall"]["brand_visibility"]["value"] == 0.5
    assert d["overall"]["share_of_voice"] == 0.5  # Acme 3 vs Globex 3
    eng = d["by_engine"][0]
    assert eng["volatility"] == 0.0  # citations identical across samples
    assert eng["brand_citation_urls"] == ["https://acme.com/x"]
    ci = d["overall"]["brand_visibility"]["ci95"]
    assert ci[0] <= 0.5 <= ci[1]


def test_google_aio_via_dataforseo(make_config):
    dfs = FakeDFS([{"url": "https://acme.com/aio", "domain": "acme.com", "title": "Acme"}])
    res = act.ai_citation_track(
        {"prompts": ["p1"], "brand": "Acme", "brand_domains": ["acme.com"], "samples": 2},
        make_config(),
        {"dataforseo": dfs},
    )
    d = res["data"]
    assert d["engines_queried"] == ["google_aio"]
    assert d["overall"]["brand_visibility"]["value"] == 1.0


def test_google_aio_detects_competitor_in_answer_body(make_config):
    # F7: competitor names are detected in the AIO ANSWER BODY, not just citation
    # domains; and an engine with responses but no brand mention reports SoV 0.0
    # (not null), consistent with other engines.
    dfs = FakeDFSText("The best tools are Zapier and Make for automation.")
    res = act.ai_citation_track(
        {"prompts": ["best automation tools"], "brand": "Acme", "competitors": ["Zapier", "Make"], "samples": 2},
        make_config(),
        {"dataforseo": dfs},
    )
    eng = res["data"]["by_engine"][0]
    assert eng["competitor_appearances"]["Zapier"] == 2
    assert eng["competitor_appearances"]["Make"] == 2
    assert eng["share_of_voice"] == 0.0          # brand absent, but measured -> 0.0
    assert res["data"]["overall"]["share_of_voice"] == 0.0


def test_no_engine_configured_auth_missing(make_config):
    res = act.ai_citation_track({"prompts": ["p"], "brand": "Acme"}, make_config(), {})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.AUTH_MISSING


def test_missing_prompts_invalid(make_config):
    res = act.ai_citation_track({"brand": "Acme"}, make_config(), {"ai_engines": FakeEngines({"perplexity": {}})})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_missing_brand_invalid(make_config):
    res = act.ai_citation_track({"prompts": ["p"]}, make_config(), {"ai_engines": FakeEngines({"perplexity": {}})})
    assert res["ok"] is False and res["error"]["code"] == ErrorCode.INVALID_INPUT


def test_failed_samples_counted(make_config):
    class Flaky:
        def available_engines(self):
            return ["perplexity"]

        def query(self, engine, prompt):
            raise ApiError(ErrorCode.RATE_LIMITED, "boom")

    res = act.ai_citation_track(
        {"prompts": ["p1", "p2"], "brand": "Acme", "samples": 2}, make_config(), {"ai_engines": Flaky()}
    )
    d = res["data"]
    assert d["failed_samples"] == 4
    assert d["overall"]["responses"] == 0
    assert d["overall"]["brand_visibility"]["value"] == 0.0
