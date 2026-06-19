"""ai_referral_overview (roadmap Track A, Wave 1).

Two first-party views of "are the AI surfaces reaching us and sending traffic?",
both from data we already have access to -- no paid API:

  1. AI referral traffic from GA4: detect sessions whose source is an AI app
     (chatgpt.com, perplexity.ai, gemini.google.com, ...) or whose medium is the
     native ``ai-assistant`` channel (GA launched 2026). Reconciled two ways
     because Google's recognized list is volatile (Claude already dropped,
     Perplexity absent) -- so we ALSO regex the session source, retroactively.

  2. AI crawler coverage: does robots.txt allow the AI crawlers/fetchers to
     reach the site? Reuses ``robots_txt_validate``'s RFC-9309 verdict engine
     with the curated ``ai_crawlers`` list as probes.

Honest caveats baked into the output: ~70% of AI referrals arrive as
Direct/dark (copy-paste, stripped referrers, in-app webviews), so referral
counts systematically UNDERCOUNT; and AI-Overview clicks count as Organic, not
AI referral.
"""

from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlparse

from ..clients.ga4 import normalize_property_id
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations
from . import robots_tools
from .ai_crawlers import AI_CRAWLERS

_SERVICE = "ai"

# AI app referrer hosts -> normalized engine label. Substring match on the GA4
# sessionSource. Volatile by design; extend as new apps appear.
_AI_HOSTS = {
    "chatgpt.com": "ChatGPT",
    "chat.openai.com": "ChatGPT",
    "openai.com": "ChatGPT",
    "perplexity.ai": "Perplexity",
    "gemini.google.com": "Gemini",
    "bard.google.com": "Gemini",
    "copilot.microsoft.com": "Copilot",
    "bing.com/chat": "Copilot",
    "claude.ai": "Claude",
    "you.com": "You.com",
    "poe.com": "Poe",
    "meta.ai": "Meta AI",
    "deepseek.com": "DeepSeek",
    "grok.com": "Grok",
    "x.ai": "Grok",
    "phind.com": "Phind",
}
_AI_MEDIUMS = {"ai-assistant", "ai_assistant", "ai-chat"}
_AI_SOURCE_RE = re.compile("|".join(re.escape(h) for h in _AI_HOSTS), re.IGNORECASE)


