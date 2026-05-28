# SEOMonster

SEOMonster is an MCP server for SEO workflows. It exposes strictly SEO-focused
tools over **Google Search Console**, **Google Analytics 4**, **PageSpeed
Insights**, **Cloudflare**, **IndexNow**, the **Chrome UX Report History API**,
and a built-in HTTP client for technical-SEO checks (`inspect_meta`,
`check_canonical`, `redirect_chain_audit`, `mixed_content_check`,
`robots_txt_validate`, `sitemap_validate`, `sitemap_health`), so an AI host
(Claude Desktop, Cline, Cursor, Codex) can query your own data with your own
credentials.

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

36 tools, grouped by service. All return the same result envelope (see
[Result envelope](#result-envelope)). Call `system_status` first if unsure what
is configured. The server also publishes five named [workflow prompts](#workflow-prompts).

**Cross-service**
- `system_status` - which services are configured/reachable, the Google auth
  method and scopes, whether destructive mode is on, the full tool catalog,
  and the list of registered prompts.

**Google Search Console (14)**

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

**Google Analytics 4 (4)**
- `ga4_run_report` - the workhorse: arbitrary dimensions/metrics/date range,
  optional dimension filter and ordering.
- `ga4_top_landing_pages` - top landing pages, organic-only by default.
- `ga4_traffic_by_channel` - sessions/engagement/conversions by channel group.
- `ga4_organic_search_overview` - organic totals plus a day-by-day trend.

**PageSpeed Insights (1)**
- `psi_analyze` - Lighthouse scores, lab Core Web Vitals, and field (CrUX) Core
  Web Vitals for a URL. Defaults to the mobile strategy.

**Cloudflare (6)**
- `cf_list_zones` - zones the token can see.
- `cf_zone_info` - status, plan, name servers for a zone.
- `cf_list_dns` - DNS records (read-only); useful for verifying canonical host
  and TXT verification records during migrations.
- `cf_web_analytics` - read-only edge Web Analytics (RUM), to compare against
  GA4. Cloudflare returns `host: null` for some sites; pass the `site_tag` to
  look those up explicitly.
- `cf_purge_cache` - purge specific URLs (gated).
- `cf_purge_cache_all` - purge an entire zone (gated + confirm token).

**IndexNow (2, v0.2.0)**
- `indexnow_submit(url)` - submit a single URL to Bing, Yandex, Naver, Seznam,
  Yep. Complements (does not replace) `gsc_request_indexing`, which only talks
  to Google. Requires `SEO_MCP_INDEXNOW_KEY` plus a verification file at
  `https://<your-host>/<key>.txt`.
- `indexnow_bulk_submit(urls)` - up to 10,000 URLs sharing one host in a
  single POST. Mixed-host batches are rejected client-side with
  `INVALID_INPUT` before any network call.

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

**Chrome UX Report (1, v0.3.0)**
- `crux_history(url? | origin?, form_factor?, metrics?)` - 25 weeks of p75
  Core Web Vitals via the CrUX History API. Reuses `PSI_API_KEY`; works
  anonymously at a tighter rate limit when no key is configured.

Every tool's `tools/list` entry carries the MCP standard annotations
(`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) so MCP
hosts can decide what to auto-approve and what to confirm.

## Workflow prompts

The server publishes five named MCP prompts (via `prompts/list` /
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
36-tool surface. The difference is only how the host launches the server
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
tools icon in the input box; you should see 22 SEOMonster tools. Try
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

## Auth

The four services authenticate independently. Configure only the ones you use;
a tool for an unconfigured service returns a clear `AUTH_MISSING` error rather
than failing the server.

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

1. Generate a key. Any 8-128 character hex string works; treat it like an API
   key (do not commit it). See [indexnow.org/documentation](https://www.indexnow.org/documentation).
2. Set `SEO_MCP_INDEXNOW_KEY` (or use the `.mcpb` configuration form; the
   field is marked sensitive and lands in the OS keychain).
3. Host a verification file at `https://<your-host>/<key>.txt` whose body is
   the key string. The first time the engines see your key they fetch this
   file to verify ownership.
4. Optional: set `SEO_MCP_INDEXNOW_KEY_LOCATION` if the verification file
   lives at a non-default URL.

A common error is `AUTH_INVALID` from `indexnow_submit`; that almost always
means the engines could not fetch the verification file. Confirm the file
returns HTTP 200 with the exact key as the body before retrying.

### Verify your setup

After configuring, call `system_status` to see what is detected. Call it with
`{"probe": true}` to make one cheap live request per configured service and
confirm the credentials actually work (GSC lists properties, GA4 runs a 1-row
report against the default property, Cloudflare lists one zone, PSI pings the
endpoint). With `probe` off (the default) it does a config-only check and makes
no network calls.

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

## Configuration

Resolution is environment-first, with a TOML file fallback. Environment always
wins.

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
