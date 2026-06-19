"""Tests for the curated AI-crawler reference (roadmap F5)."""

from __future__ import annotations

from seo_mcp.tools.ai_crawlers import AI_CRAWLERS, crawler_by_token, crawler_tokens

_VALID_CATEGORIES = {"training", "search", "user-fetch", "training-control"}


def test_entries_well_formed():
    for c in AI_CRAWLERS:
        assert set(c) >= {"token", "operator", "category", "honors_robots", "note"}
        assert c["category"] in _VALID_CATEGORIES
        assert c["honors_robots"] in (True, False, None)
        assert c["token"] and c["operator"]


def test_tokens_unique():
    tokens = crawler_tokens()
    assert len(tokens) == len(set(t.lower() for t in tokens))


def test_known_crawlers_present():
    tokens = {t.lower() for t in crawler_tokens()}
    for expected in ("gptbot", "claudebot", "perplexitybot", "oai-searchbot", "bytespider"):
        assert expected in tokens


def test_control_tokens_flagged_not_as_crawlers():
    by = crawler_by_token()
    assert by["google-extended"]["category"] == "training-control"
    assert by["applebot-extended"]["category"] == "training-control"


def test_user_fetchers_robots_behavior():
    by = crawler_by_token()
    # The documented differentiator: Claude-User honors robots, the others don't.
    assert by["claude-user"]["honors_robots"] is True
    assert by["chatgpt-user"]["honors_robots"] is False
    assert by["perplexity-user"]["honors_robots"] is False
