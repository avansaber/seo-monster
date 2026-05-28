"""MCP prompts/ capability: named workflow recipes.

Each prompt is a parameterized instruction the host can invoke. The MCP host
(Claude Desktop, Cursor, Cline, Codex) shows them in a slash-style menu or
exposes them via the prompts/list and prompts/get RPC methods.

Why prompts instead of mega-tools: the parent project's first instinct was
to ship `post_deploy_verify(site, urls)` as one tool that internally chained
CF purge -> GSC indexing -> IndexNow -> PSI. That harms composability (a
failure inside step 3 of 6 produces an ambiguous envelope) and reduces
agent transparency. The right MCP idiom is small composable tools chained
by the host. Prompts are how the server publishes named chains the host
can advertise to the user without bundling them into one tool call.

Each prompt's body is a self-contained user-role message: it lists the
arguments the user supplied, then describes the chain of existing tools to
invoke, in order. The LLM reads the message and executes the steps.
"""

from __future__ import annotations

import json
from typing import Any


# Prompt definitions. Each dict mirrors the mcp.types.Prompt shape:
# name, description, arguments (list of {name, description, required}).
PROMPTS: list[dict[str, Any]] = [
    {
        "name": "post_deploy_verify",
        "description": (
            "After deploying or publishing content, fan out the verification "
            "across CF cache purge, GSC indexing request, IndexNow notification, "
            "and a PSI baseline. Replaces a tail of manual tool calls."
        ),
        "arguments": [
            {"name": "urls", "description": "JSON array of absolute URLs that were deployed or updated. Required.", "required": True},
            {"name": "zone", "description": "Cloudflare zone hostname (if different from the configured CF_ZONE).", "required": False},
            {"name": "skip_psi", "description": "If 'true', skip the PSI baseline step (e.g. on a follow-up redeploy).", "required": False},
        ],
    },
    {
        "name": "weekly_review",
        "description": (
            "Weekly SEO summary across the configured GSC property: biggest "
            "gainers, biggest losers, CTR opportunities, content gaps, and the "
            "GA4 organic engagement context."
        ),
        "arguments": [
            {"name": "days", "description": "Window length in days. Defaults to 7.", "required": False},
            {"name": "site_url", "description": "GSC property override. Defaults to the configured SEO_MCP_GSC_DEFAULT_SITE.", "required": False},
        ],
    },
    {
        "name": "content_audit",
        "description": (
            "Cannibalization audit for the configured GSC property. Finds "
            "queries where multiple pages compete, surfaces the competing "
            "pages, and recommends consolidate / differentiate / accept."
        ),
        "arguments": [
            {"name": "site_url", "description": "GSC property override. Defaults to the configured SEO_MCP_GSC_DEFAULT_SITE.", "required": False},
            {"name": "days", "description": "Window length. Defaults to 28.", "required": False},
            {"name": "top_n_queries", "description": "Inspect the top N queries for competing pages. Defaults to 10.", "required": False},
        ],
    },
    {
        "name": "migration_check",
        "description": (
            "Pre/post migration validation. For each URL, check Google's "
            "current indexed status, canonical agreement, and last crawl. "
            "Flag URLs needing remediation."
        ),
        "arguments": [
            {"name": "urls", "description": "JSON array of URLs to validate. Required.", "required": True},
            {"name": "site_url", "description": "GSC property override.", "required": False},
        ],
    },
    {
        "name": "technical_seo_audit",
        "description": (
            "Single-URL technical-SEO sweep. Chains inspect_meta, "
            "check_canonical, redirect_chain_audit, mixed_content_check, and "
            "robots_txt_validate for the URL, then sitemap_health for its host "
            "root. Produces a triage list ranked by severity."
        ),
        "arguments": [
            {"name": "url", "description": "Absolute URL to audit. Required.", "required": True},
        ],
    },
    {
        "name": "structured_data_audit",
        "description": (
            "Structured-data + cross-site sweep. For each URL: inspect_schema "
            "to see what JSON-LD is present, then validate_schema to verdict "
            "the Rich Results required fields. If 2+ URLs are passed, also "
            "run hreflang_consistency_check across the set. Produces a per-"
            "URL findings list plus a global reciprocity-and-target report."
        ),
        "arguments": [
            {"name": "urls", "description": "JSON array of absolute URLs to audit. Required (at least 1).", "required": True},
        ],
    },
]


