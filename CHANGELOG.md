# Changelog

All notable changes to SEOMonster are documented here.

This file follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each release section lists what a validation pass should cover. Items
marked with **(validate)** are the explicit acceptance checks for an
external testing pass on that version.

## [Unreleased]

Nothing pending.

## [0.7.2] - 2026-05-31

Documentation + polish patch. No new tools or prompts (52 tools / 13 prompts).

### Changed

- README "Tools" and "Workflow prompts" sections now list the full v0.7.x
  surface and correct the stale tool counts (was showing 45 / 41 / 22).
- `ga4_site_search` treats rows whose `searchTerm` is empty or "(not set)" as
  no-data and emits a configuration-specific note (search events firing without
  the term parameter captured), instead of reporting `has_site_search_data:
  true` on empty terms (FEEDBACK Round 11 §15e.1).

### Docs

- Documented the `crux_snapshot` response shape: `overall_category` for the
  rolled-up rating; `p75_ms` for time metrics and `p75` for the unitless CLS
  (FEEDBACK Round 11 §15e.2).

## [0.7.1] - 2026-05-31

Audit-coverage + content-pipeline release: 5 read-only tools and 6 workflow
prompts. 52 tools / 13 prompts. No new dependency.

### Added (tools)

- **`psi_opportunities`** - the actionable Lighthouse "opportunity" audits (with
  estimated savings) plus the SEO-category audits (severity-graded per the
  Lighthouse SEO checklist), extracted from a PageSpeed Insights run. Read-only,
  lab data only (no CrUX field data). It is an on-page-basics checklist, not a
  ranking predictor.
  **(validate)** Run on a real URL: `opportunities` sorted by savings, and
  `seo_audits` with severities (is-crawlable / http-status / viewport critical;
  title / meta-description / hreflang / canonical high).
- **`crux_snapshot`** - current Chrome UX Report p75 Core Web Vitals (LCP, INP,
  CLS, FCP, TTFB) for a URL or origin, distinct from `crux_history`.
  **(validate)** A high-traffic origin returns p75 metrics + categories; a small
  origin returns the `no_data` envelope.
- **`cf_settings_audit`** - Cloudflare SEO-settings audit (SSL mode, Always-Use-
  HTTPS, HSTS, Automatic HTTPS Rewrites, Brotli, browser cache TTL),
  severity-graded with a "verify, not fail" discipline because Cloudflare cannot
  see the origin; HSTS is never a hard failure.
  **(validate)** On a real zone: findings + verdict; SSL=Flexible is flagged
  "verify"; HSTS off is at most medium, never critical.
- **`ga4_site_search`** - internal site-search query report (a direct content-gap
  signal); returns an honest envelope when the property has no site-search data.
- **`ga4_landing_page_conversions`** - organic landing pages ranked by conversions.

### Added (workflow prompts)

- **`content_brief`**, **`content_outline`**, **`content_article`** - the
  data-grounded brief, outline, and article pipeline. Each carries its
  validation rules as instructions; the host LLM writes the content, SEOMonster
  supplies the rules. No ranking guarantees.
- **`content_workflow`** - orchestrates opportunity, brief, outline, article,
  pre-deploy check, indexing, then a scheduled performance check.
- **`content_performance`** - Layer-5 measurement: compares periods 4 to 8 weeks
  after publish to show ranking and click lift. Measures the outcome, does not
  guarantee it.
- **`seo_setup_audit`** - chains `ga4_setup_audit`, `cf_settings_audit`,
  `psi_opportunities`, and `robots_txt_validate` into a consolidated, severity-
  ranked "is your whole stack configured for SEO?" report.

### Changed

- Clarified the `content_opportunities` top-candidate expectation: usually a
  striking-distance query, but sometimes a cannibalization-driven CTR-gap when a
  query has several ranking pages (correct, given the scoring weights).

## [0.7.0] - 2026-05-31

Content-intelligence + GA4-audit release. Adds the first content-workflow tool
and a GA4 measurement-readiness audit. 47 tools / 7 prompts. No new dependency.

### Added

- **`content_opportunities`** - ranks data-grounded content/blog topics from
  your own Search Console data. Fuses CTR-vs-expected gap, striking-distance
  position, demand, and momentum into a transparent opportunity score, flags
  cannibalization, and reports click upside plus the score's components. The
  expected-CTR curve self-calibrates from your own per-position CTR (GSC has no
  position dimension, so it buckets query rows by rounded average position;
  a reference curve fills sparse buckets). It prioritizes demand you already
  have: it does not do cold-start keyword research, and it does not write the
  content or guarantee a ranking.
  **(validate)** Run against a property with real impressions: the top
  candidate is a high-impression, low-CTR, striking-distance query; each
  candidate shows expected vs actual CTR, click upside, and its component
  scores; the calibrated CTR curve is echoed in the response.
- **`ga4_setup_audit`** - read-only audit of a GA4 property's SEO-measurement
  readiness: is a web data stream present, are key events / conversions
  defined, is data retention long enough for year-over-year analysis, are
  content-group custom dimensions set. Severity-graded findings, each with the
  reason and a benign exception. Reuses the existing `google-api-python-client`
  over REST (analyticsadmin v1beta), so there is no new dependency. v1alpha
  checks (enhanced measurement, site search, Google Signals) are deferred and
  listed in the response.
  **(validate)** Run against a real GA4 property: one missing key events
  returns verdict "issues"; a 2-month data-retention setting is flagged; a
  well-configured property returns verdict "clean".

