# Compatibility and migration strategy

## Independent release contract

SEOmonster and the workbench remain separate packages. The companion does not modify or import `seo_mcp` runtime code. Its only engine dependency is the serialized public contract in `schemas/saved-result-v1.schema.json`.

The MCP surface targets SDK 2 only. The CLI and persisted saved-result, project, ownership, change-ledger and backup formats do not require a Python SDK migration. Existing evidence remains unchanged. No SDK 1 handler or field-name shim is provided.

## Contract protection

`evaluation/contracts/seomonster-sdk2-v1.json` records the 70-tool and 13-prompt SDK 2 engine boundary and explicit failed-tool flags. The earlier `seomonster-0.9.3.json` snapshot is retained as historical protocol evidence. Runtime compatibility tests call the engine over stdio and inspect correctly aliased wire definitions, rather than importing its registry.

Known provider failures use `is_error=True` while retaining the complete JSON error envelope. Capture checks that this flag agrees with `ok`, preserves failures as unavailable evidence and records unknown cost as null. Transport failures have separate adapter error codes. Invalid registry, schema or content shapes are rejected; they never become confident zero observations. Registry pagination uses typed SDK 2 parameters with bounded pages and repeated-cursor detection.

The companion's 17 local tools return typed structured envelopes and matching text content. Ten tools read local records and seven create local records; none approves or executes an external change. Loopback HTTP and stdio exercise the same fixture workflow. Protocol tests do not establish native-host installation, authentication, skill selection or directory acceptance. Those remain separately qualified in `OPENAI_HOST_COMPATIBILITY.md`.

A changed engine contract requires semantic review, full engine/companion tests, an actual protocol handshake and a labelled contract snapshot. SDK field aliases must be serialized explicitly; updating a digest alone does not establish compatibility.

## Adapter evolution

- Additive metadata is accepted by v1.
- Removing or changing required envelope semantics requires `seomonster-result/v2` and a parallel adapter.
- Old adapters remain readable until a documented migration has produced equivalent snapshots.
- Unknown tool results can still be stored as opaque evidence; deterministic rules are opt-in by tool and version.
- The optional 0.2 MCP capture command checks a manually reviewed four-tool read allowlist before starting the operator-selected engine. It also validates current schemas and read hints, and materializes the same saved-result document before ingestion. It is not an arbitrary model-callable proxy.

## OpenAI host distribution

The local Codex lane can still start SEOmonster directly over stdio. The separate Workbench plugin exposes seventeen fixed-project tools over stdio, with equivalent authenticated localhost HTTP available for conformance testing. Ten tools read records and seven write local records. Replay and deployment-observation inspection are read-only. Ownership writes create only pending proposals and non-executing plans. Final assignments, decisions, deployment-observation import, backup, and restore remain operator CLI actions. No MCP tool approves or executes external changes. Hosted ChatGPT remains a separate future authentication and deployment gate.

Existing engine manifests retain their current host-specific meanings and are unchanged. The new portable plugin lives under `companion/plugins/seomonster-workbench` in the companion version namespace, so SEOmonster and the companion can still ship independently.

Tool additions may be backward compatible for raw MCP clients but still require a plugin metadata refresh and host evaluation. Tool removals, renames, schema narrowing, annotation changes, or changed safety behavior require an explicit compatibility review and a new plugin version.

## Persistence migrations

SQLite migrations are monotonically numbered and recorded transactionally. Published migrations are immutable. A future migration must be forward-only, preserve raw snapshots, and include an export/import round-trip test. Destructive compaction is outside the automated migration path.

Version 0.3 advances SQLite storage to schema 2 for additive ownership tables while retaining saved-result adapter v1 and project export v1 at `schema_version: 1`. Ownership has its own `seomonster-workbench-ownership/v1` format. Rule version `2026-09-09.2` protects both opportunity and ownership derivations. Replaying the same inputs does not duplicate conflicts or overwrite decisions. Published migrations are forward-only; future or incomplete histories are rejected.

Version 0.5 adds read-only replay preview without a persistence migration or rule-version change. It compares analytical projections only; human recommendation state is preserved separately. Historical ruleset execution and replay apply are not available. A future rule migration must explicitly supersede derived records and protect their dependent decisions, plans, and experiments.

Version 0.6 advances SQLite storage to schema 3 with an additive append-only deployment-observation table. It freezes the referenced plan at import and preserves asserted approval-timestamp alignment without changing the legacy mutable plan contract. Backup output advances to v2 to include change-ledger/v1; backup/v1 remains a supported restore input and produces an empty change ledger. Published v1 project and ownership formats remain unchanged.

## Import and export

Project export v1 is self-describing and contains project context, sites, snapshots, evidence, findings, recommendations, decisions, action plans, experiments, and semantic disclaimers. Ownership/v1 contains the ownership subgraph plus exact referenced evidence/snapshots. Change-ledger/v1 carries deployment observations, frozen plan snapshots, and approval lineage. Raw capture locators use basenames, not private paths. Backup/v2 composes all three graphs plus portfolio metadata and independent hashes; backup/v1 remains accepted. Operator CLI restore preview validates the complete graph and reports create/no-op/conflict; apply permits only a transactional create or exact no-op. Backup and deployment import are intentionally not model-facing. Cross-project merge and conflict reconciliation remain deferred.

## Rollback

Removing the companion directory leaves SEOmonster behavior unchanged. User databases and exports are independent local artifacts. Within the companion, application rollback must never downgrade or delete raw snapshot data; run an older compatible version against an export copy or migrate forward.
