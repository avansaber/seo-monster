"""ai_citation_track (roadmap Track A, Wave 4) -- the headline AI tool.

Measures whether AI answer engines mention/cite a brand for a managed prompt
set, vs competitors, over a dated cycle. Reframed (design doc §3 A3) from the
brief's "AI rank" -- which is statistically meaningless given LLM
non-determinism -- to a **sampled share-of-voice with confidence intervals and
volatility as a first-class KPI**.

Methodology (the actual, testable intellectual content):
  * Run N samples per prompt per engine (default 7; a single run is
    uninformative). Engines: the configured developer APIs (Perplexity / OpenAI
    / Anthropic / Gemini) plus Google AI Overviews via the DataForSEO SERP
    (no API exists for AIO).
  * brand "appears" in a response if the brand name is in the answer text OR a
    cited domain matches a brand domain.
  * Share of voice = brand appearances / all-brand appearances (simple ratio,
    disclosed). Visibility = brand appearances / total responses, with a 95%
    normal-approx CI. Volatility = 1 - mean Jaccard of cited-domain sets across
    consecutive samples (how much the engine churns run-to-run).

HONEST BOUNDS, surfaced in every response: developer-API output is NOT what a
logged-in user sees (measured ChatGPT API-vs-UI overlap ~24% brands / ~4%
sources); Google AIO has no API and the SERP path is ToS-contested; this is a
directional, sampled signal -- never a guaranteed "rank". Live, paid,
non-deterministic: validated by the tester, not by mocks.
"""

from __future__ import annotations

import statistics
from math import sqrt
from typing import Any, Mapping
from urllib.parse import urlparse

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations

_SERVICE = "ai"
_MAX_PROMPTS = 50
_MAX_SAMPLES = 20
_DEFAULT_SAMPLES = 7
_REMEDIATION = (
    "Configure at least one AI engine key (PERPLEXITY_API_KEY / OPENAI_API_KEY / "
    "ANTHROPIC_API_KEY / GEMINI_API_KEY) and/or DataForSEO (for Google AI "
    "Overviews). See README > Configuration."
)


TOOL = {
    "name": "ai_citation_track",
    "description": (
        "Track brand mention + citation share-of-voice across AI answer engines "
        "(Perplexity/OpenAI/Anthropic/Gemini via their APIs, Google AI Overviews "
        "via DataForSEO) for a managed prompt set, vs competitors. Samples each "
        "prompt N times (default 7) and reports visibility with a 95% confidence "
        "interval, share-of-voice, and run-to-run volatility -- NOT an 'AI rank' "
        "(single runs are statistically meaningless). Honest bound: developer-API "
        "output differs from the logged-in consumer UI, and AIO has no API. Paid + "
        "non-deterministic; results are directional and dated, not guaranteed."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "prompts": {"type": "array", "items": {"type": "string"}, "description": "Managed prompt set (freeze it across cycles). Required, 1-50."},
            "brand": {"type": "string", "description": "Your brand name to detect in answers. Required."},
            "brand_domains": {"type": "array", "items": {"type": "string"}, "description": "Your domain(s) to detect in citations, e.g. ['example.com']."},
            "competitors": {"type": "array", "items": {"type": "string"}, "description": "Competitor brand names for share-of-voice."},
            "engines": {"type": "array", "items": {"type": "string"}, "description": "Subset of available engines; default all configured."},
            "samples": {"type": "integer", "minimum": 1, "maximum": _MAX_SAMPLES, "description": "Samples per prompt per engine. Default 7 (>=7 recommended)."},
        },
        "required": ["prompts", "brand"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True, open_world=True, idempotent=False),
}


