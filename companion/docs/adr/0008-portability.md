# ADR 0008: Export a self-describing, privacy-checked JSON graph

- Status: accepted
- Date: 2026-09-09

## Context

User-owned evidence must remain inspectable outside one database implementation, while raw captures may accidentally carry secrets or workstation paths.

## Decision

Export `seomonster-workbench-export/v1` with project, sites, raw snapshots, evidence, findings, recommendations, decisions, plans, experiments, and semantic disclaimers. Keep its schema version fixed at 1 even when SQLite storage migrates. Export ownership history separately as `seomonster-workbench-ownership/v1`, including exact linked evidence/snapshots and an always-false execution capability. Sort records and keys deterministically. Store source basenames only. Reject populated secret-like fields, common private home paths, and a `truth_score` property.

## Consequences

Exports are portable and reviewable but not yet signed. Consumers must dispatch on the format version rather than assuming SQLite migration numbers are portable schemas. Some legitimate captures containing sensitive key names require explicit upstream redaction. Full import/merge is deferred until conflict and unknown-field preservation are tested.
