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
    {
        "name": "pre_deploy_check",
        "description": (
            "Deploy-gate workflow. For each URL: inspect_meta, "
            "check_canonical, validate_schema, redirect_chain_audit, "
            "mixed_content_check. Plus robots_txt_validate for the site. "
            "The recognizable label SEO teams reach for before pushing a "
            "production deploy. Complements technical_seo_audit (which "
            "audits one URL deeply) and structured_data_audit (which "
            "focuses on Rich Results). Use this for the broad batch check."
        ),
        "arguments": [
            {"name": "urls", "description": "JSON array of absolute URLs to gate before deploy. Required (at least 1).", "required": True},
        ],
    },
    {
        "name": "content_brief",
        "description": (
            "Build an evidence-based content brief for a topic. Pulls the "
            "current top rankers from GSC, inspects their meta + schema (and "
            "your existing page if any), then synthesizes a brief with a "
            "calibrated word-count target, heading structure, schema type, "
            "internal-link targets, competitor gaps, and target queries. "
            "SEOMonster brings the rules and the competitor evidence; the LLM "
            "brings the writing. No ranking guarantee: this structures the work."
        ),
        "arguments": [
            {"name": "topic", "description": "The topic / working title to brief. Required.", "required": True},
            {"name": "target_query", "description": "The primary search query the content should win. Required.", "required": True},
            {"name": "site_url", "description": "GSC property override. Defaults to the configured SEO_MCP_GSC_DEFAULT_SITE.", "required": False},
        ],
    },
    {
        "name": "content_outline",
        "description": (
            "Turn a content brief into a validated H2/H3 outline. States the "
            "structural rules the outline must pass (>= 5 H2 sections, >= 70% "
            "of the brief's target queries referenced, H1 carries the primary "
            "keyword, intro -> body -> conclusion flow, HowTo schema requires "
            "structured steps). SEOMonster supplies the structure rules; the "
            "LLM writes the outline."
        ),
        "arguments": [
            {"name": "brief", "description": "The content brief (from content_brief, or pasted) to outline. Required.", "required": True},
        ],
    },
    {
        "name": "content_article",
        "description": (
            "Draft the full article from an outline + brief, then self-validate "
            "against the brief's rules: word count within +/-15% of target, "
            "per-section minimum length, sensible keyword density, internal-link "
            "suggestions, an inline JSON-LD schema hint, no em-dashes, no filler. "
            "The LLM writes the prose; SEOMonster supplies the validation checklist."
        ),
        "arguments": [
            {"name": "outline", "description": "The H2/H3 outline (from content_outline). Required.", "required": True},
            {"name": "brief", "description": "The content brief (from content_brief) with the targets to validate against. Required.", "required": True},
        ],
    },
    {
        "name": "content_workflow",
        "description": (
            "End-to-end content pipeline orchestration: surface an opportunity "
            "with content_opportunities, then chain content_brief -> "
            "content_outline -> content_article, gate the draft URL with "
            "pre_deploy_check, notify search engines via gsc_request_indexing + "
            "indexnow_submit, and schedule a content_performance check. "
            "SEOMonster sequences the chain; the LLM does the writing at each step."
        ),
        "arguments": [
            {"name": "site_url", "description": "GSC property override. Defaults to the configured SEO_MCP_GSC_DEFAULT_SITE.", "required": False},
            {"name": "days", "description": "Lookback window for the opportunity scan. Defaults to 28.", "required": False},
        ],
    },
    {
        "name": "content_performance",
        "description": (
            "Layer-5 measurement of published content. Compares the URL's "
            "ranking + clicks before and after publish (gsc_compare_periods + "
            "gsc_search_analytics filtered to the URL/queries) ~4 to 8 weeks "
            "post-publish and outputs a before/after table. Honest framing: it "
            "measures the outcome, it does not guarantee it."
        ),
        "arguments": [
            {"name": "url", "description": "Absolute URL of the published page to measure. Required.", "required": True},
            {"name": "target_queries", "description": "JSON array of the queries the page targeted, to filter the analytics. Optional.", "required": False},
            {"name": "site_url", "description": "GSC property override. Defaults to the configured SEO_MCP_GSC_DEFAULT_SITE.", "required": False},
        ],
    },
    {
        "name": "seo_setup_audit",
        "description": (
            "Whole-stack configuration audit: is your stack set up so SEO can "
            "even work? Chains ga4_setup_audit (measurement), cf_settings_audit "
            "(CDN/TLS), psi_opportunities (Lighthouse SEO basics on a "
            "representative URL), and robots_txt_validate (crawl access), then "
            "produces one severity-ranked report. Audits configuration hygiene "
            "and removes known blockers; it does not predict ranking."
        ),
        "arguments": [
            {"name": "site_url", "description": "GSC property / site override. Defaults to the configured SEO_MCP_GSC_DEFAULT_SITE.", "required": False},
            {"name": "property_id", "description": "GA4 property id override. Defaults to the configured GA4 property.", "required": False},
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


def _render_pre_deploy_check(args: dict[str, Any]) -> str:
    urls = args.get("urls") or "(none provided)"
    # Take the first URL's host root for robots_txt_validate; if no URL or
    # not parseable, the LLM falls back to an explicit prompt.
    from urllib.parse import urlparse
    host_root = "(derive the site_url from the first URL)"
    first_url = ""
    if isinstance(urls, list) and urls:
        first_url = urls[0]
    elif isinstance(urls, str) and urls != "(none provided)":
        # Allow stringified arrays for hosts that pass JSON-encoded strings.
        try:
            parsed = json.loads(urls)
            if isinstance(parsed, list) and parsed:
                first_url = parsed[0]
        except (ValueError, TypeError):
            pass
    if first_url:
        p = urlparse(first_url)
        if p.scheme and p.netloc:
            host_root = f"{p.scheme}://{p.netloc}/"
    return (
        "# Pre-deploy check\n\n"
        "## Arguments\n"
        f"- urls: {urls}\n\n"
        "## Workflow\n"
        "Run as a deploy gate. Execute in order; one envelope per tool "
        "invocation so the host's audit log captures every check.\n\n"
        "  1. Call `robots_txt_validate` with "
        f"`{{ \"site_url\": \"{host_root}\", \"probes\": [{{ "
        "\"user_agent\": \"Googlebot\", \"url\": \"<the first URL>\" }] }}` "
        "to confirm robots.txt does not block the deploy host and "
        "Googlebot can crawl at least one of the URLs.\n"
        "  2. For each URL in `urls`, call `inspect_meta` "
        "with `{ \"url\": <the_url> }` to capture title, meta description, "
        "canonical, OG cards, hreflang, H1 count.\n"
        "  3. For each URL, call `check_canonical` "
        "with `{ \"url\": <the_url> }` to verify canonical resolves to 2xx "
        "and matches the fetched URL.\n"
        "  4. For each URL, call `validate_schema` with `{ \"url\": "
        "<the_url> }` to verdict any JSON-LD blocks against the Google "
        "Rich Results required-field set.\n"
        "  5. For each URL, call `redirect_chain_audit` with "
        "`{ \"url\": <the_url> }` to detect chain length, protocol "
        "downgrades, and non-2xx termini.\n"
        "  6. For each URL, call `mixed_content_check` with "
        "`{ \"url\": <the_url> }` (no-op for http:// URLs).\n\n"
        "## Output\n"
        "Produce a deploy-gate verdict. Block the deploy if ANY of:\n"
        "- robots.txt would block Googlebot on any URL (`probes[*].allowed: "
        "false`),\n"
        "- a URL's canonical target returns non-2xx,\n"
        "- a redirect chain returns a non-2xx terminus or contains a loop,\n"
        "- mixed_content_check returns `mixed_content_found` on an "
        "https:// URL,\n"
        "- validate_schema returns `verdict=fail` on any JSON-LD block "
        "where `missing_required` is non-empty (skip if the page has no "
        "JSON-LD; that's not a deploy-blocker on its own).\n\n"
        "Otherwise approve the deploy. Surface every check result in a "
        "concise per-URL table the user can paste into the PR or deploy "
        "ticket."
    )


def _render_content_brief(args: dict[str, Any]) -> str:
    topic = args.get("topic") or "(none provided)"
    target_query = args.get("target_query") or "(none provided)"
    site_arg = args.get("site_url")
    site_param = f', "site_url": "{site_arg}"' if site_arg else ""
    site_phrase = f"`{site_arg}`" if site_arg else "the configured default property"
    return (
        "# Content brief\n\n"
        "Honest framing: SEOMonster brings the rules and the competitor "
        "evidence, the LLM brings the writing. This brief structures and "
        "validates the work; it does not guarantee a ranking.\n\n"
        "## Arguments\n"
        f"- topic: {topic}\n"
        f"- target_query: {target_query}\n"
        f"- site_url: {site_phrase}\n\n"
        "## Workflow\n"
        "Execute in order; one envelope per tool invocation.\n\n"
        f"  1. Call `gsc_top_pages_by_query` with `{{ \"query\": \"{target_query}\", \"days\": 28, \"limit\": 5{site_param} }}` "
        "to identify who currently ranks for the target query. Treat the top "
        "3 ranking URLs as the competitor set. If one of the returned pages "
        "belongs to this site, treat it as the user's existing page.\n"
        "  2. For each of the top 3 ranking URLs, call `inspect_meta` with "
        "`{ \"url\": <the_url> }` to capture their title, meta description, "
        "H1, and heading shape, then call `inspect_schema` with "
        "`{ \"url\": <the_url> }` to see what schema.org @types they declare.\n"
        "  3. If the user already has a page ranking for this topic, run "
        "`inspect_meta` and `inspect_schema` on it too, so the brief can say "
        "refresh-vs-net-new and reuse the existing schema type.\n"
        "  4. Estimate each competitor's body word count from its heading "
        "structure and visible content, and compute the top-ranker average. "
        "This calibrates the target word count (do not invent a round number, "
        "anchor it to the competitor average).\n\n"
        "## Output: the brief (all sections REQUIRED)\n"
        "- **Target word count**: the top-ranker average (state the per-"
        "competitor numbers you derived it from).\n"
        "- **Heading structure**: a proposed H1 plus the H2/H3 skeleton, "
        "informed by the competitors' heading shapes.\n"
        "- **Schema type**: the single most appropriate schema.org @type "
        "(reuse the existing page's type if refreshing; match the dominant "
        "competitor type otherwise).\n"
        "- **Internal-link targets**: concrete on-site pages the new content "
        "should link to.\n"
        "- **Competitor gaps**: subtopics the top rankers miss or cover "
        "thinly, framed as the angle this content can win on.\n"
        "- **Target queries to cover**: the primary query plus the related "
        "queries and entities the content must address.\n\n"
        "## Validation rules the brief must pass\n"
        "- Word-count target is anchored to the top-ranker average, not a "
        "guess.\n"
        "- Heading structure has a single H1 containing the primary keyword.\n"
        "- Exactly one schema type is named.\n"
        "- At least one internal-link target and at least one competitor gap "
        "are listed.\n"
        "- The target-queries list is non-empty and includes the primary "
        "query."
    )


def _render_content_outline(args: dict[str, Any]) -> str:
    brief = args.get("brief") or "(none provided)"
    return (
        "# Content outline\n\n"
        "Honest framing: SEOMonster supplies the structure rules, the LLM "
        "writes the outline. The rules below remove known structural blockers; "
        "they do not guarantee a ranking.\n\n"
        "## Arguments\n"
        f"- brief:\n{brief}\n\n"
        "## Task\n"
        "Produce an H2/H3 outline for the article described by the brief "
        "above. Use the brief's heading structure as the starting point and "
        "expand it into a full section-by-section outline with one-line notes "
        "on what each section covers and which target queries it serves.\n\n"
        "## Validation rules the outline MUST pass\n"
        "- At least 5 H2 sections.\n"
        "- References at least 70% of the brief's target queries across the "
        "section notes (list which query maps to which section).\n"
        "- The H1 includes the primary keyword from the brief.\n"
        "- Logical flow: an intro section, then the body sections, then a "
        "conclusion / wrap-up.\n"
        "- If the brief's schema type is `HowTo`, the body must be expressed "
        "as ordered, structured steps (one H2 or H3 per step) so the steps "
        "map cleanly to HowTo schema.\n\n"
        "## Output\n"
        "Emit the outline as nested markdown headings, then a short "
        "self-check confirming each validation rule above is satisfied "
        "(H2 count, query-coverage percentage, H1 keyword present, flow, "
        "HowTo steps if applicable)."
    )


def _render_content_article(args: dict[str, Any]) -> str:
    outline = args.get("outline") or "(none provided)"
    brief = args.get("brief") or "(none provided)"
    return (
        "# Content article draft\n\n"
        "Honest framing: the LLM writes the prose, SEOMonster supplies the "
        "validation checklist. Passing these checks removes known on-page "
        "blockers; it does not guarantee a ranking.\n\n"
        "## Arguments\n"
        f"- outline:\n{outline}\n\n"
        f"- brief:\n{brief}\n\n"
        "## Task\n"
        "Write the full article following the outline section by section, "
        "honoring the brief's target queries, schema type, and internal-link "
        "targets.\n\n"
        "## Validation rules the draft MUST pass\n"
        "- **Word count**: within +/-15% of the brief's target word count.\n"
        "- **Per-section length**: each H2 section meets a sensible minimum "
        "(roughly 150 words or more) so no section is a stub.\n"
        "- **Keyword presence + density**: the primary query and the brief's "
        "target queries appear at a natural density (avoid stuffing; aim "
        "roughly 0.5 to 2% for the primary term).\n"
        "- **Internal links**: include the brief's internal-link targets as "
        "inline link suggestions (anchor text plus the target URL).\n"
        "- **Schema hint**: end with an inline JSON-LD block matching the "
        "brief's schema type, populated from the article (a hint for the "
        "publisher, clearly marked as a suggestion).\n"
        "- **No em-dashes** anywhere in the prose; use commas, colons, or "
        "periods instead.\n"
        "- **No filler**: no throat-clearing intros, no padding sentences "
        "written only to hit word count.\n\n"
        "## Output\n"
        "Emit the article in markdown, then the JSON-LD schema hint, then a "
        "validation summary: measured word count vs target (and the +/-15% "
        "verdict), shortest section length, primary-keyword density, list of "
        "internal links included, and a confirmation that no em-dashes are "
        "present."
    )


def _render_content_workflow(args: dict[str, Any]) -> str:
    site_arg = args.get("site_url")
    days = args.get("days") or 28
    site_param = f', "site_url": "{site_arg}"' if site_arg else ""
    site_phrase = f"`{site_arg}`" if site_arg else "the configured default property"
    return (
        "# Content workflow (end to end)\n\n"
        "Honest framing: SEOMonster sequences the chain and supplies the rules "
        "at each step, the LLM does the writing. The pipeline maximizes return "
        "on effort and removes known blockers; it does not guarantee a ranking.\n\n"
        "## Arguments\n"
        f"- site_url: {site_phrase}\n"
        f"- days: {days}\n\n"
        "## Workflow\n"
        "Run these steps in order. Pause for the user to confirm the chosen "
        "topic and to review each draft before moving on.\n\n"
        f"  1. Call the `content_opportunities` tool with `{{ \"days\": {days}{site_param} }}` "
        "to surface scored topic candidates from the site's own GSC data. "
        "Present the top candidates with their click-upside and action flag "
        "(refresh / consolidate / new) and pick one topic + its target query.\n"
        "  2. Run the `content_brief` prompt with the chosen `topic` and "
        "`target_query` (and `site_url` if set) to build the evidence-based "
        "brief.\n"
        "  3. Run the `content_outline` prompt with that `brief` to produce a "
        "validated H2/H3 outline.\n"
        "  4. Run the `content_article` prompt with that `outline` and `brief` "
        "to draft the article. Hand the draft to the user to publish.\n"
        "  5. Once published, run the `pre_deploy_check` prompt on the draft "
        "URL to gate it (robots, canonical, schema, redirects, mixed content) "
        "before it goes live.\n"
        "  6. After it is live, call `gsc_request_indexing` with "
        "`{ \"urls\": [<the published URL>] }` to notify Google, then call "
        "`indexnow_submit` with `{ \"url\": <the published URL> }` to notify "
        "Bing, Yandex, and the other IndexNow engines.\n"
        "  7. Schedule a follow-up: in 4 to 8 weeks, run the "
        "`content_performance` prompt on the published URL with its target "
        "queries to measure the ranking and click lift.\n\n"
        "## Output\n"
        "After each step, report the envelope / result so a failure mid-chain "
        "leaves the user with a clear partial-success picture. End with the "
        "scheduled content_performance check date so the loop closes."
    )


def _render_content_performance(args: dict[str, Any]) -> str:
    url = args.get("url") or "(none provided)"
    target_queries = args.get("target_queries") or "(none provided; measure the URL as a whole)"
    site_arg = args.get("site_url")
    site_param = f', "site_url": "{site_arg}"' if site_arg else ""
    site_phrase = f"`{site_arg}`" if site_arg else "the configured default property"
    return (
        "# Content performance (Layer-5 measurement)\n\n"
        "Honest framing: this measures the outcome, it does not guarantee it. "
        "We own the controllable inputs, not Google's ranking algorithm, so "
        "this prompt reports the lift, it does not claim to have caused it.\n\n"
        "## Arguments\n"
        f"- url: {url}\n"
        f"- target_queries: {target_queries}\n"
        f"- site_url: {site_phrase}\n\n"
        "## Workflow\n"
        "Run ~4 to 8 weeks after publish so Google has had time to crawl, "
        "index, and settle rankings. Execute in order.\n\n"
        f"  1. Call `gsc_compare_periods` with `{{ \"days\": 28, \"sort_by\": \"delta_clicks\", \"sort_dir\": \"desc\", \"top\": 50{site_param} }}` "
        "and locate the rows for this URL to read its clicks / impressions / "
        "position delta between the prior and current windows.\n"
        f"  2. Call `gsc_search_analytics` filtered to this page with "
        f"`{{ \"dimensions\": [\"query\"], \"page\": \"{url}\", \"days\": 28{site_param} }}` "
        "to get the per-query clicks, impressions, CTR, and average position "
        "for the page now. If `target_queries` was supplied, restrict the "
        "report / your reading to those queries so the table stays focused.\n"
        "  3. If the host supports a date range, pull the equivalent "
        "pre-publish window for the same page/queries so you have a true "
        "before snapshot; otherwise use the prior-window figures from the "
        "compare_periods call in step 1.\n\n"
        "## Output\n"
        "Produce a before/after table with one row per target query (plus a "
        "page total row), columns: clicks before, clicks after, impressions "
        "before, impressions after, avg position before, avg position after, "
        "CTR before, CTR after. Follow it with a short read of the ranking and "
        "click lift, and restate the honest bound: this is the measured "
        "outcome, not a guarantee, and other factors (seasonality, SERP "
        "features, competitor moves) can affect it."
    )


def _render_seo_setup_audit(args: dict[str, Any]) -> str:
    site_arg = args.get("site_url")
    property_arg = args.get("property_id")
    site_phrase = f"`{site_arg}`" if site_arg else "the configured default property"
    property_phrase = f"`{property_arg}`" if property_arg else "the configured GA4 property"
    property_param = f' with `{{ "property_id": "{property_arg}" }}`' if property_arg else ""
    from urllib.parse import urlparse
    host_root = "(derive the site root from the site_url)"
    if site_arg:
        cleaned = site_arg.replace("sc-domain:", "https://") if site_arg.startswith("sc-domain:") else site_arg
        p = urlparse(cleaned)
        if p.scheme and p.netloc:
            host_root = f"{p.scheme}://{p.netloc}/"
        elif site_arg.startswith("sc-domain:"):
            host_root = f"https://{site_arg.split(':', 1)[1]}/"
    return (
        "# SEO setup audit (whole-stack configuration)\n\n"
        "Honest framing: this audits configuration hygiene, that is, whether "
        "your stack is set up so SEO can even work. It removes known blockers "
        "and flags misconfigurations; it does not predict or guarantee "
        "ranking.\n\n"
        "## Arguments\n"
        f"- site_url: {site_phrase}\n"
        f"- property_id: {property_phrase}\n\n"
        "## Workflow\n"
        "Execute in order; one envelope per tool invocation so each layer's "
        "findings stay attributable.\n\n"
        f"  1. **Measurement layer.** Call `ga4_setup_audit`{property_param} to "
        "check the GA4 property is configured to measure organic outcomes "
        "(web stream present, key events defined, 14-month data retention, "
        "enhanced measurement, site-search, content grouping, Google Signals "
        "state). Without this you cannot measure SEO outcomes at all.\n"
        "  2. **CDN / TLS layer.** Call `cf_settings_audit` to check "
        "Cloudflare is not silently sabotaging crawl/index (SSL/TLS mode, "
        "Always Use HTTPS, HSTS, Automatic HTTPS Rewrites, Brotli, browser "
        "cache TTL). Note: CF cannot see the origin, so several of these are "
        "`verify`, not `fail`.\n"
        f"  3. **On-page basics.** Pick a representative URL for the site "
        f"(e.g. `{host_root}` or an important content page) and call "
        "`psi_opportunities` on it for the Lighthouse SEO category gates "
        "(is-crawlable, http-status, viewport, document-title, "
        "meta-description, hreflang, canonical, image-alt). This is the "
        "at-a-glance cross-check.\n"
        f"  4. **Crawl access.** Call `robots_txt_validate` with "
        f"`{{ \"site_url\": \"{host_root}\", \"probes\": [{{ "
        f"\"user_agent\": \"Googlebot\", \"url\": \"{host_root}\" }}] }}` to "
        "confirm robots.txt does not block Googlebot from the site.\n\n"
        "## Output\n"
        "Produce one consolidated, severity-ranked report (critical -> high "
        "-> medium -> low -> info) across all four layers. For each finding "
        "show: which layer, the rule, observed vs expected, a one-line why, "
        "and the benign exception when the flagged value can be legitimate. "
        "Reconcile overlaps: prefer the dedicated page-level signals "
        "(robots_txt_validate, canonical/meta checks) over the Lighthouse "
        "SEO cross-check when they disagree, and say so. Lead the summary "
        "with the question this answers: is the whole stack configured for "
        "SEO? List the critical blockers first, then the verify items that "
        "need the user to confirm origin reality."
    )


_RENDERERS = {
    "post_deploy_verify": _render_post_deploy_verify,
    "weekly_review": _render_weekly_review,
    "content_audit": _render_content_audit,
    "migration_check": _render_migration_check,
    "technical_seo_audit": _render_technical_seo_audit,
    "structured_data_audit": _render_structured_data_audit,
    "pre_deploy_check": _render_pre_deploy_check,
    "content_brief": _render_content_brief,
    "content_outline": _render_content_outline,
    "content_article": _render_content_article,
    "content_workflow": _render_content_workflow,
    "content_performance": _render_content_performance,
    "seo_setup_audit": _render_seo_setup_audit,
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