def _ci95(successes: int, n: int) -> list[float]:
    if n <= 0:
        return [0.0, 0.0]
    p = successes / n
    se = sqrt(p * (1 - p) / n)
    return [round(max(0.0, p - 1.96 * se), 4), round(min(1.0, p + 1.96 * se), 4)]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def _domains_of(citations: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for c in citations or []:
        d = c.get("domain") or urlparse(c.get("url") or "").netloc.lower()
        if d:
            out.add(d)
    return out


def ai_citation_track(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    try:
        ai = clients.get("ai_engines")
    except Exception:
        ai = None
    try:
        dfs = clients.get("dataforseo")
    except Exception:
        dfs = None

    available: list[str] = list(ai.available_engines()) if ai is not None else []
    if dfs is not None:
        available.append("google_aio")
    if not available:
        return err(ErrorCode.AUTH_MISSING, _SERVICE, "No AI answer engine configured.", remediation=_REMEDIATION, docs_url=DOCS_BASE + "configuration")

    prompts = arguments.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "prompts must be a non-empty array.", docs_url=DOCS_BASE + "configuration")
    prompts = [str(p) for p in prompts if str(p).strip()][:_MAX_PROMPTS]
    brand = arguments.get("brand")
    if not brand:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "brand is required.")
    brand_l = str(brand).lower()
    brand_domains = [str(d).lower() for d in (arguments.get("brand_domains") or [])]
    competitors = [str(c) for c in (arguments.get("competitors") or [])]
    engines = [e for e in (arguments.get("engines") or available) if e in available]
    if not engines:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, f"None of the requested engines are available. Available: {available}.")
    samples = max(1, min(int(arguments.get("samples", _DEFAULT_SAMPLES)), _MAX_SAMPLES))

    def _run(engine: str, prompt: str) -> dict[str, Any]:
        if engine == "google_aio":
            serp = dfs.serp(prompt)
            cits = serp.get("ai_overview_citations") or []
            # F7: detect brands/competitors in the AIO ANSWER BODY (plus citation
            # titles), not citation domains alone -- the body is where named
            # brands appear (Gemini found competitors AIO previously missed).
            text = " ".join(
                [serp.get("ai_overview_text") or ""] + [str(c.get("title") or "") for c in cits]
            ).strip()
            return {"answer_text": text, "citations": cits}
        return ai.query(engine, prompt)

    by_engine: list[dict[str, Any]] = []
    total_brand = 0
    total_n = 0
    total_comp = {c: 0 for c in competitors}
    failed = 0
    query_count = 0

    for engine in engines:
        e_brand = 0
        e_n = 0
        e_comp = {c: 0 for c in competitors}
        brand_cite_urls: set[str] = set()
        prompt_volatility: list[float] = []
        for prompt in prompts:
            domsets: list[set[str]] = []
            for _ in range(samples):
                query_count += 1
                try:
                    r = _run(engine, prompt)
                except ApiError:
                    failed += 1
                    continue
                e_n += 1
                text = (r.get("answer_text") or "").lower()
                cits = r.get("citations") or []
                doms = _domains_of(cits)
                domsets.append(doms)
                appeared = (brand_l in text) or any(bd in doms for bd in brand_domains)
                if appeared:
                    e_brand += 1
                    for c in cits:
                        cd = c.get("domain") or urlparse(c.get("url") or "").netloc.lower()
                        if any(bd in cd for bd in brand_domains) and c.get("url"):
                            brand_cite_urls.add(c["url"])
                for comp in competitors:
                    if comp.lower() in text:
                        e_comp[comp] += 1
            if len(domsets) >= 2:
                sims = [_jaccard(domsets[i], domsets[i + 1]) for i in range(len(domsets) - 1)]
                prompt_volatility.append(1 - statistics.fmean(sims))

        e_total_brands = e_brand + sum(e_comp.values())
        by_engine.append({
            "engine": engine,
            "responses": e_n,
            "brand_visibility": {"value": round(e_brand / e_n, 4) if e_n else 0.0, "ci95": _ci95(e_brand, e_n)},
            # F7: SoV is null ONLY when there's no data (n==0); with responses but
            # zero brand mentions it's 0.0, consistent across engines.
            "share_of_voice": round(e_brand / e_total_brands, 4) if e_total_brands else (0.0 if e_n else None),
            "competitor_appearances": e_comp,
            "volatility": round(statistics.fmean(prompt_volatility), 4) if prompt_volatility else None,
            "brand_citation_urls": sorted(brand_cite_urls),
        })
        total_brand += e_brand
        total_n += e_n
        for c in competitors:
            total_comp[c] += e_comp[c]

    overall_total_brands = total_brand + sum(total_comp.values())
    return ok({
        "brand": brand,
        "brand_domains": brand_domains,
        "competitors": competitors,
        "engines_queried": engines,
        "samples_per_prompt": samples,
        "prompt_count": len(prompts),
        "query_count": query_count,
        "failed_samples": failed,
        "overall": {
            "brand_visibility": {"value": round(total_brand / total_n, 4) if total_n else 0.0, "ci95": _ci95(total_brand, total_n)},
            "share_of_voice": round(total_brand / overall_total_brands, 4) if overall_total_brands else (0.0 if total_n else None),
            "responses": total_n,
        },
        "by_engine": by_engine,
        "methodology": {
            "sov_formula": "brand_appearances / all_brand_appearances (simple ratio)",
            "ci": "95% normal-approx (Wald) on the appearance proportion",
            "volatility": "1 - mean Jaccard of cited-domain sets across consecutive samples",
            "appearance": "brand name in answer text OR a cited domain matching brand_domains",
        },
        "caveats": [
            "Developer-API output is NOT what a logged-in user sees (measured "
            "ChatGPT API-vs-UI overlap ~24% brands / ~4% sources). This is a "
            "directional proxy, labeled per engine.",
            "Google AI Overviews has no API; google_aio uses DataForSEO SERP "
            "scraping, which is ToS-contested and may be fragile.",
            "LLM answers are non-deterministic; a single run is uninformative. "
            "Trust the sampled visibility + CI + volatility, never a point 'rank'. "
            "Freeze the prompt + competitor set across cycles and date each run.",
            "Increase samples for tighter CIs; >=7 recommended, more for high-volatility engines.",
        ],
    })


TOOLS = [TOOL]
HANDLERS = {"ai_citation_track": ai_citation_track}