# Render templates. Each function takes the prompt arguments dict and returns
# the user-role message body the LLM will receive.

def _render_post_deploy_verify(args: dict[str, Any]) -> str:
    urls = args.get("urls") or "(none provided)"
    zone = args.get("zone") or "(use the configured default zone)"
    skip_psi = str(args.get("skip_psi", "")).lower() in ("true", "1", "yes")
    psi_step = (
        "  4. **Skipped** (skip_psi=true)."
        if skip_psi
        else "  4. Pick the most important URL (e.g. the deploy's hero page) and call `psi_analyze` on it to capture a Lighthouse + Core Web Vitals baseline for this deploy."
    )
    return (
        "# Post-deploy verification\n\n"
        "## Arguments\n"
        f"- urls: {urls}\n"
        f"- zone: {zone}\n"
        f"- skip_psi: {skip_psi}\n\n"
        "## Workflow\n"
        "Execute these steps in order. Report each step's envelope before "
        "moving to the next, so a failure leaves the user with a clear "
        "partial-success picture.\n\n"
        "  1. Call `cf_purge_cache` with `{ urls: <urls above>, zone: <zone above if set> }` so "
        "the CDN serves the new content on the next crawl. **Requires `SEO_MCP_ALLOW_DESTRUCTIVE=true`**; "
        "if it returns DESTRUCTIVE_DISABLED, surface the remediation and continue with the rest.\n"
        "  2. Call `gsc_request_indexing` with `{ urls: <urls above> }` to notify Google.\n"
        "  3. Call `indexnow_bulk_submit` with `{ urls: <urls above> }` to notify Bing, Yandex, Naver, Seznam, Yep. "
        "If `indexnow` is unconfigured (AUTH_MISSING), report that and skip.\n"
        f"{psi_step}\n\n"
        "After step 4, summarize: each engine that was successfully notified, "
        "the lab Lighthouse scores from PSI, and any step that returned an "
        "error envelope so the user knows what to retry."
    )


def _render_weekly_review(args: dict[str, Any]) -> str:
    days = args.get("days") or 7
    site_arg = args.get("site_url")
    site_phrase = f"`{site_arg}`" if site_arg else "the configured default property"
    site_param = f', "site_url": "{site_arg}"' if site_arg else ""
    return (
        "# Weekly SEO review\n\n"
        "## Arguments\n"
        f"- site_url: {site_phrase}\n"
        f"- days: {days}\n\n"
        "## Workflow\n"
        f"  1. Call `gsc_compare_periods` with `{{ \"days\": {days}, \"sort_by\": \"delta_clicks\", \"sort_dir\": \"desc\", \"top\": 20{site_param} }}` and list the biggest gainers.\n"
        f"  2. Call `gsc_compare_periods` with `{{ \"days\": {days}, \"sort_by\": \"delta_clicks\", \"sort_dir\": \"asc\", \"top\": 20{site_param} }}` and list the biggest losers.\n"
        f"  3. Call `gsc_query_opportunities` with `{{ \"days\": {days}{site_param} }}` and surface the top CTR-optimization candidates.\n"
        f"  4. Call `gsc_query_gaps` with `{{ \"days\": {days}{site_param} }}` and surface the top impressions-without-clicks queries.\n"
        f"  5. Call `ga4_organic_search_overview` with `{{ \"days\": {days} }}` for the engagement-side context (engaged sessions, conversion rate, average duration).\n\n"
        "Synthesize a 200-word summary: lead with gainers, then losers, then "
        "the two opportunity lists, then the GA4 trend. Use plain numbers (no "
        "percentages-of-percentages); link concrete queries the user can act on."
    )


