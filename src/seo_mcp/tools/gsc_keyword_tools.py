"""gsc_keyword_expand (roadmap Track B, Wave 1) -- the flagship discovery tool.

Architecture decision (design doc §4 B1, approved): the host LLM does the
*expansion* (it brainstorms candidate terms/clusters from the site's winning
queries -- that is the host's job and needs no in-tool LLM call); THIS tool does
the deterministic *grounding* -- it checks each candidate against the property's
own Search Console data and returns a footprint verdict + a confidence signal.

So the caller flow is: (1) call gsc_top_queries to see what already wins, (2)
brainstorm candidate terms, (3) pass them here. The tool stays pure-data and
fully mockable.

Two findings shape the design:
  * GSC anonymization hides ~75% of impressions, so "no footprint" really means
    "no *visible* footprint" -- we label it that way and treat net-new
    candidates as scored hypotheses, never facts.
  * Confidence for a zero-data term comes from SIBLING STRENGTH: if the
    candidate shares content tokens with queries that already win impressions,
    it is in our proven wheelhouse. This is the strongest free signal and needs
    no external API. (Autocomplete presence + external volume are Wave 3, gated
    on the SERP/DataForSEO clients; surfaced here as null placeholders.)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations, missing_site_error, require_client, resolve_site
from ._html import content_terms
from ._scoring import band_for

_SERVICE = "gsc"
_REMEDIATION = (
    "Configure Google auth (OAuth client + token, or a service-account key) "
    "with Search Console access. See README > Auth."
)
_ROW_LIMIT = 25000
_MAX_CANDIDATES = 300


TOOL = {
    "name": "gsc_keyword_expand",
    "description": (
        "Ground LLM-brainstormed keyword candidates against your own Search "
        "Console data to find net-new terms you have no current footprint on. "
        "YOU (the host) supply `candidates` -- brainstorm them from the site's "
        "winning queries first (call gsc_top_queries). For each candidate this "
        "returns a footprint verdict (covered / thin / none) and a confidence "
        "band from sibling-strength (does it share tokens with queries that "
        "already win impressions). 'none' means no VISIBLE footprint, not no "
        "demand (GSC hides ~75% of impressions); net-new terms are scored "
        "hypotheses, not facts. Read-only."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Candidate keyword/topic strings the host brainstormed. Required, 1-300.",
            },
            "site_url": {"type": "string", "description": "Defaults to the configured default site."},
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "Window for owned-query footprint. Defaults to 90 (wider = more footprint visible)."},
            "impressions_min": {"type": "integer", "minimum": 1, "description": "Impressions at/above which an exact match counts as 'covered'. Defaults to 10."},
        },
        "required": ["candidates"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def _norm(q: str) -> str:
    return " ".join(str(q).strip().lower().split())


def gsc_keyword_expand(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    client, error = require_client(clients, "gsc", _SERVICE, remediation=_REMEDIATION)
    if error:
        return error
    raw_candidates = arguments.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            "candidates must be a non-empty array of strings.",
            remediation="Brainstorm candidate terms (seed from gsc_top_queries) and pass them as `candidates`.",
            docs_url=DOCS_BASE + "gsc",
        )
    candidates = [_norm(c) for c in raw_candidates if str(c).strip()][:_MAX_CANDIDATES]
    if not candidates:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "candidates contained no usable strings.")

    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()

    days = int(arguments.get("days", 90))
    impressions_min = int(arguments.get("impressions_min", 10))

    today = date.today()
    start = (today - timedelta(days=days)).isoformat()
    end = today.isoformat()
    body = {
        "startDate": start,
        "endDate": end,
        "dimensions": ["query"],
        "rowLimit": _ROW_LIMIT,
        "type": "web",
        "dataState": getattr(config, "gsc_data_state", "final"),
    }
    try:
        resp = client.search_analytics(site, body)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    rows = resp.get("rows", [])
    owned: dict[str, float] = {}          # normalized owned query -> impressions
    inverted: dict[str, set[str]] = {}    # content token -> set(owned query)
    total_visible_impr = 0.0
    for r in rows:
        keys = r.get("keys") or [""]
        q = _norm(keys[0] if keys else "")
        if not q:
            continue
        impr = float(r.get("impressions", 0) or 0)
        owned[q] = owned.get(q, 0.0) + impr
        total_visible_impr += impr
        for tok in content_terms(q):
            inverted.setdefault(tok, set()).add(q)

    # First pass: per-candidate raw sibling impressions, so we can normalize.
    raw_results: list[dict[str, Any]] = []
    for cand in candidates:
        ctoks = content_terms(cand)
        # footprint
        if cand in owned:
            footprint = "covered" if owned[cand] >= impressions_min else "thin"
            footprint_impr = owned[cand]
        else:
            substring_hit = any(cand in q or q in cand for q in owned) if cand else False
            footprint = "thin" if substring_hit else "none"
            footprint_impr = 0.0
        # sibling strength: impressions of owned queries sharing >=1 token
        sib_queries: set[str] = set()
        for tok in ctoks:
            sib_queries |= inverted.get(tok, set())
        sib_queries.discard(cand)
        sibling_impr = sum(owned[q] for q in sib_queries)
        raw_results.append({
            "term": cand,
            "footprint": footprint,
            "footprint_impressions": int(footprint_impr),
            "sibling_impressions": int(sibling_impr),
            "sibling_query_count": len(sib_queries),
            "_ctoks": bool(ctoks),
        })

    max_sib = max((r["sibling_impressions"] for r in raw_results), default=0) or 1

    results: list[dict[str, Any]] = []
    for r in raw_results:
        sib_norm = r["sibling_impressions"] / max_sib
        results.append({
            "term": r["term"],
            "footprint": r["footprint"],
            "footprint_impressions": r["footprint_impressions"],
            "confidence": {
                "band": band_for(sib_norm) if r["_ctoks"] else "low",
                "sibling_impressions": r["sibling_impressions"],
                "sibling_query_count": r["sibling_query_count"],
                "autocomplete_present": None,   # Wave 3 (needs SERP client)
                "external_volume": None,        # Wave 3 (needs DataForSEO)
            },
        })

    # net_new = no ACTUAL visible footprint: footprint "none", plus "thin" terms
    # that are thin only via a substring coincidence with an owned query and have
    # zero footprint_impressions (tester F3: strong-sibling 0-impression terms are
    # often the best net-new bets and were falling out). A "thin" term with real
    # impressions stays out -- you already have some footprint there.
    net_new = [r for r in results if r["footprint"] == "none" or (r["footprint"] == "thin" and r["footprint_impressions"] == 0)]
    # surface the strongest net-new first
    net_new.sort(key=lambda r: r["confidence"]["sibling_impressions"], reverse=True)

    return ok({
        "site_url": site,
        "window": {"start": start, "end": end, "days": days},
        "owned_query_count": len(owned),
        "visible_impressions": int(total_visible_impr),
        "candidate_count": len(results),
        "net_new_count": len(net_new),
        "candidates": results,
        "net_new": net_new,
        "filters_applied": {"impressions_min": impressions_min},
        "caveats": [
            "'none' = no VISIBLE footprint. GSC anonymizes queries with low "
            "volume (~75% of impressions hidden), so a 'net-new' term may "
            "already get hidden impressions. Treat net-new as scored hypotheses.",
            "Confidence is sibling-strength only (free, owned-data). "
            "autocomplete_present and external_volume are null until the SERP / "
            "DataForSEO clients ship (Wave 3).",
            "A wider `days` window surfaces more footprint; this is read against "
            "your own property, not external volume.",
            "footprint: covered = exact match with impressions; thin = exact match "
            "below impressions_min OR a substring overlap with an owned query; none "
            "= no overlap. net_new = 'none' + 'thin' terms with zero footprint "
            "impressions (substring coincidence only), ranked by sibling strength.",
        ],
    })


TOOLS = [TOOL]
HANDLERS = {"gsc_keyword_expand": gsc_keyword_expand}
