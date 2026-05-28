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


_RENDERERS = {
    "post_deploy_verify": _render_post_deploy_verify,
    "weekly_review": _render_weekly_review,
    "content_audit": _render_content_audit,
    "migration_check": _render_migration_check,
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
