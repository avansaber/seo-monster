"""keyword_universe (roadmap Track B, Wave 3).

The optional external-data tool. Its real justification is COMPETITOR KEYWORD
GAP (Domain Intersection) -- the one thing neither GSC nor autocomplete nor any
Google product can produce -- which is DataForSEO-only. Search volume is a
pluggable provider chain (design doc §0.2 / §4 B3): DataForSEO -> Google Ads ->
none. Volume is a degraded signal in 2026 (bucketing + clickstream noise +
zero-click erosion), so it is a directional tiebreaker, never a gate.

Honest provider handling (P6, catalog-never-lies): if no provider is configured
the tool returns AUTH_MISSING with the setup remediation; if only Google Ads is
configured, volume is reported as pending the adwords-scope live consent (the
running OAuth token lacks it) -- a tester live-setup step, not a dev failure.
"""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations

_SERVICE = "discovery"
_MAX_COMPETITORS = 5

# Near-duplicate collapse for the competitor gap (tester F4): a limit:25 gap was
# flooded with one-concept morphological variants ("advertisement ethos /
# advertisements ethos / advertising ethos / ad using ethos ..."). We collapse by
# a stemmed token-set signature BEFORE applying the limit, keeping the highest-
# volume representative per concept. Light suffix stemmer, no NLTK.
# Derivational suffixes (plurals handled separately so makes->make, not mak).
_GAP_SUFFIXES = ("ements", "ement", "ations", "ation", "izations", "ization", "ings", "ing", "edly", "ed", "ly")
_GAP_CONNECTIVES = frozenset(
    "using use uses that with for the a an to of in on and or via by your you it".split()
)


def _stem(word: str) -> str:
    w = word.lower()
    for suf in _GAP_SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"                     # companies -> company
    if w.endswith("es") and len(w) > 3:
        stem = w[:-2]
        # strip "es" only after a sibilant (boxes->box, watches->watch); else
        # strip just the "s" (makes->make, likes->like) so it merges with the
        # singular.
        if stem[-1:] in ("s", "x", "z") or stem[-2:] in ("ch", "sh"):
            return stem
        return w[:-1]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 2:
        return w[:-1]                           # ads -> ad, tools -> tool
    return w


def _gap_signature(keyword: str) -> frozenset[str]:
    toks = [_stem(t) for t in re.findall(r"[a-z0-9]+", keyword.lower()) if t not in _GAP_CONNECTIVES]
    return frozenset(toks) or frozenset({keyword.lower()})


def _collapse_near_dupes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[frozenset[str], dict[str, Any]] = {}
    for row in rows:
        sig = _gap_signature(row.get("keyword") or "")
        cur = best.get(sig)
        if cur is None or (row.get("search_volume") or 0) > (cur.get("search_volume") or 0):
            best[sig] = row
    return list(best.values())
_REMEDIATION = (
    "Configure DataForSEO (DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD) for competitor "
    "gap + volume, or Google Ads (GOOGLE_ADS_DEVELOPER_TOKEN + GOOGLE_ADS_CUSTOMER_ID) "
    "for volume only. See README > Configuration."
)


def _domain(value: str) -> str:
    v = str(value).strip()
    if v.startswith("sc-domain:"):
        return v.split(":", 1)[1]
    p = urlparse(v if "//" in v else f"//{v}")
    return (p.netloc or p.path).lower().lstrip("www.") or v.lower()


