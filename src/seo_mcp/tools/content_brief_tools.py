"""content_brief_data (roadmap Track C, Wave 2).

The data-wired backing for a content brief. The existing `content_brief` PROMPT
asked the host to eyeball competitors; this tool fetches the analyzed pages and
returns the hard evidence (heading union, median word-count FLOOR, schema types,
entity coverage) plus the 2026 GEO directives -- so the host writes the brief
prose from grounded data, not guesses (design doc §5 C2). Host-LLM split intact:
SEOMonster brings rules + evidence, the LLM brings the writing.

Competitor set: pass `competitor_urls` (e.g. the top SERP results the host can
see) for a true competitor brief. With no URLs, it falls back to YOUR OWN pages
that rank for the query (via GSC) -- useful for refresh-vs-new, but explicitly
NOT the full SERP (a real SERP source lands in Wave 3). Free; read-only; does not
guarantee a ranking.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import date, timedelta
from typing import Any, Mapping

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations, require_client, resolve_site
from ._html import content_entities, is_chrome_heading, parse_content

_SERVICE = "content"
_HTTP_REMEDIATION = "No setup needed; the HTTP client is built in."
_MAX_PAGES = 8
_ROW_LIMIT = 1000

_GEO_DIRECTIVES = [
    "Lead each section with a direct ~20-40 word answer (answer-first), then expand.",
    "Include concrete statistics and at least one quotation -- both measurably lift AI citation (GEO paper, KDD 2024).",
    "Cite named sources inline with outbound links to authorities.",
    "Write self-contained ~200-500 token chunks with question-style headings; repeat entity names (avoid pronouns) for RAG retrieval.",
    "Do NOT keyword-stuff (it measurably hurts).",
    "Add FAQPage JSON-LD for AI extraction only -- Google dropped FAQ rich results 2026-05-07; do not sell it as a Google snippet play.",
]

_VALIDATION_RULES = [
    "Word count is a FLOOR (top-page median), not a target -- length is a container, not a strategy.",
    "Single H1 containing the primary query; >=5 H2 sections.",
    "Cover the primary query plus the related entities listed; reference >=70% in the outline.",
    "Name exactly one primary schema.org type.",
    "Include >=1 internal-link target and >=1 competitor gap (an angle the analyzed pages miss).",
]


TOOL = {
    "name": "content_brief_data",
    "description": (
        "Gather the hard evidence for a content brief: fetch the competitor "
        "pages and return their heading union, median word count (a FLOOR, not a "
        "target), schema types, and entity coverage, plus the 2026 GEO writing "
        "directives and validation rules. Pass competitor_urls (e.g. the top "
        "SERP results) for a true brief; with none it falls back to your own "
        "ranking pages via GSC (refresh-vs-new, NOT the full SERP). The host "
        "writes the brief prose from this; SEOMonster supplies rules + evidence. "
        "Read-only; no ranking guarantee. Backs the content_brief prompt."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "target_query": {"type": "string", "description": "The primary query the content should win. Required."},
            "topic": {"type": "string", "description": "Optional working title / topic."},
            "competitor_urls": {"type": "array", "items": {"type": "string"}, "description": "URLs to analyze (e.g. top SERP results). Up to 8."},
            "site_url": {"type": "string", "description": "GSC property for the own-pages fallback. Defaults to the configured default site."},
            "days": {"type": "integer", "minimum": 1, "maximum": 240, "description": "GSC window for the own-pages fallback. Default 28."},
        },
        "required": ["target_query"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def _own_ranking_pages(clients: Mapping[str, Any], config: Any, site: str, query: str, days: int) -> list[str]:
    try:
        gsc = clients.get("gsc")
    except Exception:
        gsc = None
    if gsc is None:
        return []
    today = date.today()
    body = {
        "startDate": (today - timedelta(days=days)).isoformat(),
        "endDate": today.isoformat(),
        "dimensions": ["page"],
        "rowLimit": _ROW_LIMIT,
        "type": "web",
        "dataState": getattr(config, "gsc_data_state", "final"),
        "dimensionFilterGroups": [
            {"filters": [{"dimension": "query", "operator": "equals", "expression": query}]}
        ],
    }
    try:
        resp = gsc.search_analytics(site, body)
    except ApiError:
        return []
    rows = sorted(resp.get("rows", []), key=lambda r: r.get("clicks", 0) or 0, reverse=True)
    urls: list[str] = []
    for r in rows:
        keys = r.get("keys") or []
        if keys:
            urls.append(keys[0])
        if len(urls) >= _MAX_PAGES:
            break
    return urls


def content_brief_data(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    http, error = require_client(clients, "http", _SERVICE, remediation=_HTTP_REMEDIATION)
    if error:
        return error
    target_query = arguments.get("target_query")
    if not target_query:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "target_query is required.", docs_url=DOCS_BASE + "gsc")

    competitor_urls = arguments.get("competitor_urls") or []
    source = "competitor_urls"
    if not competitor_urls:
        site = resolve_site(arguments, config)
        if not site:
            return err(
                ErrorCode.INVALID_INPUT,
                _SERVICE,
                "Provide competitor_urls, or configure a GSC default site for the own-pages fallback.",
                remediation="Pass competitor_urls (e.g. the top SERP results), or set a GSC default site.",
                docs_url=DOCS_BASE + "gsc",
            )
        competitor_urls = _own_ranking_pages(clients, config, site, str(target_query), int(arguments.get("days", 28)))
        source = "own_ranking_pages"
        if not competitor_urls:
            return ok(_empty_brief(target_query, arguments.get("topic"), source,
                                   "No competitor_urls given and no own ranking pages found for the query."))

    analyzed: list[dict[str, Any]] = []
    doc_freq: Counter = Counter()
    word_counts: list[int] = []
    heading_union: list[str] = []
    seen_headings: set[str] = set()
    schema_types: set[str] = set()

    for url in competitor_urls[:_MAX_PAGES]:
        try:
            resp = http.fetch(url)
        except ApiError as exc:
            analyzed.append({"url": url, "error": exc.message})
            continue
        if not (200 <= resp.status < 300):
            analyzed.append({"url": url, "error": f"HTTP {resp.status}"})
            continue
        p = parse_content(resp.body_text)
        # F1: drop nav/CTA/subscribe/footer headings; keep real subtopics.
        h2h3 = [h["text"] for h in p.headings if h["level"] in (2, 3) and h["text"] and not is_chrome_heading(h["text"])]
        for h in h2h3:
            key = h.strip().lower()
            if key and key not in seen_headings:
                seen_headings.add(key)
                heading_union.append(h.strip())
        wc = p.word_count
        word_counts.append(wc)
        for t in p.jsonld_types():
            schema_types.add(t)
        doc_freq.update(content_entities(p.text))  # F2: stricter entity tokens
        analyzed.append({
            "url": resp.final_url,
            "title": (p.title or "").strip() or None,
            "word_count": wc,
            "h2_h3_count": len(h2h3),
            "schema_types": p.jsonld_types(),
        })

    median_words = int(statistics.median(word_counts)) if word_counts else 0
    # entities to cover: terms used by the most competitor pages (document freq).
    entities = [t for t, _ in doc_freq.most_common(20)]

    caveats = [
        "Target word count is the top-page MEDIAN -- treat it as a floor, not a "
        "target; length is a container, not a strategy.",
        "Entities are document-frequency terms across the analyzed pages (lexical, "
        "not semantic) -- the host should refine which are genuine entities.",
    ]
    if source == "own_ranking_pages":
        caveats.insert(0,
            "Analyzed YOUR OWN ranking pages (no competitor_urls given), so this "
            "is a refresh/own-coverage view, NOT the full SERP. Pass competitor_urls "
            "(top SERP results) for a true competitor brief; a SERP source lands in Wave 3.")

    return ok({
        "target_query": target_query,
        "topic": arguments.get("topic"),
        "source": source,
        "analyzed_pages": analyzed,
        "evidence": {
            "target_word_count_floor": median_words,
            "heading_union": heading_union,
            "schema_types_seen": sorted(schema_types),
            "entities_to_cover": entities,
        },
        "geo_directives": _GEO_DIRECTIVES,
        "validation_rules": _VALIDATION_RULES,
        "caveats": caveats,
    })


def _empty_brief(query, topic, source, reason) -> dict[str, Any]:
    return {
        "target_query": query,
        "topic": topic,
        "source": source,
        "analyzed_pages": [],
        "evidence": {"target_word_count_floor": 0, "heading_union": [], "schema_types_seen": [], "entities_to_cover": []},
        "geo_directives": _GEO_DIRECTIVES,
        "validation_rules": _VALIDATION_RULES,
        "note": reason,
        "caveats": ["No pages analyzed; pass competitor_urls to generate evidence."],
    }


TOOLS = [TOOL]
HANDLERS = {"content_brief_data": content_brief_data}
