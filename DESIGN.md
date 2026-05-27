# seo-mcp Design

An MCP server that exposes strictly SEO-focused tools over four data sources:
Google Search Console (GSC), Google Analytics 4 (GA4), PageSpeed Insights
(PSI), and Cloudflare (CF). End users install it into Claude Desktop, Cline,
Cursor, or Codex, supply their own credentials, and call the tools from the
host AI. All four sources, Cloudflare included, are in scope for v1.

This is the architecture document. The original open questions have been
resolved by the maintainer; their answers are folded into the sections below
and recorded verbatim in "Resolved decisions" at the end. `PLAN.md` carries the
phased build plan that follows from this design.

## Design principles

1. **User-credential-driven.** No auth is baked into the shipped package. Every
   credential is resolved at runtime from the user's environment or config
   file. The package on PyPI contains zero secrets and zero default account
   references.
2. **Read-first, with one narrow gate.** Reads are always available. The two
   routine SEO writes (sitemap submit, indexing request) are available by
   default because they are daily tasks with low blast radius. Only the
   Cloudflare cache-purge tools, which affect every visitor, sit behind the
   `SEO_MCP_ALLOW_DESTRUCTIVE=true` opt-in.
3. **Structured errors over exceptions.** A tool never crashes the server on a
   missing key or an API 403. It returns a typed error object the host AI can
   read and act on (see "Error shape").
4. **Thin client boundary.** All network calls live behind a small client layer
   (`clients/`). Tools call clients; tests replace clients with fakes. No tool
   imports `googleapiclient` or `urllib` directly.
5. **Lean dependencies.** Standard library plus `mcp` plus the Google client
   libraries. Cloudflare and PSI ride on stdlib `urllib` (no extra dep), exactly
   as the reference `cf.py` and `gsc.py` PSI path do.

## Architecture shape

Adopts the tailtest mcp_server layout: a single `Server` instance, one
`@server.list_tools()` registry, one `@server.call_tool()` dispatcher, stdio
transport, and a console-script entry point.

```
seo-mcp/
├── pyproject.toml            # hatchling build, console script "seo-mcp"
├── README.md
├── LICENSE                   # MIT
├── DESIGN.md                 # this file
├── PLAN.md                   # phased plan (created later, after sign-off)
├── .gitignore
├── src/
│   └── seo_mcp/
│       ├── __init__.py       # __version__
│       ├── server.py         # list_tools + call_tool dispatch (stdio)
│       ├── config.py         # env-first, file-fallback resolution
│       ├── auth.py           # Google creds + CF token resolvers, scope logic
│       ├── errors.py         # error-shape helpers + error codes
│       ├── formatting.py     # shared row/number formatting for outputs
│       ├── clients/
│       │   ├── __init__.py
│       │   ├── google_auth.py    # build credentials (SA or OAuth), cache token
│       │   ├── gsc.py            # searchconsole v1 + indexing v3 wrappers
│       │   ├── ga4.py            # Analytics Data API v1 wrappers
│       │   ├── psi.py            # PageSpeed Insights via urllib
│       │   └── cloudflare.py     # CF v4 REST via urllib
│       └── tools/
│           ├── __init__.py
│           ├── system_status.py  # auth-state + capability discovery
│           ├── gsc_tools.py
│           ├── ga4_tools.py
│           ├── psi_tools.py
│           └── cf_tools.py
└── tests/
    ├── conftest.py
    ├── test_smoke.py
    ├── test_config.py
    ├── test_errors.py
    ├── test_system_status.py
    ├── test_gsc_tools.py
    ├── test_ga4_tools.py
    ├── test_psi_tools.py
    └── test_cf_tools.py
```

Dispatch mirrors tailtest: `call_tool` looks up the name, imports the handler
lazily, calls it with the validated arguments, and wraps the dict result in a
single `TextContent` carrying `json.dumps(result, indent=2)`.

## Tool surface

Naming convention: `<service>_<verb_noun>`, except `system_status` which spans
all services. Each tool returns the standard envelope (see "Output envelope").

Tools are grouped into three trust tiers:

- **read** - safe, always available.
- **write** - mutates external state with low blast radius (sitemap submit,
  indexing request). Available by default, no gate. These are routine SEO tasks.
- **write-gated** - mutates state visible to all site visitors (Cloudflare cache
  purge). Inert unless `SEO_MCP_ALLOW_DESTRUCTIVE=true`, and `cf_purge_cache_all`
  carries an extra confirm token (see CF section).

