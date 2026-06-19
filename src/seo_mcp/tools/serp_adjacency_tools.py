"""serp_adjacency_expand (roadmap Track B, Wave 3).

Expand seed terms (your winning queries) into adjacent terms via SERP signals.
FREE core = Google Autocomplete (a public endpoint hit through the shared
HttpClient -- no key). PAA + related-searches have no free endpoint, so they are
optional via DataForSEO. Degrades gracefully: with no DataForSEO key you still
get autocomplete; if the autocomplete endpoint is flaky a seed is marked
degraded rather than failing the call.

Design notes (design doc §4 B2): Bing autosuggest is intentionally absent (the
API was retired 2025-08-11) and SerpApi is deliberately NOT the default vendor
(active Google DMCA suit). The undocumented Google endpoint can change; treat
this as the most fragile tool and clearly secondary to gsc_keyword_expand.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, Mapping

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations, require_client

_SERVICE = "discovery"
_HTTP_REMEDIATION = "No setup needed; the HTTP client is built in."
_AUTOCOMPLETE = "https://suggestqueries.google.com/complete/search"
_MAX_SEEDS = 15


def _norm(s: str) -> str:
    return " ".join(str(s).strip().lower().split())


def _autocomplete(http: Any, seed: str) -> tuple[list[str], bool]:
    """Return (suggestions, ok). Google's client=firefox returns
    ``[seed, [sugg, ...]]``. Best-effort; (==[], False) on any failure."""
    url = f"{_AUTOCOMPLETE}?{urllib.parse.urlencode({'client': 'firefox', 'q': seed})}"
    try:
        resp = http.fetch(url)
    except ApiError:
        return [], False
    if not (200 <= resp.status < 300):
        return [], False
    try:
        data = json.loads(resp.body_text)
        suggestions = data[1] if isinstance(data, list) and len(data) > 1 else []
        return [s for s in suggestions if isinstance(s, str)], True
    except (ValueError, TypeError, IndexError):
        return [], False


TOOL = {
    "name": "serp_adjacency_expand",
    "description": (
        "Expand seed terms into adjacent terms from SERP signals. FREE core: "
        "Google Autocomplete (no key). Optional PAA + related-searches via "
        "DataForSEO if configured. Pass your winning queries as `seeds` (seed "
        "from gsc_top_queries). Returns per-seed suggestions plus the aggregated "
        "net-new terms (suggestions you didn't seed). Degrades gracefully: "
        "autocomplete-only without a DataForSEO key. Read-only. Note: the "
        "autocomplete endpoint is undocumented and may change."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "seeds": {"type": "array", "items": {"type": "string"}, "description": "Seed terms (your winning queries). Required, 1-15."},
            "include_paa": {"type": "boolean", "description": "Also fetch People-Also-Ask + related searches via DataForSEO (if configured). Default true when DataForSEO is set."},
        },
        "required": ["seeds"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True, open_world=True),
}


def serp_adjacency_expand(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    http, error = require_client(clients, "http", _SERVICE, remediation=_HTTP_REMEDIATION)
    if error:
        return error
    raw_seeds = arguments.get("seeds")
    if not isinstance(raw_seeds, list) or not raw_seeds:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "seeds must be a non-empty array.", docs_url=DOCS_BASE + "gsc")
    seeds = [s for s in (_norm(s) for s in raw_seeds) if s][:_MAX_SEEDS]
    if not seeds:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "seeds contained no usable strings.")

    try:
        dfs = clients.get("dataforseo")
    except Exception:
        dfs = None
    want_paa = arguments.get("include_paa", dfs is not None)

    seed_set = set(seeds)
    per_seed: list[dict[str, Any]] = []
    all_new: set[str] = set()
    autocomplete_ok = 0
    paa_used = False
    for seed in seeds:
        suggestions, okflag = _autocomplete(http, seed)
        if okflag:
            autocomplete_ok += 1
        entry: dict[str, Any] = {"seed": seed, "autocomplete": suggestions, "autocomplete_status": "ok" if okflag else "degraded"}
        for s in suggestions:
            if _norm(s) not in seed_set:
                all_new.add(_norm(s))
        if want_paa and dfs is not None:
            try:
                serp = dfs.serp(seed)
                entry["paa"] = serp.get("paa", [])
                entry["related"] = serp.get("related", [])
                paa_used = True
                for s in entry["paa"] + entry["related"]:
                    if _norm(s) not in seed_set:
                        all_new.add(_norm(s))
            except ApiError as exc:
                entry["paa_status"] = f"unavailable ({exc.code})"
        per_seed.append(entry)

    return ok({
        "seeds": seeds,
        "per_seed": per_seed,
        "net_new_terms": sorted(all_new),
        "net_new_count": len(all_new),
        "source_status": {
            "autocomplete": f"{autocomplete_ok}/{len(seeds)} seeds ok",
            "paa_related": "dataforseo" if paa_used else ("requested but no DataForSEO key" if want_paa else "not requested"),
        },
        "caveats": [
            "Autocomplete is an undocumented Google endpoint; treat as best-effort "
            "and clearly secondary to gsc_keyword_expand.",
            "PAA / related searches require DataForSEO (no free endpoint); Bing "
            "autosuggest is unavailable (API retired 2025-08-11).",
            "net_new_terms are candidates -- validate demand with gsc_keyword_expand "
            "(owned footprint) or keyword_universe (external volume).",
        ],
    })


TOOLS = [TOOL]
HANDLERS = {"serp_adjacency_expand": serp_adjacency_expand}
