# SEOMonster

**SEOMonster turns the AI assistant you already use into an SEO analyst that
works from your own data.** Ask it what to write next and it surfaces the topics
your site is *almost* ranking for; ask whether a page is ready and it checks the
technical SEO before you publish; then it nudges Google and Bing to index the
page and measures whether rankings actually moved. It works from your own Google
Search Console, Analytics 4, and PageSpeed data, which stays on your machine -
there's no new dashboard to learn, you just chat with Claude (or Cursor, Cline,
or Codex).

<sub>The rest of this README is the technical reference for installing and
configuring the server. If you just want the product overview, see
[seomonster.avansaber.com](https://seomonster.avansaber.com).</sub>

SEOMonster is an MCP server for SEO workflows. It exposes strictly SEO-focused
tools over **Google Search Console**, **Google Analytics 4**, **PageSpeed
Insights (PSI)**, **Cloudflare**, **IndexNow**, the **Chrome UX Report (CrUX)
History API**, and a built-in HTTP client for technical-SEO checks
(`inspect_meta`, `check_canonical`, `redirect_chain_audit`,
`mixed_content_check`, `robots_txt_validate`, `sitemap_validate`,
`sitemap_health`), so an AI host (Claude Desktop, Cline, Cursor, Codex) can
query your own data with your own credentials.

- **User-credential-driven.** No auth is baked into the package. Every credential
  is resolved at runtime from your environment or a config file. The published
  package contains zero secrets.
- **Read-first.** Reads are always available. The two routine SEO writes (sitemap
  submit, indexing request) are available by default. The only gated actions are
  the Cloudflare cache-purge tools, behind `SEO_MCP_ALLOW_DESTRUCTIVE`.
- **Lean.** Standard library plus the `mcp` SDK and the Google client libraries.
  PageSpeed Insights and Cloudflare ride on `urllib`, no extra HTTP dependency.

> Published on PyPI as **`seo-monster`**, so the `uvx` command is
> `seo-monster`. The import package is `seo_mcp`, and `seo-mcp` stays as a
> dev/local console alias.

## Requirements

For the **`.mcpb` bundle path** (Claude Desktop): just Claude Desktop on macOS
or Windows. The bundle declares Python 3.11+ as a runtime; Claude Desktop
materializes the environment for you. No prior `uv` install needed.

For the **`uvx` path** (Cursor, Cline, Codex, advanced Claude Desktop): Python
3.11 or newer plus [`uv`](https://docs.astral.sh/uv/) (which provides `uvx`).
Find the absolute path to `uvx` with `which uvx`; GUI hosts do not read your
shell profile, so MCP configs need the full path.

## Tools

52 tools, grouped by service. All return the same result envelope (see
[Result envelope](#result-envelope)). Call `system_status` first if unsure what
is configured. The server also publishes thirteen named [workflow prompts](#workflow-prompts).

**Cross-service**
- `system_status` - which services are configured/reachable, the Google auth
  method and scopes, whether destructive mode is on, the full tool catalog,
  and the list of registered prompts.

<a name="gsc"></a>
**Google Search Console (18)**

*Workhorses*
- `gsc_list_properties` - properties the credentials can see, with permission
  level and a derived `writable` flag (true for `siteOwner` / `siteFullUser`).
- `gsc_search_analytics` - the workhorse: clicks/impressions/CTR/position by
  dimensions, date range, filters, and `data_state`.
- `gsc_top_queries` / `gsc_top_pages` - convenience top-N wrappers.
- `gsc_compare_periods` - current vs prior window with per-key deltas.
  v0.2.0 added `sort_by`, `sort_dir`, `min_delta_clicks` / `_impressions` /
  `_position`, `anomalies_only` + `sigma_threshold`, and `top` for one-call
  movers / losers / outliers reporting.
- `gsc_inspect_url` - URL Inspection (index verdict, coverage, canonicals).
- `gsc_batch_inspect_urls` - inspect up to 25 URLs, per-URL failures collected.
- `gsc_list_sitemaps` - registered sitemaps and their status.
- `gsc_submit_sitemap` - submit a sitemap (write, un-gated; needs the writable
  scope). Accepts either `sitemap_url` (friendly) or `feedpath` (raw API field).
- `gsc_request_indexing` - request (re)crawl via the Indexing API (write,
  un-gated). Accepts singular `url` or `urls`.

*Query intelligence (v0.2.0)*
- `gsc_query_opportunities` - queries already ranking top N with below-target
  CTR. Title and meta optimization candidates.
- `gsc_query_gaps` - queries that draw impressions but barely any clicks.
  Content opportunity signal.
- `gsc_new_queries` - queries appearing in the current window with no prior
  impressions. Emerging topics.
- `gsc_top_pages_by_query` - which pages rank for a specific query. The
  cannibalization audit input.

*Multi-property + lifecycle (v0.5.0)*
- `gsc_portfolio_summary(days, include?, exclude?)` - multi-property fleet
  view. Per-property one-row summary (clicks, impressions, CTR, position)
  for the last N days, plus a portfolio-level rollup. Honors optional
  `include` / `exclude` filters. The single fastest answer to "how is the
  whole portfolio doing?" across agency or multi-brand setups.
- `gsc_trending_pages(days, limit)` - pages whose impressions grew most over
  the last N days vs the prior N days. Wrapper on `gsc_compare_periods` with
  `dimensions=["page"], sort_by="delta_impressions", sort_dir="desc"`.
- `gsc_decaying_pages(days, limit)` - same wrapper, ascending sort. Pages
  to rescue.
- `gsc_coverage_audit(urls, site_url?)` - heuristic coverage audit. The GSC
  Index Coverage report is not exposed in the API; this tool takes a user-
  supplied URL list (typically pulled from a sitemap) and bulk-inspects
  each, then rolls up verdicts (PASS / PARTIAL / FAIL) and coverage_state
  frequencies.

<a name="content"></a>
**Content intelligence (1, v0.7.0)**
- `content_opportunities(site_url?, days?, count?, impressions_min?)` - ranks
  data-grounded content topics from your own Search Console data: fuses
  CTR-vs-expected gap (curve self-calibrated from your own per-position CTR),
  striking-distance position, demand, and momentum into a transparent
  opportunity score; flags cannibalization. If a GA4 property is configured, it
  also weights each topic by the organic conversions its top page already drives
  (up to +50%), so topics that convert rank higher; `filters_applied.ga4_value_status`
  reports whether that ran and why (`applied` / `no_ga4_property` /
  `ga4_unreachable` / `no_conversions`). Prioritizes demand you already have;
  does not do cold-start keyword research or write the content. Pairs with the
  content workflow prompts below. (GA4 weighting v0.7.3)

<a name="ga4"></a>
**Google Analytics 4 (7)**
- `ga4_run_report` - the workhorse: arbitrary dimensions/metrics/date range,
  optional dimension filter and ordering.
- `ga4_top_landing_pages` - top landing pages, organic-only by default.
- `ga4_traffic_by_channel` - sessions/engagement/conversions by channel group.
- `ga4_organic_search_overview` - organic totals plus a day-by-day trend.
- `ga4_setup_audit(property_id?)` - read-only SEO-measurement-readiness audit:
  web data stream, key events, data retention, content-group dimensions, and
  (v0.7.4) enhanced measurement, internal site search, and Google Signals.
  Severity-graded with a benign exception per finding. Uses the GA4 Admin API
  over REST (analytics.readonly; no extra dependency). (v0.7.0)
- `ga4_site_search(days?, limit?)` - internal site-search query report (a
  direct content-gap signal); honest envelope when no real search terms. (v0.7.1)
- `ga4_landing_page_conversions(days?, organic_only?, limit?)` - organic
  landing pages ranked by conversions. (v0.7.1)

<a name="psi"></a>
**PageSpeed Insights (2)**
- `psi_analyze` - Lighthouse scores, lab Core Web Vitals, and field (CrUX) Core
  Web Vitals for a URL. Defaults to the mobile strategy. Field data carries a
  `field_data_note`: Google is deprecating PSI field data, so use `crux_snapshot`
  / `crux_history` for durable field metrics.
- `psi_opportunities(url, strategy?)` - the actionable Lighthouse "opportunity"
  audits (with estimated savings) plus the SEO-category audits, severity-graded.
  Lab data only. An on-page-basics checklist, not a ranking predictor. (v0.7.1)

<a name="cf"></a>
**Cloudflare (7)**
- `cf_list_zones` - zones the token can see.
- `cf_zone_info` - status, plan, name servers for a zone.
- `cf_list_dns` - DNS records (read-only); useful for verifying canonical host
  and TXT verification records during migrations.
- `cf_web_analytics` - read-only edge Web Analytics (RUM), to compare against
  GA4. Cloudflare returns `host: null` for some sites; pass the `site_tag` to
  look those up explicitly.
- `cf_purge_cache` - purge specific URLs (gated).
- `cf_purge_cache_all` - purge an entire zone (gated + confirm token).
- `cf_settings_audit(zone?)` - read-only audit of SEO-relevant Cloudflare zone
  settings (SSL mode, Always-Use-HTTPS, HSTS, Automatic HTTPS Rewrites, Brotli,
  cache TTL). Severity-graded with a "verify, not fail" discipline because CF
  cannot see the origin; HSTS is never a hard failure. Needs Zone Settings Read
  on the token. (v0.7.1)

<a name="indexnow"></a>
**IndexNow (2, v0.2.0)**
- `indexnow_submit(url)` - submit a single URL to Bing, Yandex, Naver, Seznam,
  Yep. Complements (does not replace) `gsc_request_indexing`, which only talks
  to Google. Requires `SEO_MCP_INDEXNOW_KEY` plus a verification file at
  `https://<your-host>/<key>.txt` (see [IndexNow setup](#indexnow) for the full
  key + file format + same-host rules).
- `indexnow_bulk_submit(urls)` - up to 10,000 URLs sharing one host in a
  single POST. Mixed-host batches are rejected client-side with
  `INVALID_INPUT` before any network call. The `SEO_MCP_INDEXNOW_KEY_LOCATION`
  env var overrides the default verification-file URL when your CDN rewrites
  `/key.txt` paths.

<a name="technical"></a>
**Technical SEO (7, v0.3.0)** - no credentials needed; built-in HTTP client.
- `inspect_meta(url)` - on-page surface in one call: title, meta description,
  meta robots, canonical, Open Graph + Twitter Card tags, hreflang, H1 count.
- `check_canonical(url)` - canonical-link audit: self-referential / cross-host
  / protocol-mismatched / trailing-slash drift / canonical target reachable.
- `mixed_content_check(url)` - parses an HTTPS page and flags any `http://`
  references (img / script / iframe / form action / srcset). No-op for `http://`.
- `redirect_chain_audit(url, max_redirects=10)` - walks the chain hop by hop.
  Flags long chains, protocol downgrades, loops, non-2xx terminus.
- `robots_txt_validate(site_url, probes?)` - parses robots.txt (per-group
  rules + sitemaps), optionally verdicts (user_agent, url) probes using RFC
  9309 longest-match (matches what Google + Bing actually do, not stdlib's
  first-match).
- `sitemap_validate(sitemap_url)` - validates a sitemap or sitemap-index XML,
  counts entries, flags oversize + cross-host + missing lastmod. `.gz`
  transparent.
- `sitemap_health(sitemap_url, sample_size=25)` - sample-HEAD audit. Status
  histogram + first non-2xx examples.

<a name="crux"></a>
**Chrome UX Report (2)**
- `crux_history(url? | origin?, form_factor?, metrics?)` - 25 weeks of p75
  Core Web Vitals via the CrUX History API. Reuses `PSI_API_KEY`; works
  anonymously at a tighter rate limit when no key is configured.
- `crux_snapshot(url? | origin?, form_factor?)` - the current p75 Core Web
  Vitals (point-in-time, vs the history). Each metric reports a category
  (GOOD / NEEDS_IMPROVEMENT / POOR); the rolled-up rating is `overall_category`.
  Time metrics use `p75_ms`; the unitless CLS uses `p75`. Small origins return
  a `no_data` envelope. (v0.7.1)

**Structured data + cross-site (5, v0.4.0)** - no new credentials.
- `inspect_schema(url)` - extract every JSON-LD block from a page; report
  the schema.org @type counts and a sample entity per type.
- `validate_schema(url, types?)` - verdict each JSON-LD entity against the
  Google Rich Results required-field set. Covers Article, NewsArticle,
  BlogPosting, Product, FAQPage, BreadcrumbList, Organization, LocalBusiness,
  Event, Review, Recipe. Per-entity verdict plus missing_required and
  missing_recommended lists.
- `hreflang_consistency_check(urls)` - cross-page hreflang audit on a
  user-supplied URL set. Flags missing reciprocity, broken hreflang
  targets, duplicate hreflang on one page, missing self-link, missing
  x-default when there are 3+ language variants.
- `internal_link_graph(start_url, max_depth=2, max_pages=50)` - small
  BFS crawl within the same host. Per-page in-degree + out-degree,
  orphan pages, broken internal links, depth distribution. Hard caps
  (max_depth <= 4, max_pages <= 200) so a misuse never melts the host.
- `lighthouse_budget(url, budget)` - wraps `psi_analyze` and verdicts
  the results against a budget dict, e.g.
  `{performance: 80, LCP_ms: 2500, CLS: 0.1}`. Per-metric pass/fail and
  an overall verdict. Useful as a CI / pre-deploy gate inside an LLM
  session. Reuses `PSI_API_KEY`.

Every tool's `tools/list` entry carries the MCP standard annotations
(`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) so MCP
hosts can decide what to auto-approve and what to confirm.

## Workflow prompts

The server publishes thirteen named MCP prompts (via `prompts/list` /
`prompts/get`) that chain the granular tools into common SEO workflows. Hosts
that surface prompts (Claude Desktop's slash menu, Cursor's command palette,
Cline's prompt picker) advertise them automatically.

| Prompt | Arguments | Chains |
|---|---|---|
| `post_deploy_verify` | `urls`, `zone?`, `skip_psi?` | `cf_purge_cache` -> `gsc_request_indexing` -> `indexnow_bulk_submit` -> `psi_analyze` |
| `weekly_review` | `days?`, `site_url?` | `gsc_compare_periods` (gainers + losers via sort_dir) -> `gsc_query_opportunities` -> `gsc_query_gaps` -> `ga4_organic_search_overview` |
| `content_audit` | `site_url?`, `days?`, `top_n_queries?` | `gsc_top_queries` -> per-query `gsc_top_pages_by_query` -> cannibalization recommendation |
| `migration_check` | `urls`, `site_url?` | `gsc_batch_inspect_urls` -> `gsc_list_sitemaps` -> canonical-agreement table -> remediation list |
| `technical_seo_audit` | `url` | `inspect_meta` -> `check_canonical` -> `redirect_chain_audit` -> `mixed_content_check` -> `robots_txt_validate` -> `sitemap_health` -> severity-ranked triage list |
| `structured_data_audit` | `urls` | per-URL `inspect_schema` -> `validate_schema` -> (if 2+ URLs) `hreflang_consistency_check` -> per-URL + cross-URL report |
| `pre_deploy_check` | `urls` | `robots_txt_validate` -> per-URL `inspect_meta` -> `check_canonical` -> `validate_schema` -> `redirect_chain_audit` -> `mixed_content_check` -> deploy-gate verdict (block on critical issues, approve otherwise) |
| `content_brief` (v0.7.1) | `topic`, `target_query`, `site_url?` | `gsc_top_pages_by_query` -> `inspect_meta` / `inspect_schema` on top rankers -> brief with required sections + validation rules |
| `content_outline` (v0.7.1) | `brief` | outline with rules: >=5 H2, >=70% target-query coverage, H1 has the primary keyword |
| `content_article` (v0.7.1) | `outline`, `brief` | article with rules: word count within +/-15%, per-section minimum, internal links, inline JSON-LD hint, no em-dashes |
| `content_workflow` (v0.7.1) | `site_url?`, `days?` | `content_opportunities` -> brief -> outline -> article -> `pre_deploy_check` -> `gsc_request_indexing` + `indexnow_submit` -> scheduled `content_performance` |
| `content_performance` (v0.7.1) | `url`, `target_queries?`, `site_url?` | `gsc_compare_periods` + `gsc_search_analytics` before / after the publish window |
| `seo_setup_audit` (v0.7.1) | `site_url?`, `property_id?` | `ga4_setup_audit` -> `cf_settings_audit` -> `psi_opportunities` -> `robots_txt_validate` -> consolidated stack-config report |

Why prompts and not megatools: composability. A failed step inside a megatool
poisons the megatool's envelope and the host loses the ability to retry just
the failing leg. Prompts hand the host a recipe; each step's envelope arrives
intact at the LLM.

## Install

SEOMonster ships **two install paths**, both fully local:

- **`.mcpb` bundle** for Claude Desktop. One-click install, GUI form for
  credentials, secret-typed inputs stored in the OS keychain. Recommended for
  most users.
- **`uvx`** for Cursor, Cline, Codex, and Claude Desktop power users who prefer
  to hand-edit MCP config files.

Both paths run the same Python package (`seo_mcp`) and expose the same
52-tool surface. The difference is only how the host launches the server
and how it collects credentials.

### Claude Desktop (recommended): `.mcpb` bundle

Three short steps. The OAuth consent is **run once from a terminal** (the GUI
flow inside Claude Desktop's MCP subprocess times out before a real user can
finish; see [Why pre-flight auth?](#why-pre-flight-auth) below).

**1. Install the bundle.** Download
[`seo-monster-0.2.0.mcpb`](https://github.com/avansaber/seo-monster/releases/latest)
from GitHub releases (or, when listed, from the [Claude
Directory](https://claude.ai/directory)) and double-click it. Claude Desktop
verifies the bundle, runs `uv` to materialize the Python environment, and
shows a configuration form:

| Field                          | Type           | Required | Notes                                                                 |
|--------------------------------|----------------|----------|-----------------------------------------------------------------------|
| Google OAuth Client Secrets    | file picker    | yes      | Desktop-app client-secrets JSON from Google Cloud Console.            |
| Google OAuth Token Cache Path  | string         | yes      | Defaults to `~/.config/seo-monster/token.json`. Written on consent.   |
| GSC Default Property           | string         | no       | e.g. `sc-domain:example.com` or `https://www.example.com/`.           |
| GA4 Default Property ID        | string         | no       | `properties/123456789` or bare `123456789`.                           |
| PageSpeed Insights API Key     | string, secret | no       | Stored in the OS keychain. **Strongly recommended** ([why?](#pagespeed-insights)). |
| Cloudflare API Token           | string, secret | no       | Stored in the OS keychain. Required only for the Cloudflare tools.    |
| Cloudflare Default Zone        | string         | no       | e.g. `example.com`.                                                   |
| IndexNow Key                   | string, secret | no       | Required only for IndexNow tools. Any 8-128 hex string you generate.  |
| IndexNow Key File URL          | string         | no       | Override the default verification location (`https://<host>/<key>.txt`). |

Fill the fields, click **Save**, then **toggle the extension on**. Quit Claude
Desktop completely (⌘Q on macOS) and reopen.

**2. Run the one-time OAuth consent from a terminal.** Before using any
Google-backed tool, run:

```sh
uvx seo-monster auth
```

A browser opens. Approve the requested scopes. The command writes
`token.json` to the path you configured (default `~/.config/seo-monster/token.json`)
with `0600` permissions, then exits. This step is the recommended pattern; it
sidesteps the timeout that Claude Desktop imposes on every tool call.

**3. Start a new chat in Claude Desktop and use the tools.** Click the 🔧
tools icon in the input box; you should see 52 SEOMonster tools. Try
`system_status` first to verify everything is configured.

#### Why pre-flight auth?

The OAuth installed-app flow opens a local browser and waits for the user to
finish the consent screen. Inside Claude Desktop, MCP servers are launched as
subprocesses whose tool calls have a ~30-60 second timeout. Real users do not
complete browser consent that fast, so the originating call times out, and
since every Google tool retries the flow until a token exists, every call
times out in turn. Running `uvx seo-monster auth` once from a terminal puts
the token on disk; from that point on, Claude Desktop's MCP server just reads
the cached token and silently refreshes it as needed.

### `uvx` for Cursor, Cline, Codex (and Claude Desktop power users)

`uvx` runs the published PyPI package `seo-monster` in an ephemeral
environment. Add the snippet for your host below, using the **absolute path**
to `uvx` (find it with `which uvx`; GUI hosts do not read your shell profile).

#### Cursor (`~/.cursor/mcp.json` or project `.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "seomonster": {
      "command": "/Users/me/.local/bin/uvx",
      "args": ["seo-monster"],
      "env": {
        "SEO_MCP_GOOGLE_OAUTH_CLIENT": "/Users/me/.config/seo-monster/client_secret.json",
        "SEO_MCP_GOOGLE_TOKEN": "/Users/me/.config/seo-monster/token.json",
        "SEO_MCP_GA4_PROPERTY_ID": "properties/123456789",
        "PSI_API_KEY": "AIza...",
        "CF_API_TOKEN": "..."
      }
    }
  }
}
```

#### Cline (`cline_mcp_settings.json`)

```json
{
  "mcpServers": {
    "seomonster": {
      "command": "/Users/me/.local/bin/uvx",
      "args": ["seo-monster"],
      "env": {
        "SEO_MCP_GOOGLE_OAUTH_CLIENT": "/Users/me/.config/seo-monster/client_secret.json",
        "SEO_MCP_GOOGLE_TOKEN": "/Users/me/.config/seo-monster/token.json"
      },
      "alwaysAllow": ["system_status", "gsc_search_analytics", "ga4_run_report", "psi_analyze"]
    }
  }
}
```

`alwaysAllow` lists read tools so Cline does not prompt on each call. Leave the
cache-purge tools off so they always prompt.

#### Codex (`~/.codex/config.toml`)

```toml
[mcp_servers.seomonster]
command = "/Users/me/.local/bin/uvx"
args = ["seo-monster"]

[mcp_servers.seomonster.env]
SEO_MCP_GOOGLE_OAUTH_CLIENT = "/Users/me/.config/seo-monster/client_secret.json"
SEO_MCP_GOOGLE_TOKEN = "/Users/me/.config/seo-monster/token.json"
SEO_MCP_GA4_PROPERTY_ID = "properties/123456789"
```

#### Claude Desktop, direct `uvx` (advanced)

If you prefer to hand-edit `claude_desktop_config.json` instead of using the
`.mcpb` bundle, the same snippet shape as Cursor above works.

<a name="auth"></a>
## Auth

The four services authenticate independently. Configure only the ones you use;
a tool for an unconfigured service returns a clear `AUTH_MISSING` error rather
than failing the server.

### Quick setup (recommended): `seo-monster setup`

Run `seo-monster setup` once from a terminal. It interactively collects your
Cloudflare token, PageSpeed Insights key, IndexNow key, and the default GSC and
GA4 properties, validates what it can against the live APIs, and writes them to
`~/.config/seo-mcp/config.toml` with `0600` permissions. Your MCP host config
then needs no secrets in it:

```json
{ "command": "uvx", "args": ["seo-monster"] }
```

Two things `setup` does not do, by design:

- **Google OAuth** still uses the separate one-time browser step. After `setup`,
  run `seo-monster auth` to complete Google consent (see the next section).
- It never overrides environment variables. Anything set in your host's `env`
  block still wins over the config file, so CI and Docker keep using env vars.

`setup` is re-runnable: existing values are shown as defaults and kept when you
leave a field blank. The sections below document the per-service env vars, which
are what `setup` writes for you and what CI pipelines can set directly.

### Google (Search Console + Analytics 4) - OAuth, recommended

This is the lower-friction path: no Cloud service account, no per-property email
grants.

1. In the [Google Cloud Console](https://console.cloud.google.com/), create (or
   pick) a project and **enable the APIs** you will use:
   - Search Console API
   - Indexing API (for `gsc_request_indexing`)
   - Google Analytics Data API (for the GA4 tools)
   - PageSpeed Insights API (only if you want a PSI key; see below)
2. Create an OAuth client of type **Desktop app** and download the client-secrets
   JSON.
3. Point the server at it and at a writable token path:
   - `SEO_MCP_GOOGLE_OAUTH_CLIENT` = path to the client-secrets JSON
   - `SEO_MCP_GOOGLE_TOKEN` = a writable path where the token will be cached
4. **One-time:** run `uvx seo-monster auth` from a terminal. A browser opens;
   approve the scopes. The command writes `token.json` (`0600`) and exits.
5. Subsequent runs (server-side) refresh the token silently. **The server
   never opens a browser**; if the cached token is missing, tools return
   `AUTH_MISSING` pointing back at the `auth` command.

The signed-in Google account must have access to the Search Console properties
and GA4 properties you query.

**Token-cache hardening.** The cached token is refresh-capable and equivalent
to a long-lived credential for the requested scopes. The server writes it with
`0600` and its parent directory with `0700`. Keep `SEO_MCP_GOOGLE_TOKEN` under
a directory you control (e.g. `~/.config/seo-monster/`) and do not put it on a
shared filesystem.

### Google - service account (advanced, headless)

For fully headless or server deployments where a browser is not available:

1. Create a service account and download its JSON key.
2. Set `SEO_MCP_GOOGLE_CREDENTIALS` (or the standard
   `GOOGLE_APPLICATION_CREDENTIALS`) to the key path.
3. Grant the service-account email access on each property:
   - Search Console: add it as a user on the property.
   - GA4: add it as a Viewer on the property.

If both OAuth and a service account are configured, OAuth is used.

> **Coverage note.** The OAuth installed-app path is exercised in our
> validation pass and in production-style smoke tests. The service-account
> path is documented but not independently validated against a live Cloud
> project. If you hit issues on the SA path, please open an issue.

### Scopes (minimal vs full)

The default consent requests the scopes needed for every tool, including the two
writes:

| Capability                          | Scope                          |
|-------------------------------------|--------------------------------|
| GSC read                            | `webmasters` (covers readonly) |
| GSC sitemap submit                  | `webmasters`                   |
| GSC indexing request                | `indexing`                     |
| GA4 reporting                       | `analytics.readonly`           |

If you only want reads, you can consent to a narrower set
(`webmasters.readonly` + `analytics.readonly`) and simply not call
`gsc_submit_sitemap` / `gsc_request_indexing`; calling a write tool without its
scope returns `SCOPE_INSUFFICIENT` with remediation, never a crash.

### PageSpeed Insights

PSI works without a key in principle, but in practice the **anonymous quota is
shared across every caller without a key and is frequently exhausted**: a
single `psi_analyze` call against the anonymous endpoint often returns
`RATE_LIMITED`. **Treat the anonymous mode as a fallback, not the steady
state.**

To get reliable PSI access:

1. In Cloud Console, enable the **PageSpeed Insights API**.
2. Create an **API key** (Credentials > Create credentials > API key). It
   takes a minute. The key is free.
3. Set `PSI_API_KEY` (or use the field in the `.mcpb` configuration form).

The PSI API only accepts the key as a URL query parameter (not a header), so
treat PSI keys as low-sensitivity. Scope the key to the PageSpeed Insights
API only and attach no other GCP roles.

### Cloudflare

Create an API token at
[dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens)
and set `CF_API_TOKEN` (and optionally `CF_ZONE` for a default zone). Grant only
the permissions you need:

| Permission              | Needed for                          |
|-------------------------|-------------------------------------|
| Zone: `Zone:Read`       | `cf_list_zones`, `cf_zone_info`     |
| Zone: `DNS:Read`        | `cf_list_dns`                       |
| Account: `Account Analytics:Read` | `cf_web_analytics`        |
| Zone: `Cache Purge:Purge` | `cf_purge_cache`, `cf_purge_cache_all` (only if you enable destructive mode) |

### IndexNow

IndexNow notifies Bing, Yandex, Naver, Seznam, and Yep when a URL is created
or updated. Google does not participate, so the IndexNow tools complement
rather than replace `gsc_request_indexing`.

#### One-time setup

1. **Generate a key.** Any 8-128 character string of letters, digits, or
   hyphens (`a-z`, `A-Z`, `0-9`, `-`) per the IndexNow spec. Common patterns:
   a 32-char lowercase hex string (e.g. `python -c "import secrets;
   print(secrets.token_hex(16))"`) or any random alphanumeric of similar
   length. Treat it like an API key; do not commit it.
2. **Configure SEOMonster** by setting `SEO_MCP_INDEXNOW_KEY` to that string
   (or use the `.mcpb` configuration form; the field is marked sensitive and
   lands in the OS keychain).
3. **Host the verification file** at `https://<your-host>/<key>.txt`. The
   file body MUST be **exactly** the key string with no trailing newline, no
   BOM, no extra whitespace, no HTML wrapper. The Content-Type should be
   `text/plain`. Confirm with `curl -i https://<your-host>/<key>.txt` before
   moving on; the response body must be byte-identical to the key.
4. **(Optional)** Set `SEO_MCP_INDEXNOW_KEY_LOCATION` if the verification
   file lives at a non-standard URL (some CDNs rewrite `/key.txt` paths).
   The default location is `https://<host>/<key>.txt` derived from the URLs
   you submit, so you usually do not need this.

#### Same-host constraint

Every URL submitted in one `indexnow_submit` or `indexnow_bulk_submit` call
must share the same host as the verification file. Mixed-host batches are
rejected by IndexNow with HTTP 422; `indexnow_bulk_submit` enforces this
client-side and returns `INVALID_INPUT` before any network call when it
detects mixed hosts.

If you have multiple hosts, host a verification file per host and either
make separate calls per host or override `SEO_MCP_INDEXNOW_KEY_LOCATION`
per call (the tool does not currently expose per-call override; set
distinct env values per session).

#### Common errors

| Symptom | Likely cause |
|---|---|
| `AUTH_INVALID` from `indexnow_submit` | Engines could not fetch `https://<host>/<key>.txt`. Confirm the file returns HTTP 200 with the exact key as the body |
| `INVALID_INPUT` from `indexnow_bulk_submit` mentioning mixed hosts | URL list spans multiple hosts; split into per-host batches |
| `RATE_LIMITED` | Hit IndexNow's per-host rate cap. Wait before retrying |

### Verify your setup

After configuring, call `system_status` to see what is detected. Call it with
`{"probe": true}` to make one cheap live request per configured service and
confirm the credentials actually work (GSC lists properties, GA4 runs a 1-row
report against the default property, Cloudflare lists one zone, PSI pings the
endpoint). With `probe` off (the default) it does a config-only check and makes
no network calls.

<a name="destructive-mode"></a>
## Destructive mode

Cache purges affect every visitor, so they are off by default. Set
`SEO_MCP_ALLOW_DESTRUCTIVE=true` to enable `cf_purge_cache` and
`cf_purge_cache_all`. While off, those tools return `DESTRUCTIVE_DISABLED` and
make no network call.

`cf_purge_cache_all` (purge the whole zone) carries an extra safeguard: it
requires a `confirm` argument equal to the resolved zone hostname. A missing or
mismatched `confirm` returns `CONFIRM_REQUIRED` and issues no purge.

The two GSC writes (`gsc_submit_sitemap`, `gsc_request_indexing`) are **not**
gated; they are routine, low-blast-radius SEO tasks.

<a name="configuration"></a>
## Configuration

Resolution is environment-first, with a TOML file fallback. Environment always
wins. The config file is normally written for you by `seo-monster setup` (with
`0600` permissions); you can also write it by hand or set the env vars below.

| Env var                          | Service | Purpose                                          |
|----------------------------------|---------|--------------------------------------------------|
| `SEO_MCP_GOOGLE_OAUTH_CLIENT`    | Google  | OAuth client-secrets JSON path (recommended).    |
| `SEO_MCP_GOOGLE_TOKEN`           | Google  | Writable cached-token path (OAuth).              |
| `SEO_MCP_GOOGLE_CREDENTIALS`     | Google  | Service-account key path (alternative).          |
| `GOOGLE_APPLICATION_CREDENTIALS` | Google  | Standard service-account fallback.               |
| `SEO_MCP_GSC_DEFAULT_SITE`       | GSC     | Default property, e.g. `sc-domain:example.com`.  |
| `SEO_MCP_GA4_PROPERTY_ID`        | GA4     | Default property, e.g. `properties/123456789`.   |
| `SEO_MCP_DATA_STATE`             | GSC     | `all` (default) or `final`.                      |
| `PSI_API_KEY`                    | PSI     | PageSpeed Insights API key (optional).           |
| `CF_API_TOKEN`                   | CF      | Cloudflare API token.                            |
| `CF_ZONE`                        | CF      | Default zone hostname.                           |
| `SEO_MCP_INDEXNOW_KEY`           | IndexNow| Shared key for the IndexNow tools (sensitive).   |
| `SEO_MCP_INDEXNOW_KEY_LOCATION`  | IndexNow| Override default key-file URL (optional).        |
| `SEO_MCP_ALLOW_DESTRUCTIVE`      | all     | `true` enables cache-purge tools. Default off.   |
| `SEO_MCP_CONFIG`                 | all     | Path to the TOML config file.                    |

Config file fallback at `~/.config/seo-mcp/config.toml` (or `SEO_MCP_CONFIG`):

```toml
[google]
oauth_client = "/Users/me/.config/seo-mcp/client_secret.json"
token        = "/Users/me/.config/seo-mcp/token.json"
# credentials = "/Users/me/.config/seo-mcp/sa.json"   # service-account alternative

[gsc]
default_site = "sc-domain:example.com"
data_state   = "all"

[ga4]
property_id  = "properties/123456789"

[psi]
api_key = "AIza..."

[cloudflare]
api_token = "..."
zone      = "example.com"

[server]
allow_destructive = false
```

<a name="errors"></a>
## Result envelope

Every tool returns the same shape. On success:

```json
{ "ok": true, "data": { /* tool-specific */ }, "error": null }
```

On failure:

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "AUTH_MISSING",
    "service": "gsc",
    "message": "No Google credentials found for Search Console.",
    "remediation": "Configure OAuth ... or a service-account key. See README > Auth.",
    "docs_url": "https://seomonster.avansaber.com#auth",
    "details": null
  }
}
```

Error codes:

| Code                   | Meaning                                                       |
|------------------------|---------------------------------------------------------------|
| `AUTH_MISSING`         | No credential configured for the service.                     |
| `AUTH_INVALID`         | Credential present but rejected (401/403, bad key, expired).  |
| `SCOPE_INSUFFICIENT`   | Token lacks the scope this tool needs.                        |
| `DESTRUCTIVE_DISABLED` | A cache-purge tool was called with destructive mode off.      |
| `CONFIRM_REQUIRED`     | `cf_purge_cache_all` called without a matching `confirm`.     |
| `NOT_FOUND`            | Site / property / zone / record not found or not visible.     |
| `INVALID_INPUT`        | Argument failed validation (bad date, missing required arg).  |
| `RATE_LIMITED`         | Upstream 429.                                                 |
| `SERVICE_DISABLED`     | A Google Cloud API is not enabled; `details` has the activation URL. |
| `UPSTREAM_ERROR`       | Any other non-2xx from an upstream API.                       |

## Development

```sh
git clone https://github.com/avansaber/seo-monster
cd seo-monster
uv venv && uv pip install -e ".[dev]"
uv run pytest               # offline test suite
uv run seo-monster          # run the server over stdio
uv run seo-monster auth     # one-time OAuth consent (or `uv run seo-mcp auth`)
```

The package exposes two console-script aliases: `seo-monster` (canonical,
matches the PyPI distribution) and `seo-mcp` (a v0.1.x dev alias kept for
back-compat). Both invoke the same entry point. As of v0.2.0, invoking the
server via `seo-mcp` emits a one-line stderr deprecation notice; nothing on
stdout, so the MCP protocol channel is unaffected. Production configs should
use `seo-monster`; the alias will be removed in a future major release.

Tests are fully offline: they mock at the client layer, so no network and no
credentials are needed to run them.

> **Server identity note.** Some MCP host UIs display the server name as
> `seo-mcp` and the version as the `mcp` SDK version (e.g. `1.27.1`). The
> server-name string is the value we passed to `Server("seo-mcp")` and is kept
> stable for back-compat; the version readout is a quirk of the SDK
> (`create_initialization_options()` does not propagate the package version).
> The package's real version is in `pyproject.toml` and `seo_mcp.__version__`.

## Changelog

Release-by-release notes, including the validation checks each version's
external testing pass should cover, live in [CHANGELOG.md](CHANGELOG.md).

## Privacy

SEOMonster runs entirely on your machine and talks only to the upstream APIs
you configure. The maintainers do not see any of your data, credentials,
queries, or tool calls. See [PRIVACY.md](PRIVACY.md) for the full statement.

## License

MIT. See [LICENSE](LICENSE).

<!-- mcp-name: io.github.avansaber/seo-monster -->