def _render_content_audit(args: dict[str, Any]) -> str:
    site_arg = args.get("site_url")
    days = args.get("days") or 28
    top_n = args.get("top_n_queries") or 10
    site_param = f', "site_url": "{site_arg}"' if site_arg else ""
    site_phrase = f"`{site_arg}`" if site_arg else "the configured default property"
    return (
        "# Content cannibalization audit\n\n"
        "## Arguments\n"
        f"- site_url: {site_phrase}\n"
        f"- days: {days}\n"
        f"- top_n_queries: {top_n}\n\n"
        "## Workflow\n"
        f"  1. Call `gsc_top_queries` with `{{ \"days\": {days}, \"limit\": {top_n}{site_param} }}` to get the top queries by clicks.\n"
        f"  2. For each query from step 1, call `gsc_top_pages_by_query` with `{{ \"query\": \"<query>\", \"days\": {days}, \"limit\": 5{site_param} }}`.\n"
        "  3. Identify queries where step 2 returned more than one ranking page. Those are cannibalization candidates.\n"
        "  4. For each candidate, capture: the competing pages, their average positions, their clicks share, their CTR.\n\n"
        "Output a per-candidate recommendation:\n"
        "- **Consolidate**: when one page already dominates clicks and the others have low traffic, redirect the weaker pages.\n"
        "- **Differentiate**: when pages have comparable traffic but cover slightly different intents, rewrite titles/metas to disambiguate.\n"
        "- **Accept**: when the pages are intentionally targeting different search intents and traffic is genuinely additive."
    )


def _render_migration_check(args: dict[str, Any]) -> str:
    urls = args.get("urls") or "(none provided)"
    site_arg = args.get("site_url")
    site_param = f', "site_url": "{site_arg}"' if site_arg else ""
    site_phrase = f"`{site_arg}`" if site_arg else "the configured default property"
    sitemap_call = (
        "Call `gsc_list_sitemaps` with `{ \"site_url\": \"" + site_arg + "\" }`"
        if site_arg
        else "Call `gsc_list_sitemaps`"
    )
    return (
        "# Migration validation\n\n"
        "## Arguments\n"
        f"- urls: {urls}\n"
        f"- site_url: {site_phrase}\n\n"
        "## Workflow\n"
        f"  1. Call `gsc_batch_inspect_urls` with `{{ \"urls\": <urls above>{site_param} }}` to get each URL's indexed status, coverage state, canonicals, and last-crawl time in one call. If you have more than 25 URLs, chunk and repeat.\n"
        f"  2. {sitemap_call} so you can flag URLs that should be in a sitemap but are not.\n"
        "  3. Build a per-URL table with columns: verdict, indexed, google_canonical, user_canonical, canonical_agrees, last_crawl_time, coverage_state.\n"
        "  4. Flag for remediation:\n"
        "       - any URL whose verdict is not PASS,\n"
        "       - any URL where google_canonical differs from user_canonical,\n"
        "       - any URL with coverage_state indicating discovery or exclusion issues.\n\n"
        "Output the table first, then the flagged remediation list with one "
        "concrete action per row (resubmit sitemap, fix self-referential "
        "canonical, request indexing, etc.)."
    )