### Internal

- New `tools/content_tools.py` and `clients/ga4_admin.py` (REST discovery); a
  `ga4_admin` client builder registered in the lazy client provider.

## [0.6.0] - 2026-05-30

Setup-CLI release. Adds an interactive `seo-monster setup` so MCP host configs
need no secrets, and repoints error-envelope `docs_url` links to the GitHub
README (which carries the anchors those links target). 45 tools / 7 prompts
unchanged.

### Added

- **`seo-monster setup`** - interactive subcommand that collects the Cloudflare
  token, PageSpeed Insights key, IndexNow key, and the default GSC / GA4
  properties, validates what it can against the upstream APIs, and writes them
  to `~/.config/seo-mcp/config.toml` with `0600` permissions (parent dir
  `0700`). A rejected credential is not written; an unreachable (offline) one
  is saved with a note. Re-runnable: blank answers keep existing values.
  Environment variables still override the file. Google OAuth stays on the
  separate `seo-monster auth` flow.
  **(validate)** Run `seo-monster setup`; enter a clearly-bad Cloudflare token:
  it is reported rejected and not persisted to the file. Re-run and skip a
  field: the prior value survives. With the file written, an env var set in the
  host (e.g. `CF_API_TOKEN`) still wins at runtime. Piping no input (non-TTY)
  exits cleanly with code 1, not a traceback.

### Changed

- README documents `seo-monster setup` as the recommended credential path;
  per-service environment variables are framed as the manual / CI alternative.
- `config.py` exposes `resolve_config_path` as the single source of truth for
  the config-file location, shared by the loader and the setup writer.

### Fixed

- Error-envelope `docs_url` links pointed at `seomonster.avansaber.com#<anchor>`,
  but the live site has none of those anchor targets, so every remediation link
  landed at the page top (FEEDBACK Round-5 §10c.iv). `docs_url` now points at
  the GitHub README, which carries an explicit `<a name>` anchor for each value.
  **(validate)** Trigger any `INVALID_INPUT` (e.g. `psi_analyze` with no `url`):
  the `docs_url` resolves to the matching README section.

### Internal

- Removed an unused `__version__` import in `server.py`.
- `.gitignore` / `.mcpbignore` / sdist exclude now drop dated conversation-export
  `.txt` files, preventing accidental transcript or secret leaks.

## [0.5.1] - 2026-05-28

Patch release closing the two ship-blockers and one partial-fix
identified by the external Round-6 validator (`FEEDBACK.md` §11i pre-tag
checklist). No new features, no surface change. 45 tools / 7 prompts
unchanged.

### Fixed

- **`gsc_coverage_audit` was unusable on real properties (Round 6
  §11b.i).** The handler called `client.inspect_url(site, url)` with
  the positional arguments swapped; the underlying
  `GscClient.inspect_url(url, site_url)` interpreted the property URL
  as the inspection URL, returning `NOT_FOUND` for every URL. Mocked
  tests in `test_gsc_tools.py` could not catch this because positional
  mock signatures accept anything.
  **Fix:** call site corrected to use keywords. To prevent this bug
  class from recurring, `GscClient.inspect_url` is now **keyword-only**
  (`def inspect_url(self, *, url, site_url)`). All three handlers
  (`gsc_inspect_url`, `gsc_batch_inspect_urls`, `gsc_coverage_audit`)
  updated to the kwargs form.
  **Test:** `test_gsc_client_inspect_url_is_kwargs_only` pins the
  keyword-only contract via `inspect.signature` so a future refactor
  cannot silently remove the `*,`. `test_coverage_audit_passes_url_not_site_to_inspect`
  asserts the per-URL call body's `inspectionUrl` field carries the
  URL, not the property.
  **(validate)** Call `gsc_coverage_audit` with a real `site_url` and
  5-10 URLs you know are indexed; confirm `verdict_counts` and
  `coverage_state_counts` populate and `audited_count == len(urls)`
  with empty `failed`.

- **`.mcpbignore` was missing `.private/` (Round 6 §11b.ii).** Local
  `npx @anthropic-ai/mcpb pack` runs against the working tree, which
  has access to `.private/cursor-submission/*` (gitignored but
  present). The shipped v0.5.0 `.mcpb` on GitHub was clean because CI
  checks out from git (never sees `.private/`), but a developer's
  local pack would have leaked 7 files including draft submission
  bodies and the email template. **Fix:** `.private/` added to
  `.mcpbignore`, alongside the existing `marketing/` entry.
  **(validate)** Run `npx @anthropic-ai/mcpb pack` against the
  working tree at this commit; verify the produced bundle contains
  no `.private/`, `marketing/`, or `*.md` planning paths.

- **Round 5 §10a.v IndexNow doc partial close (Round 6 §11d.i).** The
  v0.4.0 fix added Claude Desktop install-form labels and the full
  Auth section setup steps. The Tools-section blurb for
  `indexnow_submit` / `indexnow_bulk_submit` still didn't mention
  `SEO_MCP_INDEXNOW_KEY_LOCATION` or cross-reference the setup
  section. **Fix:** Tools section now links into the Auth IndexNow
  section for the full setup story, and calls out
  `SEO_MCP_INDEXNOW_KEY_LOCATION` as the env var to set when a CDN
  rewrites `/key.txt` paths. The Auth section already had key
  generation, file format rules, same-host constraint, and a
  common-errors triage table from v0.4.0; no changes there.

