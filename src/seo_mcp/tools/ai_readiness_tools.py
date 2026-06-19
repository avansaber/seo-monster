"""ai_citation_readiness (roadmap Track A, Wave 1).

Is a page structured to be *extracted and cited* by LLM answer engines
(ChatGPT, Perplexity, Claude, Google AIO)? This is the most defensible of the
three AI tools because it scores on-page signals with documented causal support
(the GEO paper, KDD 2024, arXiv:2311.09735) and is honest about what is NOT a
driver.

Evidence discipline (design doc §3 A1):
  * Tier A (scored): server-rendered/extractable content, statistics,
    quotations, inline cited sources, no keyword-stuffing.
  * Tier B (reported, not scored): structure/listicle, chunking, freshness hint.
  * Tier C (informational, weight 0): schema.org, FAQ -- the 2026 evidence says
    these are neutral-to-negative for AI citation, so we report them and refuse
    to score them, unlike tools that sell schema as an AI lever.

Headline signal: ``rendered_blind`` -- GPTBot / OAI-SearchBot / ClaudeBot /
PerplexityBot fetch but do NOT execute JS, so a client-side-only SPA is a blank
page to them. This is the single highest-value, cleanest binary check.

Free, no LLM, no paid API: one HTTP GET + stdlib parse. Fully mockable.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import ANNOT_READ, require_client
from ._html import parse_content
from ._scoring import TIER_A, banded_score

_SERVICE = "ai"
_REMEDIATION = "No setup needed; the HTTP client is built in."

# Tier-A weights (relative magnitudes anchored to the GEO paper's measured
# visibility lifts; normalized to sum 1.0). Echoed back under readiness.weights.
_WEIGHTS = {
    "extractable": 0.25,
    "statistics": 0.20,
    "quotations": 0.20,
    "cited_sources": 0.15,
    "no_keyword_stuffing": 0.20,
}

# A single content word exceeding this share of all content words reads as
# keyword stuffing (GEO paper: stuffing HURTS, -8%).
_STUFFING_RATIO = 0.05
# Word-count floor below which a page (with scripts) is treated as render-blind.
_RENDER_BLIND_WORDS = 200
_RENDER_BLIND_SCRIPTS = 3


TOOL = {
    "name": "ai_citation_readiness",
    "description": (
        "Score whether a page is structured to be extracted and cited by LLM "
        "answer engines (ChatGPT, Perplexity, Claude, Google AI Overviews). "
        "Leads with a render-blindness check (AI crawlers do not run JS, so a "
        "client-rendered SPA is invisible to them), then scores evidence-backed "
        "signals: statistics, quotations, cited sources, no keyword stuffing. "
        "Reports schema.org / FAQ as informational only -- the 2026 evidence "
        "does NOT support them as AI-citation drivers, so they are not scored. "
        "Read-only HTTP GET; does not guarantee a citation."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL to assess."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def ai_citation_readiness(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    client, error = require_client(clients, "http", _SERVICE, remediation=_REMEDIATION)
    if error:
        return error
    url = arguments.get("url")
    if not url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url is required.", docs_url=DOCS_BASE + "technical")
    try:
        resp = client.fetch(url)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    if not (200 <= resp.status < 300):
        return err(
            ErrorCode.UPSTREAM_ERROR,
            _SERVICE,
            f"Fetch of {url!r} returned HTTP {resp.status}.",
            details={"status": resp.status, "final_url": resp.final_url},
        )

    p = parse_content(resp.body_text)
    words = p.word_count
    h2plus = sum(c for lvl, c in p.heading_levels().items() if lvl >= 2)

    # Headline: render-blind heuristic. Little server-rendered text but plenty
    # of scripts => an AI crawler that does not run JS sees ~nothing.
    rendered_blind = words < _RENDER_BLIND_WORDS and p.script_count >= _RENDER_BLIND_SCRIPTS

    # Tier-A components, each normalized to 0..1.
    extractable = 0.0 if rendered_blind else (0.6 + 0.4 * min(1.0, h2plus / 3.0))
    # stats density: ~2 numbers / 100 words saturates.
    stats_per_100w = (p.number_count / (words / 100.0)) if words else 0.0
    statistics = min(1.0, stats_per_100w / 2.0)
    quotations = min(1.0, (p.blockquotes + p.inline_quotes) / 3.0)
    cited_sources = min(1.0, p.outbound_links(resp.final_url) / 5.0)
    top_word, top_ratio = p.top_token_ratio()
    stuffed = top_ratio > _STUFFING_RATIO
    no_keyword_stuffing = 0.2 if stuffed else 1.0

    components = {
        "extractable": extractable,
        "statistics": statistics,
        "quotations": quotations,
        "cited_sources": cited_sources,
        "no_keyword_stuffing": no_keyword_stuffing,
    }

    caveats = [
        "Scores on-page signals with documented causal support (GEO paper, "
        "KDD 2024). It does not measure off-page brand mentions -- the strongest "
        "known correlate of AI visibility -- which an on-page tool cannot see.",
        "The render-blindness check is a heuristic (low server-rendered word "
        "count + multiple scripts); confirm with a 'view source' if it flags.",
    ]
    if rendered_blind:
        caveats.insert(
            0,
            "RENDER-BLIND: little server-rendered text was found. AI crawlers "
            "(GPTBot, OAI-SearchBot, ClaudeBot, PerplexityBot) do not execute "
            "JavaScript, so this page may be near-empty to them. Server-render "
            "or pre-render the main content. This dominates everything else.",
        )

    jsonld = p.jsonld_types()
    faq_detected = any(t.lower() in ("faqpage", "qapage") for t in jsonld)

    return ok({
        "url": url,
        "final_url": resp.final_url,
        "status": resp.status,
        "rendered_blind": rendered_blind,
        "word_count": words,
        "readiness": banded_score(components, _WEIGHTS, evidence_tier=TIER_A, caveats=caveats),
        "secondary": {  # Tier B: reported, not scored
            "evidence_tier": "B",
            "heading_levels": p.heading_levels(),
            "list_items": p.list_items,
            "tables": p.tables,
            "paragraphs": p.paragraphs,
            "has_time_markup": p.time_tags > 0,
            "note": "Structure / freshness signals: correlational, not weighted.",
        },
        "informational": {  # Tier C: NOT an AI-citation driver in 2026 evidence
            "evidence_tier": "C",
            "schema_types": jsonld,
            "faq_detected": faq_detected,
            "note": (
                "schema.org and FAQ are reported but NOT scored as AI-citation "
                "drivers: 2026 controlled evidence finds them neutral-to-negative "
                "for AI Overviews. FAQ rich results were dropped by Google "
                "2026-05-07; keep FAQPage only for AI extraction, not Google "
                "snippets. llms.txt is not consumed by any engine."
            ),
        },
        "stuffing": {"top_term": top_word, "top_term_ratio": round(top_ratio, 4), "flagged": stuffed},
    })


TOOLS = [TOOL]
HANDLERS = {"ai_citation_readiness": ai_citation_readiness}