### Cross-service

#### `system_status`  (tier: read)

The "call this first" tool, modeled on mcp-gsc's `get_capabilities`. Reports
which services are configured and reachable, which scopes the Google token
carries, whether destructive mode is on, and the full tool catalog grouped by
service. Does not make billable API calls beyond a cheap reachability probe per
configured service (e.g. GSC `sites.list`, CF `/zones?per_page=1`); probes are
opt-in via the `probe` flag to keep the default call free.

- **input**
  ```json
  {
    "type": "object",
    "properties": {
      "probe": {
        "type": "boolean",
        "description": "If true, make one cheap live call per configured service to confirm the credentials actually work. Default false (config-only check)."
      }
    },
    "additionalProperties": false
  }
  ```
- **output** (`data` field of the envelope)
  ```json
  {
    "version": "0.1.0",
    "destructive_enabled": false,
    "services": {
      "gsc":  {"configured": true,  "auth_method": "oauth", "scopes": ["webmasters","indexing"], "reachable": true,  "default_site": "sc-domain:example.com"},
      "ga4":  {"configured": true,  "auth_method": "oauth", "reachable": null, "default_property": "properties/123456789"},
      "psi":  {"configured": true,  "auth_method": "api_key", "reachable": null},
      "cf":   {"configured": false, "auth_method": null, "reachable": null}
    },
    "tools": {
      "gsc": ["gsc_list_properties", "gsc_search_analytics", "..."],
      "ga4": ["ga4_run_report", "..."],
      "psi": ["psi_analyze"],
      "cf":  ["cf_list_zones", "..."]
    }
  }
  ```
  `reachable: null` means "not probed". `configured: false` means no credential
  was found for that service, and its tools will return `AUTH_MISSING` if called.

### Google Search Console

Wraps `searchconsole v1` and `indexing v3`, following the call patterns in the
reference `gsc.py`.

#### `gsc_list_properties`  (tier: read)
Lists every property the credentials can see, with permission level. Maps to
`sites().list()`.
- **input**: `{}`
- **output**: `{"properties": [{"site_url": "sc-domain:example.com", "permission_level": "siteOwner"}]}`

#### `gsc_search_analytics`  (tier: read)
The workhorse. Maps to `searchanalytics().query()`. Exposes dimensions, date
range, row limit, filters, and `data_state`. Defaults `data_state` to the
configured value (`all` matches the GSC dashboard, mcp-gsc convention).
- **input**
  ```json
  {
    "type": "object",
    "properties": {
      "site_url":   {"type": "string", "description": "Property to query. Defaults to configured default site."},
      "start_date": {"type": "string", "description": "ISO date (YYYY-MM-DD). Defaults to 28 days ago."},
      "end_date":   {"type": "string", "description": "ISO date. Defaults to today."},
      "dimensions": {"type": "array", "items": {"type": "string", "enum": ["query","page","country","device","searchAppearance","date"]}, "description": "Defaults to [\"query\"]."},
      "row_limit":  {"type": "integer", "minimum": 1, "maximum": 25000, "description": "Defaults to 1000."},
      "start_row":  {"type": "integer", "minimum": 0, "description": "Pagination offset. Defaults to 0."},
      "search_type":{"type": "string", "enum": ["web","image","video","news","discover","googleNews"], "description": "Defaults to web."},
      "data_state": {"type": "string", "enum": ["all","final"], "description": "all matches the dashboard (includes fresh, partial data); final lags 2-3 days. Defaults to configured value."},
      "filters":    {"type": "array", "items": {"type": "object", "properties": {"dimension": {"type": "string"}, "operator": {"type": "string", "enum": ["equals","notEquals","contains","notContains","includingRegex","excludingRegex"]}, "expression": {"type": "string"}}, "required": ["dimension","operator","expression"]}, "description": "Optional dimension filters, ANDed together."}
    },
    "additionalProperties": false
  }
  ```
- **output**: `{"site_url": "...", "start_date": "...", "end_date": "...", "data_state": "all", "row_count": 142, "rows": [{"keys": ["..."], "clicks": 12, "impressions": 340, "ctr": 0.035, "position": 8.4}]}`

#### `gsc_top_queries`  (tier: read)
Convenience wrapper over `gsc_search_analytics` with `dimensions=["query"]`.
Same shape as the reference `cmd_queries`. Inputs: `site_url`, `days` (default
28), `limit` (default 50).

