# Changelog

All notable changes to SEOMonster are documented here.

This file follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Each release section lists what a validation pass should cover. Items
marked with **(validate)** are the explicit acceptance checks for an
external testing pass on that version.

## [Unreleased]

Nothing pending.

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
  **(validate)** Confirm `prompts/list` returns four entries with their
  arguments. Invoke `prompts/get` for `weekly_review` with `days=14`,
  confirm the returned message body templates `"days": 14` into the
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
- **Schema aliases** for AI-host robustness:
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
