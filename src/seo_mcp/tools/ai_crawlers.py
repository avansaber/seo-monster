"""Curated AI crawler / fetcher user-agent reference (roadmap F5).

Before this module the codebase had **no** enumerated AI-crawler identifiers
(confirmed by the design-phase audit). This is the single source of truth used
by ``ai_referral_overview`` (crawl-coverage section) and reusable by the robots
tools. Sourced from ``ai.robots.txt`` + Cloudflare's verified-bot reference
(2026-04). The space churns -- treat this as a maintained list, not a closed
set.

Each entry:
  token         - the ``User-agent`` token to match in robots.txt (RFC 9309
                  case-insensitive prefix) and to probe with.
  operator      - the company running it.
  category      - "training" | "search" | "user-fetch" | "training-control".
  honors_robots - True / False / None(unknown). User-initiated fetchers and a
                  few known bad actors ignore robots.txt regardless of
                  directives; enforce those at the WAF, not robots.txt.
  note          - short human note.

Important nuance baked into the categories: Google-Extended and
Applebot-Extended are *control tokens*, NOT separate crawlers -- blocking them
does not remove you from Google Search or Apple results and is purely an
AI-training opt-out. ``ai_referral_overview`` surfaces that so users don't
block themselves out of search by mistake.
"""

from __future__ import annotations

AI_CRAWLERS: list[dict] = [
    # OpenAI
    {"token": "GPTBot", "operator": "OpenAI", "category": "training", "honors_robots": True,
     "note": "Trains OpenAI models. Fetches but does not execute JS."},
    {"token": "OAI-SearchBot", "operator": "OpenAI", "category": "search", "honors_robots": True,
     "note": "Indexes for ChatGPT search citations."},
    {"token": "ChatGPT-User", "operator": "OpenAI", "category": "user-fetch", "honors_robots": False,
     "note": "User-initiated live fetch; ignores robots.txt."},
    # Anthropic
    {"token": "ClaudeBot", "operator": "Anthropic", "category": "training", "honors_robots": True,
     "note": "Trains Claude models."},
    {"token": "Claude-SearchBot", "operator": "Anthropic", "category": "search", "honors_robots": True,
     "note": "Indexes for Claude search citations."},
    {"token": "Claude-User", "operator": "Anthropic", "category": "user-fetch", "honors_robots": True,
     "note": "User-initiated fetch; honors robots.txt (a differentiator vs peers)."},
    # Perplexity
    {"token": "PerplexityBot", "operator": "Perplexity", "category": "search", "honors_robots": True,
     "note": "Indexes for Perplexity answers; stealth-crawl concerns reported (2025)."},
    {"token": "Perplexity-User", "operator": "Perplexity", "category": "user-fetch", "honors_robots": False,
     "note": "User-initiated fetch; ignores robots.txt."},
    # Google (control tokens are NOT crawlers; blocking them does not affect Search)
    {"token": "Google-Extended", "operator": "Google", "category": "training-control", "honors_robots": True,
     "note": "Control token for Gemini training/grounding; not a crawler. Blocking does NOT affect Google Search."},
    {"token": "GoogleOther", "operator": "Google", "category": "training", "honors_robots": True,
     "note": "Non-Search Google fetches incl. AI R&D."},
    # Apple
    {"token": "Applebot", "operator": "Apple", "category": "search", "honors_robots": True,
     "note": "Siri / Spotlight; renders JS."},
    {"token": "Applebot-Extended", "operator": "Apple", "category": "training-control", "honors_robots": True,
     "note": "Control token for Apple AI training; not a crawler."},
    # Others
    {"token": "Amazonbot", "operator": "Amazon", "category": "search", "honors_robots": True,
     "note": "Alexa / Amazon assistant indexing."},
    {"token": "Meta-ExternalAgent", "operator": "Meta", "category": "training", "honors_robots": True,
     "note": "Trains Meta AI."},
    {"token": "Meta-ExternalFetcher", "operator": "Meta", "category": "user-fetch", "honors_robots": None,
     "note": "Assistant link-fetch."},
    {"token": "Bytespider", "operator": "ByteDance", "category": "training", "honors_robots": False,
     "note": "Ignores robots.txt; block at the WAF if undesired."},
    {"token": "CCBot", "operator": "Common Crawl", "category": "training", "honors_robots": True,
     "note": "Common Crawl corpus feeds many LLMs."},
    {"token": "cohere-ai", "operator": "Cohere", "category": "training", "honors_robots": True,
     "note": "Cohere training / grounding."},
    {"token": "DuckAssistBot", "operator": "DuckDuckGo", "category": "search", "honors_robots": True,
     "note": "DuckDuckGo AI assist."},
    {"token": "Diffbot", "operator": "Diffbot", "category": "training", "honors_robots": True,
     "note": "Knowledge-graph extraction resold to AI firms."},
]


def crawler_tokens() -> list[str]:
    """Just the UA tokens, e.g. for building robots probes."""
    return [c["token"] for c in AI_CRAWLERS]


def crawler_by_token() -> dict[str, dict]:
    """Lookup map token(lowercased) -> entry, for annotating verdicts."""
    return {c["token"].lower(): c for c in AI_CRAWLERS}