#### `gsc_top_pages`  (tier: read)
Convenience wrapper with `dimensions=["page"]`. Mirrors `cmd_pages`. Same inputs
as `gsc_top_queries`.

#### `gsc_compare_periods`  (tier: read)
Runs two `searchanalytics().query()` calls (current vs prior window) and returns
per-row deltas in clicks/impressions/ctr/position. Inputs: `site_url`,
`dimensions` (default `["query"]`), `current_days` (default 28), `gap_days`
(default 0). Output adds `delta_*` fields per matched key plus an `unmatched`
bucket for keys present in only one window.

#### `gsc_inspect_url`  (tier: read)
Maps to `urlInspection().index().inspect()`. Returns verdict, coverageState,
crawledAs, lastCrawlTime, indexingState, pageFetchState, plus mobile-usability
and rich-results summaries when present.
- **input**: `{"url": "...", "site_url": "..." (optional, defaults to configured)}`
- **output**: `{"url": "...", "verdict": "PASS", "coverage_state": "Submitted and indexed", "crawled_as": "MOBILE", "last_crawl_time": "...", "indexing_state": "...", "page_fetch_state": "..."}`

#### `gsc_batch_inspect_urls`  (tier: read)
Inspects a list of URLs sequentially with light rate-limit handling (mcp-gsc
parity). Inputs: `urls` (array, capped at e.g. 25 per call), `site_url`. Output:
array of inspection results plus a `failed` list with per-URL error reasons.

#### `gsc_list_sitemaps`  (tier: read)
Maps to `sitemaps().list()`. Returns path, lastSubmitted, lastDownloaded,
isPending, isSitemapsIndex, and per-content submitted/indexed counts. Mirrors
`cmd_sitemaps`.

#### `gsc_submit_sitemap`  (tier: write, un-gated)
Maps to `sitemaps().submit()`. Requires the `webmasters` (writable) scope.
Available by default (no destructive flag needed). Inputs: `feedpath` (full
sitemap URL), `site_url`.

#### `gsc_request_indexing`  (tier: write, un-gated)
Maps to `indexing v3` `urlNotifications().publish()` with `type=URL_UPDATED`.
Requires the `indexing` scope. Available by default. Carries the reference
`gsc.py` hint logic: if the API returns `ACCESS_TOKEN_SCOPE_INSUFFICIENT` or
`SERVICE_DISABLED`, the error object includes the remediation text and (when
present) the activation URL. Inputs: `urls` (array, capped), `site_url`.

### Google Analytics 4

Wraps the Analytics Data API v1 (`runReport`). This surface is a fresh proposal:
neither reference file covers GA4. Auth shares the Google credential resolver
but needs the `analytics.readonly` scope (and the service account, if used, must
be added as a Viewer on the GA4 property). The tools are scoped to
SEO-meaningful reporting only (no realtime, no audience exports, no admin
mutations).

#### `ga4_run_report`  (tier: read)
The workhorse. Maps to `runReport`. Generic dimensions/metrics/date-range query.
- **input**
  ```json
  {
    "type": "object",
    "properties": {
      "property_id": {"type": "string", "description": "GA4 property, e.g. \"properties/123456789\" or bare \"123456789\". Defaults to configured property."},
      "start_date":  {"type": "string", "description": "ISO date or GA4 relative (e.g. \"28daysAgo\"). Defaults to 28daysAgo."},
      "end_date":    {"type": "string", "description": "ISO date or \"today\". Defaults to today."},
      "dimensions":  {"type": "array", "items": {"type": "string"}, "description": "GA4 dimension API names, e.g. [\"pagePath\",\"sessionDefaultChannelGroup\"]. Defaults to [\"date\"]."},
      "metrics":     {"type": "array", "items": {"type": "string"}, "description": "GA4 metric API names, e.g. [\"sessions\",\"screenPageViews\"]. Defaults to [\"sessions\"]."},
      "row_limit":   {"type": "integer", "minimum": 1, "maximum": 100000, "description": "Defaults to 1000."},
      "dimension_filter": {"type": "object", "description": "Optional GA4 FilterExpression-shaped object (simple field/stringFilter form documented in README)."},
      "order_by":    {"type": "object", "description": "Optional ordering, e.g. {\"metric\": \"sessions\", \"desc\": true}."}
    },
    "additionalProperties": false
  }
  ```
