"""onpage_serp_gap (roadmap Track D, Wave 3).

What entities, headings and schema the top SERP results have that a target page
lacks -> concrete on-page actions. FREE when the caller supplies competitor_urls
(fetch + stdlib parse + existing schema extraction); optional SERP auto-fetch via
DataForSEO when only a query is given. When the SERP is auto-fetched it also
returns the winnability signals that live in the SERP: composition / zero-click
risk (Score B) and -- if Open PageRank is configured -- competitor domain
authority (the backlink tier). 2026 framing: surface INFORMATION GAIN, not just
parity (matching competitors scores low; original data/expertise wins).
"""

from __future__ import annotations

import statistics
from typing import Any, Mapping
from urllib.parse import urlparse

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations, require_client
from ._html import content_entities, content_terms, is_chrome_heading, parse_content

_SERVICE = "technical"
_HTTP_REMEDIATION = "No setup needed; the HTTP client is built in."
_MAX_COMPETITORS = 6
_UGC_DOMAINS = ("reddit.com", "quora.com", "stackexchange.com", "stackoverflow.com",
                "medium.com", "youtube.com", "pinterest.com", "facebook.com")


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _registrable(host: str) -> str:
    """Strip a leading ``www.`` so Open PageRank resolves a score (tester F6:
    every www-prefixed host returned page_rank null, the bare domain resolved)."""
    h = (host or "").lower()
    return h[4:] if h.startswith("www.") else h


def _fetch_parsed(http: Any, url: str):
    resp = http.fetch(url)
    if not (200 <= resp.status < 300):
        raise ApiError(ErrorCode.UPSTREAM_ERROR, f"Fetch of {url!r} returned HTTP {resp.status}.", details={"status": resp.status})
    return resp, parse_content(resp.body_text)