TOOL = {
    "name": "ai_referral_overview",
    "description": (
        "First-party view of AI traffic: (1) referral sessions from AI apps "
        "(ChatGPT, Perplexity, Gemini, Copilot, Claude, ...) via GA4, detected "
        "by both the native ai-assistant channel and a source-host regex; and "
        "(2) AI-crawler coverage -- whether robots.txt lets GPTBot, ClaudeBot, "
        "PerplexityBot, etc. reach the site. Needs a GA4 property (for referral) "
        "and/or a site_url (for crawl coverage). Honest bound: AI referrals "
        "undercount badly (~70% land as Direct), and AI-Overview clicks count "
        "as Organic, not here."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "property_id": {"type": "string", "description": "GA4 property (e.g. 'properties/123' or '123'). Defaults to the configured property."},
            "site_url": {"type": "string", "description": "Any URL on the target host, for the robots crawl-coverage check. Defaults to deriving from the GSC default site."},
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "Lookback window for GA4 referral. Defaults to 28."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def _site_root(value: str | None) -> str | None:
    """Normalize a site argument (URL or 'sc-domain:host') to a scheme+host root."""
    if not value:
        return None
    v = value.strip()
    if v.startswith("sc-domain:"):
        return f"https://{v.split(':', 1)[1]}/"
    p = urlparse(v)
    if p.scheme and p.netloc:
        return f"{p.scheme}://{p.netloc}/"
    return None


def _ga4_referral(clients: Mapping[str, Any], property_id: str | None, days: int) -> tuple[dict[str, Any] | None, str]:
    """Returns (referral_section_or_None, note). Soft: any not-applicable state
    returns None + an explanatory note rather than failing the whole tool."""
    if not property_id:
        return None, "GA4 referral not run: no property_id configured or passed."
    try:
        ga4 = clients.get("ga4")
    except Exception:
        ga4 = None
    if ga4 is None:
        return None, "GA4 referral not run: no GA4 client / credentials available."
    try:
        report = ga4.run_report(
            property_id,
            dimensions=["sessionSource", "sessionMedium"],
            metrics=["sessions", "conversions"],
            start_date=f"{days}daysAgo",
            end_date="today",
            row_limit=500,
            order_by={"metric": "sessions", "desc": True},
        )
        totals = ga4.run_report(
            property_id,
            dimensions=[],
            metrics=["sessions"],
            start_date=f"{days}daysAgo",
            end_date="today",
            row_limit=1,
        )
    except Exception as exc:
        return None, f"GA4 referral not run: report failed ({type(exc).__name__})."

    by_engine: dict[str, dict[str, float]] = {}
    for row in report.get("rows", []):
        dims = row.get("dimensions") or ["", ""]
        source = (dims[0] if dims else "").strip()
        medium = (dims[1] if len(dims) > 1 else "").strip().lower()
        engine = _classify(source, medium)
        if engine is None:
            continue
        mets = row.get("metrics") or [0, 0]
        sessions = float(mets[0] or 0)
        conversions = float(mets[1] or 0) if len(mets) > 1 else 0.0
        slot = by_engine.setdefault(engine, {"sessions": 0.0, "conversions": 0.0})
        slot["sessions"] += sessions
        slot["conversions"] += conversions

    total_rows = totals.get("rows", [])
    total_sessions = float(total_rows[0]["metrics"][0]) if total_rows and total_rows[0].get("metrics") else 0.0
    ai_sessions = sum(v["sessions"] for v in by_engine.values())

    by_source = sorted(
        ({"engine": k, "sessions": int(v["sessions"]), "conversions": round(v["conversions"], 2)} for k, v in by_engine.items()),
        key=lambda r: r["sessions"],
        reverse=True,
    )
    section = {
        "days": days,
        "by_source": by_source,
        "ai_sessions": int(ai_sessions),
        "total_sessions": int(total_sessions),
        "share_of_traffic": round(ai_sessions / total_sessions, 5) if total_sessions else None,
    }
    return section, "GA4 referral applied."


def _classify(source: str, medium: str) -> str | None:
    if medium in _AI_MEDIUMS:
        # native channel; try to name the engine from the source, else generic
        m = _AI_SOURCE_RE.search(source)
        if m:
            return _engine_for(m.group(0))
        return "AI Assistant (native channel)"
    m = _AI_SOURCE_RE.search(source)
    if m:
        return _engine_for(m.group(0))
    return None


def _engine_for(host_fragment: str) -> str:
    frag = host_fragment.lower()
    for host, label in _AI_HOSTS.items():
        if host in frag or frag in host:
            return label
    return host_fragment


def _crawl_coverage(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any], root: str | None) -> tuple[list[dict[str, Any]] | None, str]:
    if not root:
        return None, "Crawl coverage not run: no site_url provided or derivable."
    probes = [{"user_agent": c["token"], "url": root} for c in AI_CRAWLERS]
    res = robots_tools.robots_txt_validate({"site_url": root, "probes": probes}, config, clients)
    if not res.get("ok"):
        return None, f"Crawl coverage not run: robots fetch failed ({res['error']['code']})."
    data = res["data"]
    # When there is no robots.txt, every crawler is allowed (no probes returned).
    no_robots = data.get("verdict") == "no_robots_txt" or not data.get("groups")
    probe_by_ua = {pr["user_agent"].lower(): pr for pr in data.get("probes", [])}
    coverage: list[dict[str, Any]] = []
    for c in AI_CRAWLERS:
        pr = probe_by_ua.get(c["token"].lower())
        allowed = True if no_robots else (pr["allowed"] if pr else True)
        coverage.append({
            "crawler": c["token"],
            "operator": c["operator"],
            "category": c["category"],
            "honors_robots": c["honors_robots"],
            "allowed": allowed,
            "matched_rule": (pr or {}).get("matched_rule") if not no_robots else None,
            "note": c["note"],
        })
    return coverage, "Crawl coverage applied."


def ai_referral_overview(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    property_id = normalize_property_id(arguments.get("property_id") or getattr(config, "ga4_property_id", None))
    root = _site_root(arguments.get("site_url") or getattr(config, "gsc_default_site", None))
    days = int(arguments.get("days", 28))

    if not property_id and not root:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            "Provide property_id (for GA4 AI-referral) and/or site_url (for AI-crawler coverage).",
            remediation="Pass property_id and/or site_url, or configure SEO_MCP_GA4_PROPERTY_ID / a GSC default site.",
            docs_url=DOCS_BASE + "configuration",
        )

    referral, referral_note = _ga4_referral(clients, property_id, days)
    coverage, coverage_note = _crawl_coverage(arguments, config, clients, root)

    blocked_search = [c["crawler"] for c in (coverage or []) if c["category"] == "search" and not c["allowed"]]

    caveats = [
        "AI referral traffic UNDERCOUNTS badly: ~70% of AI-app referrals arrive "
        "as Direct/dark (copy-paste, stripped referrers, in-app webviews). Treat "
        "by_source as a floor, not the total AI influence.",
        "AI-Overview / AI-Mode clicks count as Organic Search in GA4, not as AI "
        "referral -- they are NOT in by_source.",
        "The native ai-assistant channel is forward-only and its recognized host "
        "list is volatile (Claude dropped, Perplexity absent), so the source "
        "regex is the retroactive backstop.",
    ]
    if blocked_search:
        caveats.insert(0, f"robots.txt blocks AI search crawlers that could cite you: {', '.join(blocked_search)}.")

    return ok({
        "property_id": property_id,
        "site_root": root,
        "ai_referral": referral,
        "ai_referral_note": referral_note,
        "crawl_coverage": coverage,
        "crawl_coverage_note": coverage_note,
        "caveats": caveats,
    })


TOOLS = [TOOL]
HANDLERS = {"ai_referral_overview": ai_referral_overview}