- **output**: `{"property_id": "...", "start_date": "...", "end_date": "...", "dimension_headers": [...], "metric_headers": [...], "row_count": 100, "rows": [{"dimensions": ["..."], "metrics": [12, 340]}]}`

#### `ga4_top_landing_pages`  (tier: read)
Convenience: `dimensions=["landingPagePlusQueryString"]`,
`metrics=["sessions","engagementRate","conversions"]`, optionally filtered to
organic search via `sessionDefaultChannelGroup == "Organic Search"`. Inputs:
`property_id`, `days` (default 28), `organic_only` (default true), `limit`.

#### `ga4_traffic_by_channel`  (tier: read)
Convenience: `dimensions=["sessionDefaultChannelGroup"]`,
`metrics=["sessions","engagedSessions","conversions"]`. Lets the AI separate
organic from paid/referral at a glance. Inputs: `property_id`, `days`, `limit`.

#### `ga4_organic_search_overview`  (tier: read)
Convenience: SEO landing-page health. Pulls sessions, engagementRate,
averageSessionDuration, conversions for organic-search traffic over the window,
plus a day-by-day trend. Inputs: `property_id`, `days` (default 28).

(Property listing is intentionally omitted from v1; see Open Question 3. It
requires the GA4 Admin API and a second scope.)

### PageSpeed Insights

Wraps the PSI v5 `runPagespeed` endpoint via stdlib `urllib`, following the
reference `cmd_psi`. Uses a simple API key (optional but recommended to avoid
tight anonymous rate limits).

#### `psi_analyze`  (tier: read)
- **input**
  ```json
  {
    "type": "object",
    "properties": {
      "url":        {"type": "string", "description": "Page URL to analyze. Required."},
      "strategy":   {"type": "string", "enum": ["mobile","desktop"], "description": "Defaults to mobile (Google ranks on mobile)."},
      "categories": {"type": "array", "items": {"type": "string", "enum": ["performance","accessibility","best-practices","seo"]}, "description": "Defaults to all four."}
    },
    "required": ["url"],
    "additionalProperties": false
  }
  ```
- **output**: lab + field split, matching the reference output structure
  ```json
  {
    "url": "...", "strategy": "mobile",
    "lighthouse_scores": {"performance": 87, "accessibility": 95, "best-practices": 92, "seo": 100},
    "lab_core_web_vitals": {"LCP": "2.1 s", "CLS": "0.02", "TBT": "120 ms", "FCP": "1.4 s", "speed_index": "...", "TTI": "..."},
    "field_core_web_vitals": {"overall_category": "FAST", "LCP": {"p75_ms": 2100, "category": "FAST"}, "INP": {"p75_ms": 180, "category": "FAST"}, "CLS": {"p75": 0.02, "category": "FAST"}, "TTFB": {"p75_ms": 600, "category": "AVERAGE"}},
    "field_data_available": true
  }
  ```
  When CrUX field data is absent (low traffic site), `field_data_available` is
  `false` and `field_core_web_vitals` is `null`, mirroring the reference.

### Cloudflare (optional)

Wraps the CF v4 REST API via stdlib `urllib`, following the reference `cf.py`.
Scoped to SEO-relevant operations only: zone discovery, zone status, DNS read
(for verifying canonical host and verification records during migrations),
read-only Web Analytics (edge traffic, comparable against GA4), and cache purge
(the SEO action after fixing on-page content or meta tags). DNS writes, Workers
management, and Web Analytics create/delete are out of SEO scope and excluded.

#### `cf_list_zones`  (tier: read)
Maps to `GET /zones`. Returns name, status, plan, id.

#### `cf_zone_info`  (tier: read)
Maps to `GET /zones/{id}`. Resolves the zone by hostname first (reference
`resolve_zone_id`). Returns status, plan, paused, name servers, created/modified.
Input: `zone` (hostname; defaults to configured `CF_ZONE`).

#### `cf_list_dns`  (tier: read)
Maps to `GET /zones/{id}/dns_records`. Read-only. Useful for confirming the
canonical host, CNAME flattening, and TXT verification records during SEO
migrations. Input: `zone`, optional `type` filter.

