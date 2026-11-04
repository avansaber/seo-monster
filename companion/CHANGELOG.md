# Changelog

## [Unreleased]

- MCP capture and local serving require Python SDK 2.3 or later within major version 2. The CLI keeps its dependency-free installation.
- Provider-error envelopes remain inspectable as unavailable evidence, with the original error and unknown cost preserved. Typed pagination, error-flag consistency and text/structured-content checks protect capture provenance.
- The local facade returns typed results with explicit failure flags and fixed diagnostics. Loopback HTTP rejects untrusted Host headers, duplicate security headers and browser Origins.
- Remote schema references and malformed schemas are refused before a provider call. Optional MCP entrypoints explain how to install the required extra without exposing a traceback.

## 0.6.0 - 2026-09-12

- Added an append-only deployment-observation ledger for changes performed outside the workbench. Local operator imports preserve source metadata, verification assertion, operations, rollback reference, caveats, an exact plan snapshot, and derived approval-timestamp alignment; no deploy or external-write capability was added.
- Added CLI record/inspect/export commands and one read-only fixed-project MCP inspection tool. Observation import remains operator-CLI-only; the MCP registry is seventeen tools: ten reads and seven local-record writes.
- Added `seomonster-workbench-change-ledger/v1`, SQLite migration 3, and `seomonster-workbench-backup/v2`. Current backups include the change ledger while legacy backup/v1 remains validated and restorable.
- Simulation rejected immutable plan revision work for this increment because the compatibility-preserved approval flow mutates plan and recommendation state. Plan revisioning requires an event-source contract rather than a second competing truth.
- Added adversarial simulations for pre-approval observations, late approval, frozen plan history, idempotency, unsafe inputs, cross-project scope, tamper detection, missing-database side effects, backup v2 replay, and legacy backup v1 restore.
- Fixed independent-review findings by validating SQLite rows on inspection/idempotent import, allowing one source observation to link to multiple plans, labeling approval as unattested timestamp order, and reporting decisions attached to transitive dependent plans during rule replay.

## 0.5.0 - 2026-09-12

- Added deterministic read-only rule replay preview for one stored raw snapshot, with installed/stored rule versions, canonical collection digests, and added/removed/changed analytical identifiers.
- Added dependency-impact reporting for decisions, action plans, and experiments that would require review before any future rule migration. No replay apply or migration capability exists.
- Kept human recommendation state outside analytical comparison so approval is preserved and does not appear as rule drift; malformed workflow state and corrupted snapshot identity are rejected.
- Added CLI and accurately read-only fixed-project MCP surfaces. The Workbench registry is now sixteen tools: nine reads and seven local-record writes; no approval or external executor was added.
- Evaluated GA4 property-to-site binding and deferred it after simulation showed unresolved many-to-many property scope, missing hostname dimensions, supersession, portability, backup, and safe rule-migration requirements. Property ID alone is not treated as site evidence.
- Added adversarial simulations for deterministic/non-mutating replay, same-ID tampering, version churn, human approval preservation, dependency impact, corrupted/missing/cross-project snapshots, invalid workflow state, malformed stored provenance, unavailable GA4 signals, CLI behavior, and stdio/HTTP contract parity.
- Fixed independent-review findings: replay now reports ownership assignments, conflicts, decisions, and plans that depend on affected evidence, and CLI preview refuses a nonexistent database without creating or migrating a file.

## 0.4.0 - 2026-09-12

- Added `seomonster-workbench-backup/v1`, a self-contained wrapper around the unchanged project export v1 and ownership export v1 with portfolio metadata, canonical component hashes, explicit restore policy, and no external execution capability.
- Added complete project-graph runtime validation for versions, identities, record shapes, cross-project and dangling references, provenance, states, plan dependencies, observational experiments, privacy, and no-execution invariants.
- Added deterministic restore preview plus transactional create-or-exact-no-op restore. Existing differences, incomplete targets, project renames, partial merge, overwrite, downgrade, deletion, and external execution are refused.
- Added operator-only backup and restore CLI commands. An adversarial Codex-host simulation showed that model-mediated structured arguments can normalize exact JSON number representations and invalidate provenance hashes, so backup export/preview/apply are intentionally absent from MCP.
- Added adversarial simulations for missing portfolio metadata, version skew, tampering, secrets, truth scores, duplicate/malformed JSON, component disagreement, target conflicts, exact replay, project isolation, and deliberate mid-restore rollback.

## 0.3.0 - 2026-09-09

- Added a deterministic portfolio ownership ledger over comparable saved GSC evidence, with exact-query cross-site visibility signals that never infer intended ownership or cannibalization.
- Added operator-declared query/topic page assignments, explicit supersession, exact-participant `accept`, `differentiate`, `consolidate`, and `route` decisions, and rollback-aware plans with no executor.
- Added evidence drill-down, stale/provider-limited confidence handling, missing-metric preservation, most-specific site/path attribution, and deterministic replay/reopen behavior.
- Added a separate `seomonster-workbench-ownership/v1` portable export and SQLite migration 2 while keeping `seomonster-workbench-export/v1` at schema version 1.
- Added seven ownership CLI commands and five MCP tools. MCP may create only pending decision drafts; final ownership assignments and decisions remain operator CLI actions.
- Expanded regression and host workflow tests for participant changes, migration, Unicode normalization, unavailable data, write gates, schema validation, CLI flow, and stdio/HTTP parity.
- Fixed resolved-date alias comparability, zero-row snapshot supersession, parallel current decisions, superseded-decision planning, and incomplete ownership export reference validation found by independent Web-growth probes.

## 0.2.0 - 2026-09-09

- Added optional single-project MCP tools over stdio and authenticated localhost Streamable HTTP, with accurate local-write annotations, structured envelopes and no approval/execution tools.
- Added an operator-only allowlisted engine capture client with timeouts, contract validation and explicit unavailable/cost/freshness metadata.
- Added portable/Codex plugin manifests, an evidence-review skill, configuration examples and an opt-in real Codex host smoke.
- Fixed replay resetting approval state, cross-project experiment references, silently overwritten project context, baseline self-comparisons, unknown freshness treated as current, and contradictory rows treated as observed.
- Corrected normalization of real GSC comparison and GA4 tabular results. Preserved original captures.
- Added transactional mutation, runtime provenance validation, explicit plan-revision refusal, non-overwriting exports, broader privacy guards and immutable experiment outcomes.
- Expanded offline regression, host transport and packaging tests; pinned complete engine tool/prompt metadata digests in addition to registration names.
- Addressed independent Web-growth review: project-site capture gates, repo-relative Git packaging checks, exact export byte checksums and compatible-outcome selection.
- Corrected GSC apex Domain-property acceptance for configured subdomains such as `www`, retaining exact page-host checks and rejecting unconfigured sibling/spoof hosts.

## 0.1.0 - 2026-09-09

- Added the independent saved-result adapter and versioned JSON contracts.
- Added local SQLite persistence with forward migration tracking.
- Added deterministic evidence, finding, recommendation, and opportunity-board generation.
- Added exact provenance/calculation inspection, typed non-executing plans, and separate decision records.
- Added baseline/outcome experiment comparisons that explicitly make no causal claim.
- Added privacy-checked portable exports.
- Added offline GSC, GA4, crawl, SERP, AI-citation, provider-error, stale, partial-failure, cold-start, malicious-content, and write-gating evaluation coverage.
- Added an external MCP handshake and 70-tool/13-prompt compatibility lock for SEOmonster 0.9.3.
