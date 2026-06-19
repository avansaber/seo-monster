"""Offline tests for the AI answer-engine client (roadmap F4). The per-engine
_raw_* network seam is monkeypatched with canned payloads; we verify the
normalization + availability logic, not the live HTTP."""

from __future__ import annotations

import pytest

from seo_mcp.clients.ai_engines import AiEnginesClient, build_ai_engines_client
from seo_mcp.clients.errors import ApiError


def _client(engine: str, raw: dict):
    c = AiEnginesClient({engine: "key"})
    setattr(c, f"_raw_{engine}", lambda prompt: raw)
    return c


def test_perplexity_normalization():
    c = _client("perplexity", {
        "choices": [{"message": {"content": "Acme is great"}}],
        "search_results": [{"url": "https://acme.com/x", "title": "Acme"}],
    })
    out = c.query("perplexity", "best widgets?")
    assert out["answer_text"] == "Acme is great"
    assert out["citations"] == [{"url": "https://acme.com/x", "title": "Acme", "domain": "acme.com"}]


def test_openai_normalization():
    c = _client("openai", {
        "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "Use Acme.",
             "annotations": [{"type": "url_citation", "url": "https://acme.com/y", "title": "A"}]},
        ]}],
    })
    out = c.query("openai", "q")
    assert "Use Acme." in out["answer_text"]
    assert out["citations"][0]["domain"] == "acme.com"


def test_anthropic_normalization():
    c = _client("anthropic", {
        "content": [{"type": "text", "text": "Acme rocks", "citations": [{"url": "https://acme.com/z", "title": "A"}]}],
    })
    out = c.query("anthropic", "q")
    assert out["answer_text"] == "Acme rocks"
    assert out["citations"][0]["domain"] == "acme.com"


def test_gemini_normalization():
    c = _client("gemini", {
        "candidates": [{
            "content": {"parts": [{"text": "Acme"}]},
            "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://acme.com/g", "title": "A"}}]},
        }],
    })
    out = c.query("gemini", "q")
    assert out["answer_text"] == "Acme"
    assert out["citations"][0]["url"] == "https://acme.com/g"


def test_availability_drops_empty_keys():
    c = AiEnginesClient({"perplexity": "k", "openai": "", "gemini": None})
    assert c.available_engines() == ["perplexity"]
    assert c.probe() is True


def test_query_unconfigured_engine_raises():
    c = AiEnginesClient({"perplexity": "k"})
    with pytest.raises(ApiError):
        c.query("openai", "q")


def test_builder(make_config):
    assert build_ai_engines_client(make_config()) is None
    c = build_ai_engines_client(make_config(ANTHROPIC_API_KEY="k"))
    assert isinstance(c, AiEnginesClient) and c.available_engines() == ["anthropic"]