#### `cf_web_analytics`  (tier: read)
Read-only Cloudflare Web Analytics (RUM). Lists the account's Web Analytics
sites and their edge traffic so the AI can compare edge-measured visits against
GA4 sessions (bot/edge traffic that GA4's JS tag never sees). Resolves the
account ID from a visible zone (reference `cf.py` `get_account_id`). Maps to the
account RUM `site_info/list` and `site_info/{tag}` reads. Create/update/delete
RUM endpoints are deliberately excluded. Input: `host_or_tag` (optional; when
given, returns that one site's detail, else lists all).

#### `cf_purge_cache`  (tier: write-gated)
Maps to `POST /zones/{id}/purge_cache` with `{"files": [...]}`. Requires
`SEO_MCP_ALLOW_DESTRUCTIVE=true`. Input: `zone`, `urls` (array, required,
non-empty). This is the primary gated SEO action: purge specific URLs after
publishing corrected content so search crawlers refetch.

#### `cf_purge_cache_all`  (tier: write-gated, extra confirm)
Maps to `POST /zones/{id}/purge_cache` with `{"purge_everything": true}`.
Requires destructive mode AND an explicit `confirm` field equal to the zone
hostname, so the AI cannot trigger a full-zone purge without an unambiguous
instruction. Input: `zone`, `confirm` (must equal the resolved zone name).

## Auth model

### Credential resolution order (per service)

1. Environment variable(s).
2. Config file (path from `SEO_MCP_CONFIG`, else `~/.config/seo-mcp/config.toml`).
3. Not configured: the service's tools return `AUTH_MISSING`.

Environment always wins over the file, matching both reference tools.

### Google (GSC + GA4 + PSI-key share one Google account context)

Two supported methods, selected automatically by what is present. OAuth is the
primary, recommended path; the service account is the advanced headless option.

- **OAuth installed-app (recommended, documented first).** The user supplies
  `SEO_MCP_GOOGLE_OAUTH_CLIENT` (client-secrets JSON) and a writable
  `SEO_MCP_GOOGLE_TOKEN` path. First use triggers the browser consent flow
  (reference `gsc.py` pattern) and caches the token; later runs refresh the
  token silently with no further prompts. The `uvx` browser flow is smooth in
  current hosts (mcp-gsc parity), and the setup bar is low: no Cloud Console
  service-account creation, no per-property email grants. This is what the
  README leads with.
- **Service account (advanced, headless).** The user creates a Google Cloud
  service account, downloads the JSON key, points `SEO_MCP_GOOGLE_CREDENTIALS`
  (or standard `GOOGLE_APPLICATION_CREDENTIALS`) at it, and adds the service
  account email as a user on each GSC property and as a Viewer on each GA4
  property. No browser and no token refresh, which suits fully headless or
  server deployments. Higher setup friction, so it is documented as the
  secondary path. Fully supported, not second-class in code: the credential
  resolver treats both as first-class and picks whichever is configured.

Scopes are requested by feature, smallest set that covers the enabled tools:

| Capability                         | Scope                                            |
|------------------------------------|--------------------------------------------------|
| GSC read (analytics, inspect, list)| `webmasters.readonly`                            |
| GSC sitemap submit (write)         | `webmasters`                                     |
| GSC indexing request (write)       | `indexing`                                       |
| GA4 reporting                      | `analytics.readonly`                             |

Because sitemap submit and indexing request are available by default, the
standard consent requests `webmasters` (which covers read), `indexing`, and
`analytics.readonly`. The README documents how to drop to a read-only consent
(`webmasters.readonly` + `analytics.readonly`) for users who want least
privilege and do not need the two write tools; calling a write tool without its
scope returns `SCOPE_INSUFFICIENT` with remediation, never a crash.

### PageSpeed Insights

Simple API key, independent of the OAuth/SA choice. `PSI_API_KEY` env var or
config file. If absent, `psi_analyze` still works against the anonymous endpoint
but the error path explains the rate-limit tradeoff and links to key creation
(reference `_psi_key` behavior, softened to a warning rather than a hard exit).

### Cloudflare

Bearer API token, no OAuth ever (reference `cf.py`). `CF_API_TOKEN` env var or
config file. Optional `CF_ZONE` default. The README documents the minimal token
permissions: `Zone:Read` and `DNS:Read` for the read tools, `Account Analytics:
Read` for `cf_web_analytics`, and `Cache Purge:Purge` only if the user wants the
gated purge tools.

### Error shape

Every tool returns the same envelope. On failure, `ok` is `false` and `error`
is populated; `data` is `null`.

```json
{
  "ok": false,
  "data": null,
  "error": {
    "code": "AUTH_MISSING",
    "service": "gsc",
    "message": "No Google credentials found for Search Console.",
    "remediation": "Configure OAuth: set SEO_MCP_GOOGLE_OAUTH_CLIENT to your client-secrets JSON and SEO_MCP_GOOGLE_TOKEN to a writable token path, then re-run to consent in the browser. Or use a service-account key via SEO_MCP_GOOGLE_CREDENTIALS. See README > Auth.",
    "docs_url": "https://github.com/avansaber/seo-mcp#auth",
    "details": null
  }
}
```

Error codes (closed set):

| code                    | meaning                                                        |
|-------------------------|----------------------------------------------------------------|
| `AUTH_MISSING`          | No credential configured for the service.                      |
| `AUTH_INVALID`          | Credential present but rejected (401/403, bad key, expired).   |
| `SCOPE_INSUFFICIENT`    | Token lacks the scope this tool needs (e.g. indexing).         |
| `DESTRUCTIVE_DISABLED`  | CF cache-purge tool called while `SEO_MCP_ALLOW_DESTRUCTIVE` is off. |
| `CONFIRM_REQUIRED`      | `cf_purge_cache_all` called without matching `confirm`.        |
| `NOT_FOUND`             | Site/property/zone/record not found or not visible to creds.   |
| `INVALID_INPUT`         | Argument failed validation beyond JSON-schema (e.g. bad date). |
| `RATE_LIMITED`          | Upstream 429; includes retry hint in `details` when available. |
| `SERVICE_DISABLED`      | Google Cloud API not enabled; `details` carries activation URL.|
| `UPSTREAM_ERROR`        | Any other non-2xx from an upstream API; `details` has the body.|

On success:

```json
{ "ok": true, "data": { /* tool-specific, shapes above */ }, "error": null }
```

This envelope is the contract the host AI reads, so it is stable across all
tools and documented once in the README.

## Configuration shape

Hybrid, env-first with a TOML file fallback. Full env surface:

| Env var                          | Service | Purpose                                                              |
|----------------------------------|---------|----------------------------------------------------------------------|
| `SEO_MCP_GOOGLE_CREDENTIALS`     | Google  | Path to service-account JSON key (primary auth).                     |
| `GOOGLE_APPLICATION_CREDENTIALS` | Google  | Standard fallback if the above is unset.                             |
| `SEO_MCP_GOOGLE_OAUTH_CLIENT`    | Google  | Path to OAuth client-secrets JSON (alternative auth).                |
| `SEO_MCP_GOOGLE_TOKEN`           | Google  | Writable path for the cached OAuth token.                            |
| `SEO_MCP_GSC_DEFAULT_SITE`       | GSC     | Default property, e.g. `sc-domain:example.com`.                      |
| `SEO_MCP_GA4_PROPERTY_ID`        | GA4     | Default property, e.g. `properties/123456789`.                       |
| `SEO_MCP_DATA_STATE`             | GSC     | `all` (default) or `final`.                                          |
| `PSI_API_KEY`                    | PSI     | PageSpeed Insights API key.                                          |
| `CF_API_TOKEN`                   | CF      | Cloudflare API token.                                                |
| `CF_ZONE`                        | CF      | Default zone hostname.                                               |
| `SEO_MCP_ALLOW_DESTRUCTIVE`      | all     | `true` enables write tools. Default off.                             |
| `SEO_MCP_CONFIG`                 | all     | Path to the TOML config file (overrides the default location).       |

Config file fallback at `~/.config/seo-mcp/config.toml` (or `SEO_MCP_CONFIG`):

```toml
[google]
credentials = "/Users/me/.config/seo-mcp/sa.json"   # service-account key
# oauth_client = "/path/to/client_secret.json"       # or the OAuth path
# token        = "/Users/me/.config/seo-mcp/token.json"

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

The config file is convenient for power users who run the server from a
terminal; the env-var path is what the host-app JSON configs use. Both reach the
same resolver in `config.py`.

## Install and distribution

Distributed on PyPI as a `uvx`-runnable package (mcp-gsc path). Console script
`seo-mcp` maps to `seo_mcp.server:main`. The PyPI name targets `seo-mcp`; if that
is taken at publish time, the fallback is `avansaber-seo-mcp` (the `uvx` argument
and the console-script name change accordingly, the import package stays
`seo_mcp`). Snippets below use `seo-mcp`.

`uvx` runs the published package in an ephemeral environment, so there is
nothing to `pip install` and no virtualenv to manage. GUI hosts do not read the
shell profile, so the config must use the **absolute path** to `uvx` (mcp-gsc
documents this same gotcha). Find it with `which uvx`.

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "seo": {
      "command": "/Users/me/.local/bin/uvx",
      "args": ["seo-mcp"],
      "env": {
        "SEO_MCP_GOOGLE_OAUTH_CLIENT": "/Users/me/.config/seo-mcp/client_secret.json",
        "SEO_MCP_GOOGLE_TOKEN": "/Users/me/.config/seo-mcp/token.json",
        "SEO_MCP_GSC_DEFAULT_SITE": "sc-domain:example.com",
        "SEO_MCP_GA4_PROPERTY_ID": "properties/123456789",
        "PSI_API_KEY": "AIza...",
        "CF_API_TOKEN": "..."
      }
    }
  }
}
```

The snippet above uses the recommended OAuth path. For the service-account
alternative, replace the two `SEO_MCP_GOOGLE_OAUTH_CLIENT`/`SEO_MCP_GOOGLE_TOKEN`
entries with a single `SEO_MCP_GOOGLE_CREDENTIALS` pointing at the key JSON. On
first launch with OAuth, the server opens a browser for one-time consent and
writes the token to `SEO_MCP_GOOGLE_TOKEN`; later launches are silent.

### Cursor (`~/.cursor/mcp.json` or project `.cursor/mcp.json`)

Same object shape under `mcpServers`. Cursor reads the identical schema.

### Cline (`cline_mcp_settings.json`)

```json
{
  "mcpServers": {
    "seo": {
      "command": "/Users/me/.local/bin/uvx",
      "args": ["seo-mcp"],
      "env": {
        "SEO_MCP_GOOGLE_OAUTH_CLIENT": "/Users/me/.config/seo-mcp/client_secret.json",
        "SEO_MCP_GOOGLE_TOKEN": "/Users/me/.config/seo-mcp/token.json"
      },
      "alwaysAllow": ["system_status", "gsc_search_analytics", "ga4_run_report", "psi_analyze"]
    }
  }
}
```

`alwaysAllow` lists read tools so Cline does not prompt on every call; write
tools are deliberately left off so they always prompt.

### Codex (`~/.codex/config.toml`)

```toml
[mcp_servers.seo]
command = "/Users/me/.local/bin/uvx"
args = ["seo-mcp"]

[mcp_servers.seo.env]
SEO_MCP_GOOGLE_OAUTH_CLIENT = "/Users/me/.config/seo-mcp/client_secret.json"
SEO_MCP_GOOGLE_TOKEN = "/Users/me/.config/seo-mcp/token.json"
SEO_MCP_GSC_DEFAULT_SITE = "sc-domain:example.com"
```

A local-development path (`uv run seo-mcp` from a clone, or `pip install -e .`)
is documented in the README for contributors, mirroring tailtest's "Path 3".

## Test plan

Tests run offline. The boundary that gets mocked is the client layer, not the
network primitives, so we test our wrappers and tool logic without HTTP.

### Layers and how each is tested

1. **`config.py`** - pure function over an injected env dict and a temp TOML
   file. Tests cover env-wins-over-file, file fallback, missing-everything,
   and the destructive flag parsing. No mocking needed.
2. **`errors.py`** - pure constructors. Tests assert the envelope shape and that
   every code in the closed set has a helper.
3. **Client layer (`clients/`)** - the only modules that touch the network.
   - Google clients (`gsc.py`, `ga4.py`): inject a fake `service` object whose
     chained methods (`searchanalytics().query().execute()`) return canned
     dicts. The `googleapiclient` discovery object is replaced via a builder
     function the client accepts, so no `build()` call happens in tests.
   - `psi.py` and `cloudflare.py`: both use `urllib`. Tests monkeypatch the
     single `_http_get`/`_http_request` helper in each module to return canned
     JSON or raise a synthetic `HTTPError`, covering 200, 401, 403, 404, 429,
     and `SERVICE_DISABLED` bodies.
4. **Tool layer (`tools/`)** - each tool is called with arguments and a fake
   client injected through the dispatcher's seam. Tests assert:
   - happy path returns `ok: true` with the documented `data` shape,
   - missing creds returns `AUTH_MISSING`,
   - a CF cache-purge tool with destructive off returns `DESTRUCTIVE_DISABLED`
     and makes zero client calls,
   - the un-gated GSC writes (`gsc_submit_sitemap`, `gsc_request_indexing`) run
     without the destructive flag,
   - `cf_purge_cache_all` without matching `confirm` returns `CONFIRM_REQUIRED`,
   - upstream 403 maps to `AUTH_INVALID`, 429 to `RATE_LIMITED`, indexing
     scope error to `SCOPE_INSUFFICIENT`.
5. **`system_status`** - tests with various env permutations assert the
   `configured`/`auth_method` matrix and that `probe: false` makes no client
   calls.
6. **Smoke (`test_smoke.py`)** - AST-level checks that mirror tailtest's
   approach: `list_tools` and `call_tool` exist, every tool named in the
   registry has a dispatch branch and a handler, the console script is declared,
   `requires-python` is correct. These run even before the `mcp` SDK is
   installed.

Test deps: `pytest`, `pytest-asyncio` (the `call_tool` handler is async, as in
tailtest). No `responses`/`httpx` needed because we mock at the client seam.

Fixtures live in `conftest.py`: canned GSC/GA4/PSI/CF payloads as Python dicts,
a `fake_env` factory, and a `make_dispatcher(clients=...)` helper that builds the
server's tool dispatch with injected fakes.

## Resolved decisions

The eight open questions from the first draft were settled by the maintainer.
Recorded here so the design's rationale stays auditable.

1. **Primary auth: OAuth.** OAuth installed-app is the recommended, documented-
   first path (the `uvx` browser flow is smooth in current hosts, and it avoids
   the service-account setup friction). Service account stays fully supported as
   the advanced headless option, first-class in code.
2. **Destructive gating: cache purge only.** `gsc_submit_sitemap` and
   `gsc_request_indexing` are un-gated and available by default (routine SEO
   tasks, low blast radius). Only the Cloudflare cache-purge tools sit behind
   `SEO_MCP_ALLOW_DESTRUCTIVE`, and `cf_purge_cache_all` additionally needs a
   matching `confirm` token.
3. **GA4 property listing: deferred.** v1 requires a user-supplied property ID.
   No `ga4_list_properties`, no GA4 Admin API scope.
4. **Cloudflare scope: read-only DNS kept.** Zones, zone info, DNS read, and
   cache purge. DNS writes and Workers stay excluded.
5. **Cloudflare RUM: read-only.** A single `cf_web_analytics` read tool is in.
   RUM create/update/delete are excluded.
6. **PyPI name: `seo-mcp`, fallback `avansaber-seo-mcp`.**
7. **Cloudflare in v1.** Built now, not deferred.
8. **Convenience tools: all kept.** The wrappers (`gsc_top_queries`,
   `gsc_top_pages`, `gsc_compare_periods`, `ga4_top_landing_pages`,
   `ga4_traffic_by_channel`, `ga4_organic_search_overview`) ship alongside the
   generic `gsc_search_analytics` / `ga4_run_report` workhorses, because they
   reduce the AI's chance of mis-constructing GA4/GSC payloads and save context.

## Final tool count (v1)

22 tools: 1 cross-service (`system_status`), 10 GSC, 4 GA4, 1 PSI, 6 CF.

- **GSC (10):** `gsc_list_properties`, `gsc_search_analytics`, `gsc_top_queries`,
  `gsc_top_pages`, `gsc_compare_periods`, `gsc_inspect_url`,
  `gsc_batch_inspect_urls`, `gsc_list_sitemaps`, `gsc_submit_sitemap`,
  `gsc_request_indexing`.
- **GA4 (4):** `ga4_run_report`, `ga4_top_landing_pages`,
  `ga4_traffic_by_channel`, `ga4_organic_search_overview`.
- **PSI (1):** `psi_analyze`.
- **CF (6):** `cf_list_zones`, `cf_zone_info`, `cf_list_dns`, `cf_web_analytics`,
  `cf_purge_cache`, `cf_purge_cache_all`.

8. **Convenience tools vs one workhorse per service.** I proposed several
   convenience wrappers (`gsc_top_queries`, `ga4_top_landing_pages`, etc.) on top
   of the generic `gsc_search_analytics` / `ga4_run_report`. They make the AI's
   job easier but enlarge the surface. Keep all of them, or trim to the generic
   workhorse plus only the highest-value convenience tools?