### Changed

- **CI release workflow** (`release.yml`) now has a defense-in-depth
  scan that rejects any unpacked artifact containing `.private/`,
  `marketing/`, `PLAN.md`, `RESEARCH-AND-PROPOSAL.md`,
  `LISTINGS-PLAN.md`, `EMAIL_DRAFT.md`, or `SUBMISSION_GUIDE.md`
  paths. The existing credential-pattern scan stays in place. This
  catches the bug class behind §11b.ii even if `.mcpbignore` or the
  `pyproject.toml` sdist-exclude block drift again in the future.
- HTTP client User-Agent bumped from `SEOMonster/0.5.0` to
  `SEOMonster/0.5.1`.

### Tests

- 316 passing (up from 314 in `0.5.0`; +2 new tests:
  `test_gsc_client_inspect_url_is_kwargs_only` and
  `test_coverage_audit_passes_url_not_site_to_inspect`).

### Not changed

- Tool count: **45** (unchanged from v0.5.0).
- Prompt count: **7** (unchanged from v0.5.0).
- No tools renamed or removed.
- API stability: existing handlers still accept the same arguments;
  this is a pure-internal client refactor invisible to MCP callers.

## [0.5.0] - 2026-05-28

The multi-property + lifecycle sprint. Surface grows to 45 tools and 7
named workflow prompts. No new auth surface; the four new tools reuse
the existing `GscClient`, the new prompt chains tools we already
shipped. No breaking changes for v0.4.x consumers; everything is
additive.

### Added