def _render_technical_seo_audit(args: dict[str, Any]) -> str:
    url = args.get("url") or "(none provided)"
    # Derive the host root for the sitemap_health step. We do the slicing
    # outside the f-string so the body stays Python 3.11-compatible (no
    # backslashes inside f-string expressions).
    from urllib.parse import urlparse
    host_root = "(derive from the URL above)"
    parsed = urlparse(url) if url and url != "(none provided)" else None
    if parsed and parsed.scheme and parsed.netloc:
        host_root = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    return (
        "# Technical-SEO audit\n\n"
        "## Arguments\n"
        f"- url: {url}\n\n"
        "## Workflow\n"
        "Execute in order. Each tool call should be a separate tool invocation "
        "so the host's audit log captures one envelope per check.\n\n"
        f"  1. Call `inspect_meta` with `{{ \"url\": \"{url}\" }}` to capture title, "
        "meta description, robots, canonical, OG/Twitter tags, and H1 count.\n"
        f"  2. Call `check_canonical` with `{{ \"url\": \"{url}\" }}` to verify the "
        "canonical link resolves to a 2xx and matches the fetched URL (or note "
        "the deliberate cross-URL canonical).\n"
        f"  3. Call `redirect_chain_audit` with `{{ \"url\": \"{url}\" }}` to count "
        "hops and surface protocol downgrades or non-2xx terminus.\n"
        f"  4. Call `mixed_content_check` with `{{ \"url\": \"{url}\" }}` (no-op "
        "for http:// pages; for https:// pages it flags http:// sub-resources).\n"
        f"  5. Call `robots_txt_validate` with `{{ \"site_url\": \"{url}\", \"probes\": "
        f"[{{ \"user_agent\": \"Googlebot\", \"url\": \"{url}\" }}] }}` to confirm "
        "Googlebot can crawl this URL.\n"
        f"  6. Call `sitemap_health` with `{{ \"sitemap_url\": \"{host_root}\", "
        "\"sample_size\": 25 }}` to sanity-check the site's sitemap quality.\n\n"
        "## Output\n"
        "Produce a ranked triage list. Severity order (highest first):\n"
        "- **Critical**: redirect-loop / non-2xx terminus / Disallow on this "
        "URL / canonical_target_unreachable.\n"
        "- **High**: protocol_downgrade / cross_host canonical without intent "
        "/ mixed_content_found on https.\n"
        "- **Medium**: long_chain (>1 hop) / trailing_slash_drift / multiple H1s.\n"
        "- **Low**: missing meta_description / missing OG tags / "
        "missing_lastmod sitemap entries.\n\n"
        "End with a one-paragraph summary the user can paste into a ticket."
    )


def _render_structured_data_audit(args: dict[str, Any]) -> str:
    urls = args.get("urls") or "(none provided)"
    return (
        "# Structured-data + cross-site audit\n\n"
        "## Arguments\n"
        f"- urls: {urls}\n\n"
        "## Workflow\n"
        "Execute in order. Treat each tool call as a separate invocation so "
        "the host's audit log captures one envelope per check.\n\n"
        "  1. For each URL in `urls`, call `inspect_schema` with "
        "`{ \"url\": <the_url> }` to see which schema.org @types are "
        "declared and how many blocks per type.\n"
        "  2. For each URL in `urls`, call `validate_schema` with "
        "`{ \"url\": <the_url> }` to verdict every JSON-LD block against "
        "the Google Rich Results required-field set. Record entities with "
        "verdict=fail and the `missing_required` list.\n"
        "  3. If `urls` has 2 or more entries, call "
        "`hreflang_consistency_check` with `{ \"urls\": <urls above> }` to "
        "verdict the reciprocity matrix and broken-target list.\n\n"
        "## Output\n"
        "Produce a two-section report:\n"
        "- **Per-URL structured data**. One row per URL with: declared "
        "@types, count of pass entities, count of fail entities, and the "
        "single most impactful missing_required field across the page "
        "(the one that blocks the most Rich Results categories).\n"
        "- **Cross-URL hreflang**. If hreflang ran: counts of reciprocity "
        "misses, broken targets, missing self-links, missing x-default. "
        "List the first 5 examples per category.\n\n"
        "End with a one-paragraph summary the user can paste into a "
        "ticket: how many pages need structured-data fixes, how many "
        "hreflang misses, and the single highest-priority fix."
    )


_RENDERERS = {
    "post_deploy_verify": _render_post_deploy_verify,
    "weekly_review": _render_weekly_review,
    "content_audit": _render_content_audit,
    "migration_check": _render_migration_check,
    "technical_seo_audit": _render_technical_seo_audit,
    "structured_data_audit": _render_structured_data_audit,
}


def render(name: str, arguments: dict[str, Any] | None) -> str:
    """Build the user-role message body for the named prompt.

    Unknown prompts return a short error string the host can surface; the
    server's get_prompt handler turns this into a TextContent in the
    PromptMessage rather than raising.
    """
    args = arguments or {}
    renderer = _RENDERERS.get(name)
    if renderer is None:
        return f"Unknown prompt: {name!r}. Known prompts: {', '.join(sorted(_RENDERERS))}."
    return renderer(args)


def prompt_names() -> list[str]:
    """Names of the registered prompts. Used by tests + system_status."""
    return [p["name"] for p in PROMPTS]