TOOL = {
    "name": "keyword_universe",
    "description": (
        "External keyword data (optional, paid). Its core value is the COMPETITOR "
        "keyword GAP: keywords competitors rank for that you don't (DataForSEO "
        "Domain Intersection; no Google equivalent). Optionally returns search "
        "volume/difficulty/intent for a keyword list via a provider chain "
        "(DataForSEO, else Google Ads volume-only). Requires DataForSEO and/or "
        "Google Ads to be configured. External volume is a degraded directional "
        "signal in 2026 -- never let a low/missing volume drop a high-intent term."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "target_domain": {"type": "string", "description": "Your domain (for the competitor gap), e.g. 'example.com' or 'sc-domain:example.com'."},
            "competitors": {"type": "array", "items": {"type": "string"}, "description": "Competitor domains for the gap. Up to 5."},
            "keywords": {"type": "array", "items": {"type": "string"}, "description": "Keywords to look up volume/difficulty/intent for."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Max gap keywords to return. Default 100."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True, open_world=True),
}


def keyword_universe(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    try:
        dfs = clients.get("dataforseo")
    except Exception:
        dfs = None
    gads_configured = bool(getattr(config, "google_ads_developer_token", None) and getattr(config, "google_ads_customer_id", None))

    if dfs is None and not gads_configured:
        return err(ErrorCode.AUTH_MISSING, _SERVICE, "No keyword-data provider configured.", remediation=_REMEDIATION, docs_url=DOCS_BASE + "configuration")

    target_domain = arguments.get("target_domain")
    competitors = arguments.get("competitors") or []
    keywords = arguments.get("keywords") or []
    limit = int(arguments.get("limit", 100))

    if not keywords and not (competitors and target_domain):
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            "Provide keywords (for volume) and/or target_domain + competitors (for the gap).",
            docs_url=DOCS_BASE + "gsc",
        )

    providers = {"competitor_gap": None, "volume": None}
    competitor_gap: list[dict[str, Any]] | None = None
    volume: list[dict[str, Any]] | None = None
    notes: list[str] = []

    # Competitor gap -- DataForSEO only.
    if competitors and target_domain:
        if dfs is not None:
            try:
                tgt = _domain(target_domain)
                target_kws = {k["keyword"] for k in dfs.ranked_keywords(tgt) if k.get("keyword")}
                gap_map: dict[str, dict[str, Any]] = {}
                for comp in competitors[:_MAX_COMPETITORS]:
                    cd = _domain(comp)
                    for k in dfs.ranked_keywords(cd):
                        kw = k.get("keyword")
                        if not kw or kw in target_kws:
                            continue
                        cur = gap_map.get(kw)
                        if cur is None or (k.get("search_volume") or 0) > (cur.get("search_volume") or 0):
                            gap_map[kw] = {"keyword": kw, "search_volume": k.get("search_volume"), "competitor": cd, "competitor_position": k.get("position")}
                deduped = _collapse_near_dupes(list(gap_map.values()))
                competitor_gap = sorted(deduped, key=lambda r: r.get("search_volume") or 0, reverse=True)[:limit]
                providers["competitor_gap"] = "dataforseo"
            except ApiError as exc:
                return exc.to_envelope(_SERVICE)
        else:
            notes.append("Competitor gap needs DataForSEO; Google Ads cannot produce it.")

    # Volume -- provider chain.
    if keywords:
        if dfs is not None:
            try:
                volume = dfs.keyword_overview([str(k) for k in keywords])
                providers["volume"] = "dataforseo"
            except ApiError as exc:
                return exc.to_envelope(_SERVICE)
        elif gads_configured:
            providers["volume"] = "google_ads_pending"
            notes.append(
                "Google Ads is configured but volume needs an adwords-scope OAuth "
                "re-consent (the running token lacks it) -- a live tester setup step. "
                "No volumes returned this run."
            )

    return ok({
        "target_domain": _domain(target_domain) if target_domain else None,
        "competitors": [_domain(c) for c in competitors[:_MAX_COMPETITORS]],
        "providers": providers,
        "competitor_gap": competitor_gap,
        "competitor_gap_count": len(competitor_gap) if competitor_gap is not None else None,
        "volume": volume,
        "notes": notes,
        "caveats": [
            "Competitor gap is the durable value here (no Google product provides "
            "it). External volume is a degraded directional signal in 2026 "
            "(bucketing + clickstream noise + zero-click erosion) -- a tiebreaker, "
            "never a gate. Owned GSC impressions beat it.",
            "Never drop a high-intent zero-volume term just because external "
            "volume is low or missing.",
        ],
    })


TOOLS = [TOOL]
HANDLERS = {"keyword_universe": keyword_universe}