- **Four new GSC tools (multi-property + lifecycle theme):**
  - **`gsc_portfolio_summary(days, include?, exclude?)`** — multi-property
    fleet view. Lists every property the credentials can see, runs one
    aggregated query per property (clicks, impressions, CTR, position
    over the last N days), and returns per-property rows plus a
    portfolio-level rollup. Honors optional `include` and `exclude`
    allow/deny lists.
    **(validate)** Pass `days=14` against a real account with multiple
    GSC properties; confirm `portfolio_totals.property_count` matches
    the visible count and each row has the expected metrics.
  - **`gsc_trending_pages(days, limit)`** — pages whose impressions grew
    most over the last N days vs the prior N days. Wrapper on
    `gsc_compare_periods` with `dimensions=["page"]`,
    `sort_by="delta_impressions"`, `sort_dir="desc"`.
    **(validate)** Pass `days=14, limit=10` against a real property;
    confirm `filters_applied.sort_dir == "desc"` and rows are ordered by
    descending `delta_impressions`.
  - **`gsc_decaying_pages(days, limit)`** — same wrapper, ascending
    sort. Pages whose impressions fell most.
    **(validate)** Pass `days=14, limit=10`; confirm
    `filters_applied.sort_dir == "asc"`.
  - **`gsc_coverage_audit(urls, site_url?)`** — heuristic coverage
    audit. The GSC Index Coverage report is not exposed in the public
    API, so this tool takes a user-supplied URL list (typically from a
    sitemap), bulk-inspects each, and rolls up `verdict_counts` and
    `coverage_state_counts`. Cap 200 URLs per call.
    **(validate)** Pass a list of 5-10 URLs from a real site; confirm
    `verdict_counts` and `coverage_state_counts` populate and per-URL
    failures (e.g. URLs outside the property's scope) land in
    `.failed` instead of poisoning the call.
- **One new prompt:** **`pre_deploy_check(urls)`** — recognizable
  deploy-gate label. Runs `robots_txt_validate` against the host root,
  then for each URL: `inspect_meta`, `check_canonical`, `validate_schema`,
  `redirect_chain_audit`, `mixed_content_check`. Output is a deploy-gate
  verdict (block on critical issues, approve otherwise) plus a per-URL
  table. Complements `technical_seo_audit` (one URL deep) and
  `structured_data_audit` (Rich Results focus) by covering the broad
  batch case.
  **(validate)** In Claude Desktop, invoke `/pre_deploy_check` with two
  staging URLs and confirm all 6 tool calls fire in order followed by
  the deploy-gate verdict.
- **`llms-install.md`** at the repo root. Short-form install for AI
  coding agents (Cline, Cursor, Codex) per the `llms-install.md`
  convention. Lists env vars, OAuth pre-flight CLI, and the
  `system_status` discovery convention.

### Changed

- **`DOCS_BASE` flips back to the brand subdomain.** Every error
  envelope's `docs_url` now points at `https://seomonster.avansaber.com#anchor`
  (was: GitHub README anchors as a v0.4.0 interim per Round-5
  §10c.iv). The subdomain is now provisioned at the DNS layer; the
  full marketing site content lands in parallel with this release.
- **HTTP client User-Agent** bumped from `SEOMonster/0.4.0 (+...)` to
  `SEOMonster/0.5.0 (+...)`.
- **`manifest.json`** `tools[]` array extended with the 4 new GSC tools.

### Tests

- 314 passing (up from 306 in `0.4.0`). 8 new offline tests in
  `test_gsc_tools.py` covering the 4 new tools: portfolio aggregation,
  include filter, per-property error collection, trending sort
  direction, decaying sort direction, coverage rollup, coverage
  per-URL failures, coverage empty-URLs guard. All offline; mock at
  the client seam.

## [0.4.0] - 2026-05-28

The structured-data + cross-site-consistency sprint. Surface grows to 41
tools and 6 named workflow prompts. No new auth surface; all five new
tools reuse either the existing `HttpClient` (4 of 5) or `PsiClient`
(1 of 5). No breaking changes for v0.3.x consumers; everything is
additive.

### Added

- **`inspect_schema(url)`** extracts every JSON-LD block from a page
  (script type=application/ld+json), flattens `@graph` wrappers and
  arrays, and reports the schema.org `@type` counts plus a sample
  entity per type. Discovery tool: tells you what structured data
  exists before you validate it.
  **(validate)** Pass a real product page and confirm at least
  `Product` (or `BreadcrumbList`) appears in `type_counts` with the
  expected sample entity.
- **`validate_schema(url, types?)`** verdicts every JSON-LD entity
  against the Google Rich Results required-field set for Article,
  NewsArticle, BlogPosting, Product, FAQPage, BreadcrumbList,
  Organization, LocalBusiness, Event, Review, Recipe. Per-entity
  verdict (`pass` / `fail` / `unknown_type` / `parse_error`) plus
  `missing_required` and `missing_recommended` lists. Optional
  `types` filter limits the check to a subset of `@types`.
  **(validate)** Pass a Product page missing the `offers` field;
  confirm verdict is still `pass` (offers is recommended only) but
  `missing_recommended` contains `offers`.
- **`hreflang_consistency_check(urls)`** runs cross-page hreflang
  validation on a user-supplied URL set (max 50). Builds the full
  reciprocity matrix, flags missing reciprocal links, broken
  hreflang targets (non-2xx), duplicate hreflang values on one page,
  missing self-link, and missing `x-default` when there are three or
  more language variants.
  **(validate)** Pass three URLs representing en / fr / de of the
  same page where the de version has no `x-default`; confirm
  `missing_x_default` appears in that page's `flags`.
- **`internal_link_graph(start_url, max_depth=2, max_pages=50)`**
  does a small BFS crawl within the same host. Returns per-page
  in-degree + out-degree, orphan pages (zero in-degree among
  fetched pages), broken internal links (4xx/5xx), and a depth
  histogram. Hard ceilings: `max_depth` <= 4 and `max_pages` <= 200.
  Skips `mailto:`, `tel:`, `javascript:`, and pure-fragment links.
  **(validate)** Pass a documentation site root with
  `max_depth=2, max_pages=30`; confirm `pages_fetched` >= 5,
  `depth_distribution` shows at least one entry at depth 1 and one
  at depth 2.
- **`lighthouse_budget(url, budget)`** wraps `psi_analyze` and
  verdicts the result against a budget dict, e.g.
  `{performance: 80, LCP_ms: 2500, CLS: 0.1}`. Higher-is-better
  category scores must meet or exceed the budget; lower-is-better
  metrics (LCP_ms, FCP_ms, TBT_ms, TTI_ms, speed_index_ms, CLS)
  must be at or below. Returns per-metric verdict and an overall
  pass/fail. Unknown budget keys are collected separately so a typo
  is surfaced rather than silently ignored. Reuses `PSI_API_KEY`.
  **(validate)** Run against a known-fast URL with a tight budget
  (e.g. `{performance: 90, LCP_ms: 2500}`); flip the budget to
  `{performance: 99}` and confirm overall_verdict flips to `fail`
  with the per-metric verdict explaining why.
- **New MCP prompt: `structured_data_audit(urls)`** chains
  `inspect_schema` and `validate_schema` per URL, then (when 2+
  URLs are supplied) `hreflang_consistency_check` across the set.
  Output is a per-URL findings list plus a global hreflang report.
  **(validate)** In Claude Desktop, invoke `/structured_data_audit`
  with two URLs and confirm both per-URL tool calls run in order
  followed by the cross-URL hreflang call.

### Changed

- `system_status` catalog groups the four prefix-free v0.4.0 tools
  (`inspect_schema`, `validate_schema`, `hreflang_consistency_check`,
  `internal_link_graph`) plus `lighthouse_budget` under the existing
  `technical` group. `lighthouse_budget` internally calls `PsiClient`
  but is grouped with technical-SEO tools because that is when SEO
  triage actually reaches for it (CI / pre-deploy gate). Grouping is
  for catalog UX, not for service routing.
- HTTP client User-Agent bumped from `SEOMonster/0.3.1 (+...)` to
  `SEOMonster/0.4.0 (+...)`.

### Round-5 validation fixes (folded in)

Tester report at `Round 5` (post-v0.3.1) surfaced 9 findings + 5
documentation gaps. All landed before tagging v0.4.0:

- **CrUX `API_KEY_SERVICE_BLOCKED` remap (Round-5 §10a.iv).** Previously
  surfaced as `AUTH_INVALID` ("check your API key"), which sent users on
  the wrong debugging path. Now correctly classified as
  `SERVICE_DISABLED` with the activation URL extracted into
  `details.activation_url`. Same UX as the GA4 `SERVICE_DISABLED` path.
  Marker list at `clients/errors.py` pinned to the verbatim upstream
  text the validator captured.
  **(validate)** Call `crux_history` with a `PSI_API_KEY` whose project
  hasn't enabled the CrUX API; confirm the envelope is `SERVICE_DISABLED`
  and `details.activation_url` is populated.
- **`sitemap_validate` emits `missing_lastmod` finding for sitemap-index
  too (Round-5 §10a.ii).** Previously only emitted for `urlset`. Sitemaps.org
  spec says sitemap-index `<lastmod>` tells crawlers when the underlying
  sub-sitemap last changed (more useful than per-URL lastmod). AI hosts
  triaging by `findings` length now see the issue.
- **`system_status.openWorldHint` flipped to `false` (Round-5 §10a.i).**
  Server-internal state, not external data; hosts that cache by
  `openWorldHint` can now cache it. Pinned to RESEARCH §5.1 matrix; new
  regression test in `test_smoke.py` guards specific annotation overrides.
- **`lighthouse_budget` unknown budget keys now surface as findings**
  (Round-5 §10b.U). Previous behaviour silently stashed typos in
  `unknown_budget_keys: ["LCP"]` and returned `overall_verdict: "pass"`
  even though the LCP budget was never applied. New shape is structured
  with a "did you mean LCP_ms?" hint, and the tool's schema description
  enumerates all valid budget keys + their units + their 0-100 scale.
  Same change documented in README.
- **`DOCS_BASE` repointed to GitHub README (Round-5 §10c.iv).**
  `seomonster.avansaber.com` is not live yet (LISTINGS §9 Q1 STILL
  OPEN); every error envelope's `docs_url` was pointing at a
  connection-refused page. Now points at
  `https://github.com/avansaber/seo-monster/blob/main/README.md#anchor`
  with explicit `<a name="...">` markers in README (11 anchors:
  `#auth`, `#errors`, `#configuration`, `#destructive-mode`, `#gsc`,
  `#ga4`, `#psi`, `#cf`, `#indexnow`, `#technical`, `#crux`).
  Reversible when the brand subdomain stands up.
- **IndexNow README setup section expanded (Round-5 §10a.v).** Now
  covers key-generation rules (8-128 alphanumeric or hyphen, with a
  `secrets.token_hex(16)` example), verification-file format
  requirements (plain text, no trailing newline / BOM / whitespace,
  Content-Type `text/plain`), the same-host constraint between key file
  and submitted URLs, and a common-errors triage table.
- **`gsc_compare_periods` `sigma_used` field documented (Round-5 §10a.iii).**
  Tool description now explains: when `anomalies_only=true`, `sigma_used`
  reports the population-stdev computed from the matched rows' `sort_by`
  metric distribution; the effective z-score cutoff applied is
  `sigma_threshold * sigma_used`.
- **Canonical service labels across listings (Round-5 §10c.iii).**
  Standardized on "PageSpeed Insights (PSI)" and "Chrome UX Report
  (CrUX)" with the full form on first mention per document. Updated
  `manifest.json`, `server.json`, and README. `server.json` description
  now 96 chars (under the 100-char cap).
- **PyPI Trusted Publishing step un-commented behind a feature flag
  (Round-5 §10e.i).** `.github/workflows/release.yml` now contains a
  ready-to-fire `Publish to PyPI` step gated by the repo variable
  `PYPI_PUBLISH_ENABLED`. To activate: (1) set up the trusted publisher
  in PyPI's publishing settings, (2) set the repo variable to `true`,
  (3) push the next tag. Until then the step is skipped and the
  pipeline behaves identically to today's manual-publish flow.
- **mcp.so submission comment (`chatmcp/mcpso#1`) edited to remove
  em-dash (Round-5 §10c.ii).** External-surface hygiene; the project's
  in-repo docs were already clean.

### Tests

- 306 passing (up from 260 in `0.3.1`; +4 net-new tests from the Round-5
  fixes: CrUX `API_KEY_SERVICE_BLOCKED` mapping, generic 403 fallthrough,
  `lighthouse_budget` LCP-typo did-you-mean, and the §5.1 annotation-matrix
  regression in `test_smoke.py`; one existing test renamed for the new
  did-you-mean shape). Total new tests across the v0.4.0 sprint: 46 (42
  from the new tool modules + 4 from Round-5 fixes). All offline; mock
  at the client seam.

## [0.3.1] - 2026-05-28

Discoverability release. No behaviour change.

### Added

- README carries the `<!-- mcp-name: io.github.avansaber/seo-monster -->`
  ownership-verification marker required by the official MCP Registry
  (`registry.modelcontextprotocol.io`) for PyPI-registry-type entries.
  The marker is HTML-commented so it does not render in user-facing
  views of the README, only in the raw markdown that PyPI exposes as
  the package description.

### Changed

- HTTP client User-Agent bumped from `SEOMonster/0.3.0 (+...)` to
  `SEOMonster/0.3.1 (+...)` so server-side analytics that key on the
  UA reflect the running version.

### Tests

- 260 passing (no change from `0.3.0`; the marker is content-only).

## [0.3.0] - 2026-05-28

The technical-SEO sprint. Surface grows to 36 tools and 5 named workflow
prompts. No breaking changes for v0.2.x consumers; everything is additive.

### Added

- **Seven technical-SEO HTTP tools** (no new auth surface; the built-in
  HTTP client needs no configuration):
  - `inspect_meta(url)` returns the page's on-page SEO surface in one
    call: title, meta description, meta robots, canonical, Open Graph +
    Twitter Card tags, hreflang list, and H1 count.
    **(validate)** Pass a real product page and confirm title, meta
    description, canonical, and at least one OG tag come back populated.
  - `check_canonical(url)` follows the page's canonical link and reports
    whether it is self-referential, cross-host, protocol-mismatched, or
    has trailing-slash drift; flags missing canonical and unreachable
    canonical target.
    **(validate)** Pass a URL whose canonical points to a different
    hostname; confirm `cross_url` and `cross_host` are listed in
    `findings`.
  - `mixed_content_check(url)` parses an HTTPS page and reports any
    `http://` references (img / script / iframe / form action / srcset).
    No-op for `http://` pages.
    **(validate)** Pass an HTTPS URL with at least one known mixed-content
    issue; confirm `verdict=mixed_content_found` and the violation appears
    in the right category bucket.
  - `redirect_chain_audit(url, max_redirects=10)` walks the chain hop by
    hop without auto-following. Flags `long_chain`, `protocol_downgrade`,
    `non_2xx_terminus`; surfaces redirect loops as `UPSTREAM_ERROR`.
    **(validate)** Pass a URL with a known multi-hop redirect; confirm
    `hop_count > 1` and `long_chain` is in `findings`.
  - `robots_txt_validate(site_url, probes=[{user_agent, url}])` parses
    `/robots.txt`, returns the per-group rules + sitemaps, and verdicts
    optional probes using RFC 9309 longest-match semantics (which is what
    Google and Bing actually do, not what the stdlib `robotparser` does).
    **(validate)** Pass a robots.txt containing
    `Disallow: /admin/` + `Allow: /admin/public` and probe
    `/admin/public/page`; confirm `allowed=true` and
    `matched_rule.path == "/admin/public"`.
  - `sitemap_validate(sitemap_url)` validates a sitemap or sitemap-index
    XML, counts entries, flags oversize (>50k URLs or >50 MiB),
    cross-host entries, and missing `<lastmod>`. Handles `.gz`
    transparently.
    **(validate)** Pass a real sitemap URL; confirm `entry_count` is
    non-zero and `findings` is empty for a well-formed one.
  - `sitemap_health(sitemap_url, sample_size=25)` samples N URLs from a
    sitemap (descending one level for an index), HEAD-checks each, and
    returns a status histogram plus the first non-2xx examples.
    **(validate)** Pass a sitemap with known 404s; confirm the histogram
    has a `"404"` bucket and `non_2xx_examples` lists them.
- **`crux_history(url? | origin?, form_factor?, metrics?)`** wraps the
  Chrome UX Report History API to return the last 25 weekly collection
  periods of Core Web Vitals at p75 (LCP, INP, CLS, etc.). Reuses
  `PSI_API_KEY`; works anonymously at a tighter rate limit when no key
  is configured.
  **(validate)** Pass a high-traffic origin (e.g. an e-commerce
  homepage); confirm `periods` returns 25 entries and at least
  `largest_contentful_paint` has 25 p75 values.
- **One new MCP prompt: `technical_seo_audit(url)`** chains
  `inspect_meta` + `check_canonical` + `redirect_chain_audit` +
  `mixed_content_check` + `robots_txt_validate` for the URL, then
  `sitemap_health` for its host root, and produces a triage list ranked
  by severity (Critical / High / Medium / Low).
  **(validate)** In Claude Desktop, invoke `/technical_seo_audit` with a
  real URL and confirm all six tool calls fire in order.

### Changed

- `system_status` now surfaces the new `technical` and `crux` service
  rows alongside the existing five, and groups the new tools under those
  keys in the catalog so a host can filter by service.
- `manifest.json` `description` + `long_description` updated to mention
  the technical-SEO and CrUX additions.
- Console scripts unchanged: `seo-monster` (primary) and `seo-mcp`
  (deprecated alias) both run the same `main()`.

### Tests

- 260 passing (up from 211 in v0.2.1). New test modules:
  `test_http_client.py`, `test_onpage_tools.py`,
  `test_redirect_robots_tools.py`, `test_sitemap_tools.py`,
  `test_crux_tools.py`. All offline; mock at the client seam.

## [0.2.1] - 2026-05-28

Housekeeping release. No new tools, no behaviour changes. The previously
public planning + strategy + marketing files have been scrubbed from git
history, removed from the published sdist, and guarded against
reintroduction. **`0.2.0` was yanked on PyPI as part of this cleanup** so
the default resolver picks `0.2.1`.

### Changed

- `pyproject.toml` declares `[tool.hatch.build.targets.sdist] exclude`
  covering the internal planning patterns (`RESEARCH-AND-PROPOSAL.md`,
  `LISTINGS-PLAN.md`, `PLAN.md`, `marketing/`, `.private/`, `.env*`). Any
  future accidental tracking of these names will not reach a published
  sdist.
- `.github/workflows/release.yml` now runs a "reject internal planning
  docs" pre-build check on every tag push. Any tag with one of those
  patterns tracked fails the release before anything is built or uploaded.
- New `CONTRIBUTING.md` documenting the convention so the next maintainer
  knows where internal docs go.

### Security

- `.gitignore` adds explicit entries for the planning-doc filenames and
  `.private/`. `git add` of any of these is silently dropped.
- Git history rewritten with `git filter-repo --invert-paths` to remove the
  planning docs from every commit they ever appeared in. Force-pushed
  `main` and all tags (`v0.1.0` through `v0.2.0`). Anyone with a clone
  from before this release needs to re-clone or rebase.
- **(validate)** `curl -s -o /dev/null -w "%{http_code}\n"
  https://raw.githubusercontent.com/avansaber/seo-monster/main/PLAN.md`
  should return `404`. Same for `LISTINGS-PLAN.md`,
  `RESEARCH-AND-PROPOSAL.md`, and any path under `marketing/`.

### Tests

- 211 passing (no change from `0.2.0`; no code changed).

## [0.2.0] - 2026-05-28

The intelligence sprint. Surface grows to 28 tools and 4 named workflow
prompts. No breaking changes for existing v0.1.x consumers; everything is
additive.

### Added

- **Four GSC query intelligence tools** (parity with Suganthan's MCP for
  the common SEO triage flows):
  - `gsc_query_opportunities(site_url?, days=28, position_max=10, ctr_max=0.03, impressions_min=100, limit=50)`
    finds queries already ranking in the top N with below-target CTR.
    Title and meta optimization candidates. Sorted by impressions desc.
    **(validate)** Pass with a real GSC property and confirm rows return
    sorted; pass `position_max=5` and confirm the row set shrinks.
  - `gsc_query_gaps(site_url?, days=28, impressions_min=50, clicks_max=2, limit=50)`
    finds queries that draw impressions but barely any clicks.
    **(validate)** Pass with a real property; confirm zero-click queries
    appear at the top.
  - `gsc_new_queries(site_url?, days=7, prior_days=28, impressions_min=5, limit=50)`
    set-difference between current and prior windows; queries appearing
    only in the current window are returned. Makes two GSC calls.
    **(validate)** Pass with `days=7, prior_days=28`; confirm only newly
    appearing queries are surfaced.
  - `gsc_top_pages_by_query(query, site_url?, days=28, limit=20)` returns
    pages ranking for an exact query. Cannibalization audit input.
    **(validate)** Pass a query you know has multiple ranking pages; the
    response should list them with their positions.
- **Two IndexNow tools** (complementing the Google-only
  `gsc_request_indexing` for Bing, Yandex, Naver, Seznam, Yep):
  - `indexnow_submit(url)` and `indexnow_bulk_submit(urls)`. Bulk submit
    enforces single-host precondition client-side and surfaces
    `INVALID_INPUT` before any HTTP call when mixed hosts are passed.
    **(validate)** Set `SEO_MCP_INDEXNOW_KEY`, host the verification file
    at `https://<your-host>/<key>.txt`, then submit a real URL and
    confirm `accepted: true`. Try mixed-host bulk submit; expect
    `INVALID_INPUT` with the message naming the second host.
- **MCP tool annotations on every tool** (`readOnlyHint`, `destructiveHint`,
  `idempotentHint`, `openWorldHint`). Required by the Anthropic Connectors
  Directory; about 30% of submissions are rejected for missing them.
  **(validate)** `system_status` is no help here. Inspect any tool entry
  in the `tools/list` MCP response and confirm an `annotations` block is
  present with all four fields. The `test_every_tool_carries_mcp_annotations`
  test in CI pins this.
- **MCP `prompts/` capability** with four named workflow recipes:
  - `post_deploy_verify(urls, zone?, skip_psi?)` chains CF cache purge,
    GSC indexing request, IndexNow notification, PSI baseline.
  - `weekly_review(days?, site_url?)` chains gainers, losers,
    opportunities, gaps, GA4 organic context.
  - `content_audit(site_url?, days?, top_n_queries?)` chains top queries,
    per-query top pages, cannibalization recommendation.
  - `migration_check(urls, site_url?)` chains batch URL inspection,
    sitemap list, canonical agreement table.
  **(validate)** Confirm `prompts/list` returns the four prompts named
  above (`post_deploy_verify`, `weekly_review`, `content_audit`,
  `migration_check`) with their arguments. Note: v0.3.0 ships a fifth
  prompt (`technical_seo_audit`), so the actual `prompts/list` length
  will be 5 on current builds, but these four must all be present.
  Invoke `prompts/get` for `weekly_review` with `days=14`, confirm the
  returned message body templates `"days": 14` into the
  `gsc_compare_periods` call shape.
- **`gsc_compare_periods` enhancements** (absorbs the originally proposed
  `gsc_rank_drops` and `traffic_anomaly_detector`; defaults preserve
  v0.1.x behaviour):
  - `sort_by` (`delta_clicks` default), `sort_dir` (`desc` default).
  - `min_delta_clicks`, `min_delta_impressions`, `min_delta_position`
    (absolute-value floors, AND-combined).
  - `anomalies_only` + `sigma_threshold` (default 2.0) for z-score
    filtering against the sort_by metric.
  - `top` to cap returned rows.
  - Output adds `total_matched` (pre-filter) and `filters_applied`
    (including `sigma_used` when anomaly mode fires).
  **(validate)** With `sort_dir=asc` confirm losers surface first. With
  `anomalies_only=true, sigma_threshold=2.0` confirm only statistical
  outliers remain and `filters_applied.sigma_used` is populated.
- **Configuration:** new `SEO_MCP_INDEXNOW_KEY` (sensitive) and
  `SEO_MCP_INDEXNOW_KEY_LOCATION` env vars; matching `[indexnow]` TOML
  block; matching MCPB `user_config` entries.
- **`system_status` envelope** gains a new `services.indexnow` block (with
  `key_location`) and a top-level `prompts` array listing the four prompt
  names.

### Changed

- `Tool` advertisement in `server.py` now uses `Tool.model_validate(d)`
  instead of `Tool(**d)` so the nested annotations sub-dict is parsed
  into the SDK's `ToolAnnotations` type.
- Smoke test renamed `test_full_v1_surface_registered` ->
  `test_full_v02_surface_registered`; it now asserts the 22 v0.1.x tools
  are a subset of the registered set (so future additions don't trip it)
  and that GA4 + Cloudflare + IndexNow categories are all populated.
- Tool count: 22 -> 28. Prompt count: 0 -> 4.

### Deprecated

- `seo-mcp` console alias still works (no breaking change). The canonical
  console name is `seo-monster`. Invoking via `seo-mcp` now emits a single
  line on stderr explaining the deprecation; stdout (the MCP protocol
  channel) is untouched. The alias will be removed in a future major
  release; current installs need do nothing.

### Tests

- 159 -> 211 offline tests passing.
- `test_every_tool_carries_mcp_annotations` regression guard pins the
  annotation requirement; future tools cannot ship without them.

### Manifest

- `version` bumped to 0.2.0.
- `user_config` gains `indexnow_key` (sensitive) and
  `indexnow_key_location`.
- `tools` array now lists all 28 tools and four prompts get listed via
  the runtime `prompts/list` call (no static manifest entry needed for
  prompts).

## [0.1.2] - 2026-05-28

### Fixed

- GSC property-scope 403 markers now match Google's verbatim upstream text
  (`"You do not own this site, or the inspected URL is not part of this
  property."`). The v0.1.1 markers were hypothetical and missed this exact
  phrasing, sending the error through to `AUTH_INVALID` instead of the
  intended `NOT_FOUND`. **(validate)** Inspect any URL outside the
  property's verified scope; the envelope should now read `NOT_FOUND` with
  a remediation pointing at `site_url` scope, not at credentials.

## [0.1.1] - 2026-05-28

The Round-2 validation-pass response. Critical fix for Claude Desktop
users; broad DX improvements.

### Added

- **`seo-monster auth` CLI subcommand.** The server no longer opens a
  browser from inside an MCP subprocess (the GUI flow was timing out under
  Claude Desktop). Run the consent once from a terminal; subsequent server
  runs read the cached token. **(validate)** Run `uvx seo-monster auth`
  in a terminal, complete consent, then confirm GSC tools work from Claude
  Desktop.
- **Schema aliases** so AI hosts get the parameter names right on the first try:
  - `gsc_search_analytics` accepts `days` and `limit`.
  - `gsc_compare_periods` accepts `days` and `limit`.
  - `gsc_submit_sitemap` accepts `sitemap_url` (the friendly form).
  - `gsc_request_indexing` accepts singular `url` in addition to `urls`.
  - `ga4_run_report` accepts `days` and `limit`.
  All backwards-compatible. **(validate)** Confirm each old-name call
  still works and each new-name call returns equivalent results.
- **Diagnostic polish in `system_status`**: new `config_source` field
  (`"env"` or the TOML path) and new `services.ga4.reason` field
  explaining why GA4 `reachable` is null when it is.
- **`gsc_list_properties`** rows now carry a derived `writable: bool`
  (true for `siteOwner` / `siteFullUser`).
- **PSI 429** now carries an actionable remediation pointing at
  `PSI_API_KEY` (anonymous quota is often exhausted).

### Changed

- GSC property-scope 403 routes to `NOT_FOUND` with a property-scope
  remediation rather than `AUTH_INVALID` (regression: see v0.1.2 for the
  marker-text completeness fix).
- OAuth token file written with `0600` permissions; parent directory
  created with `0700`.

### Security

- `.gitignore` covers `.env.*` variants in addition to `.env`.
- `.mcpbignore` excludes `.claude/` (Claude Code's local settings) in
  addition to `.cursor/`.

### Manifest

- `version` bumped to 0.1.1.
- `user_config` gains `gsc_default_site` so Claude Desktop users can
  pre-set their GSC property via the install form.

## [0.1.0] - 2026-05-27

Initial public release.

### Added

- **22 tools across four services**: GSC (10), GA4 (4), PSI (1),
  Cloudflare (6), plus the cross-service `system_status` discovery tool.
- **OAuth-first auth**, with service-account as the advanced headless
  alternative. User-credential-driven; no auth baked into the package.
- **Read-first design**: only Cloudflare cache-purge tools are gated
  behind `SEO_MCP_ALLOW_DESTRUCTIVE=true`. The GSC writes (sitemap
  submit, indexing request) are available by default.
- **`.mcpb` bundle** for one-click Claude Desktop install.
- **Stable result envelope** (`{ok, data, error}`) with a closed set of
  error codes (`AUTH_MISSING`, `AUTH_INVALID`, `SCOPE_INSUFFICIENT`,
  `DESTRUCTIVE_DISABLED`, `CONFIRM_REQUIRED`, `NOT_FOUND`,
  `INVALID_INPUT`, `RATE_LIMITED`, `SERVICE_DISABLED`, `UPSTREAM_ERROR`).
- **123 offline tests**, no network in CI.

[Unreleased]: https://github.com/avansaber/seo-monster/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/avansaber/seo-monster/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/avansaber/seo-monster/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/avansaber/seo-monster/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/avansaber/seo-monster/releases/tag/v0.1.0