TOOL = {
    "name": "onpage_serp_gap",
    "description": (
        "Find the headings, entities and schema the top SERP results have that a "
        "target page lacks, and turn them into on-page actions. Pass "
        "competitor_urls (free), or a query with DataForSEO configured to "
        "auto-fetch the SERP. When the SERP is auto-fetched, also returns "
        "winnability signals (AI-Overview presence, UGC/forum dominance = "
        "zero-click risk) and, with Open PageRank, competitor domain authority. "
        "Surfaces information-gain (add something novel), not just parity. "
        "Read-only; does not guarantee a ranking."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "target_url": {"type": "string", "description": "The page to analyze. Required."},
            "query": {"type": "string", "description": "Query to auto-fetch the SERP for (needs DataForSEO) when competitor_urls is omitted."},
            "competitor_urls": {"type": "array", "items": {"type": "string"}, "description": "Competitor URLs to compare against. Up to 6."},
            "max_competitors": {"type": "integer", "minimum": 1, "maximum": _MAX_COMPETITORS, "description": "Cap competitors analyzed. Default 5."},
        },
        "required": ["target_url"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def onpage_serp_gap(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    http, error = require_client(clients, "http", _SERVICE, remediation=_HTTP_REMEDIATION)
    if error:
        return error
    target_url = arguments.get("target_url")
    if not target_url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "target_url is required.", docs_url=DOCS_BASE + "technical")

    # fetch target
    try:
        _t_resp, target = _fetch_parsed(http, target_url)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    # competitor source
    competitor_urls = list(arguments.get("competitor_urls") or [])
    query = arguments.get("query")
    serp_composition: dict[str, Any] | None = None
    source = "provided"
    if not competitor_urls:
        if not query:
            return err(
                ErrorCode.INVALID_INPUT,
                _SERVICE,
                "Provide competitor_urls, or a query with DataForSEO configured to auto-fetch the SERP.",
                docs_url=DOCS_BASE + "technical",
            )
        try:
            dfs = clients.get("dataforseo")
        except Exception:
            dfs = None
        if dfs is None:
            return err(
                ErrorCode.AUTH_MISSING,
                "dataforseo",
                "SERP auto-fetch needs DataForSEO. Pass competitor_urls instead, or configure DataForSEO.",
                remediation="Set DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD, or pass competitor_urls.",
                docs_url=DOCS_BASE + "configuration",
            )
        try:
            serp = dfs.serp(str(query))
        except ApiError as exc:
            return exc.to_envelope("dataforseo")
        tgt_domain = _domain(target_url)
        competitor_urls = [o["url"] for o in serp.get("organic", []) if o.get("url") and _domain(o["url"]) != tgt_domain]
        source = "dataforseo_serp"
        organic_domains = [o.get("domain") or _domain(o.get("url") or "") for o in serp.get("organic", [])]
        serp_composition = {
            "ai_overview_present": serp.get("ai_overview_present"),
            "result_types": serp.get("result_types"),
            "ugc_results": sum(1 for d in organic_domains if any(u in (d or "") for u in _UGC_DOMAINS)),
            "organic_count": len(serp.get("organic", [])),
            "zero_click_risk": bool(serp.get("ai_overview_present")) or any(any(u in (d or "") for u in _UGC_DOMAINS) for d in organic_domains),
        }

    competitor_urls = competitor_urls[: int(arguments.get("max_competitors", 5))]
    if not competitor_urls:
        return ok(_empty(target_url, source, "No competitor URLs to compare against."))

    # parse competitors
    analyzed: list[dict[str, Any]] = []
    comp_parsed: list[Any] = []
    comp_domains: list[str] = []
    for url in competitor_urls[:_MAX_COMPETITORS]:
        try:
            resp, p = _fetch_parsed(http, url)
        except ApiError as exc:
            analyzed.append({"url": url, "error": exc.message})
            continue
        comp_parsed.append(p)
        comp_domains.append(_domain(resp.final_url))
        analyzed.append({"url": resp.final_url, "word_count": p.word_count, "h2_h3": sum(1 for h in p.headings if h["level"] in (2, 3)), "schema_types": p.jsonld_types()})

    if not comp_parsed:
        return ok(_empty(target_url, source, "Could not fetch any competitor pages."))

    target_terms = content_terms(target.text)
    target_headings_terms = {t for h in target.headings for t in content_terms(h["text"])}
    target_schema = {s.lower() for s in target.jsonld_types()}

    # heading gaps: competitor h2/h3 whose terms the target doesn't cover.
    heading_gaps: list[str] = []
    seen_h: set[str] = set()
    for p in comp_parsed:
        for h in p.headings:
            if h["level"] not in (2, 3) or not h["text"]:
                continue
            if is_chrome_heading(h["text"]):   # F1: drop nav/CTA/subscribe/tagline
                continue
            hterms = content_terms(h["text"])
            if not hterms:
                continue
            if not (hterms & target_terms) and not (hterms & target_headings_terms):
                key = h["text"].strip().lower()
                if key not in seen_h:
                    seen_h.add(key)
                    heading_gaps.append(h["text"].strip())

    # schema gaps: types on >=2 competitors but not on target.
    schema_freq: dict[str, int] = {}
    for p in comp_parsed:
        for s in set(p.jsonld_types()):
            schema_freq[s] = schema_freq.get(s, 0) + 1
    schema_gaps = sorted(s for s, n in schema_freq.items() if n >= 2 and s.lower() not in target_schema)

    # entity gaps: entities used by >=2 competitor pages but absent from target.
    # F2: content_entities applies a >=4-char + broader-stopword filter so we
    # don't surface "cover the entity: good".
    term_doc_freq: dict[str, int] = {}
    for p in comp_parsed:
        for t in content_entities(p.text):
            term_doc_freq[t] = term_doc_freq.get(t, 0) + 1
    entity_gaps = [t for t, n in sorted(term_doc_freq.items(), key=lambda kv: kv[1], reverse=True)
                   if n >= 2 and t not in target_terms][:20]

    comp_words = [p.word_count for p in comp_parsed]
    median_words = int(statistics.median(comp_words)) if comp_words else 0

    # winnability backlink tier: competitor domain authority via Open PageRank.
    competitor_authority = None
    try:
        opr = clients.get("openpagerank")
    except Exception:
        opr = None
    if opr is not None and comp_domains:
        # F6: query Open PageRank with the registrable domain (strip www.), which
        # is the form it scores; www-prefixed hosts returned null.
        reg_domains = list(dict.fromkeys(_registrable(d) for d in comp_domains))
        try:
            ranks = opr.domain_rank(reg_domains)
            competitor_authority = [{"domain": d, "page_rank": ranks.get(d)} for d in reg_domains]
        except ApiError:
            competitor_authority = None

    actions: list[str] = []
    for h in heading_gaps[:10]:
        actions.append(f"Add a section covering: '{h}'.")
    if schema_gaps:
        actions.append(f"Add schema.org type(s): {', '.join(schema_gaps)}.")
    if entity_gaps:
        actions.append(f"Cover missing entities/terms: {', '.join(entity_gaps[:10])}.")
    if median_words and target.word_count < median_words * 0.7:
        actions.append(f"Content is thin ({target.word_count}w) vs competitor median {median_words}w; expand the substantive sections.")
    actions.append("Add information gain: original data, first-hand testing, or expertise the competitors lack -- parity alone scores low for AI Overviews (aim ~150-word self-contained passages).")

    return ok({
        "target_url": target_url,
        "source": source,
        "target": {"word_count": target.word_count, "schema_types": target.jsonld_types(), "h2_h3": sum(1 for h in target.headings if h["level"] in (2, 3))},
        "competitors_analyzed": analyzed,
        "gaps": {
            "headings": heading_gaps,
            "schema_types": schema_gaps,
            "entities": entity_gaps,
            "word_count": {"target": target.word_count, "competitor_median": median_words},
        },
        "serp_composition": serp_composition,
        "competitor_authority": competitor_authority,
        "actions": actions,
        "caveats": [
            "Gaps are lexical (term/heading overlap), not semantic -- review before "
            "acting. Entities are document-frequency terms, not verified entities.",
            "Match-the-competitors is table stakes; the ranking lever in 2026 is "
            "information gain (something novel), which a gap analysis can't supply.",
        ] + ([
            "serp_composition flags zero-click risk (AI Overview / UGC dominance): "
            "a winnable SERP can still be not-worth-winning."
        ] if serp_composition else []),
    })


def _empty(target_url, source, reason) -> dict[str, Any]:
    return {
        "target_url": target_url,
        "source": source,
        "competitors_analyzed": [],
        "gaps": {"headings": [], "schema_types": [], "entities": [], "word_count": {}},
        "actions": [],
        "note": reason,
        "caveats": ["No competitors analyzed."],
    }


TOOLS = [TOOL]
HANDLERS = {"onpage_serp_gap": onpage_serp_gap}
