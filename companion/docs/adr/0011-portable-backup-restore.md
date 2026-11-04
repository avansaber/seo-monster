# ADR 0011: Portable backup and transactional restore

- Status: accepted after simulation
- Date: 2026-09-12

## Initial hypothesis

Increment 1 should begin with a local backup/restore boundary rather than a broad merge engine. The first proposal is to import the existing project export v1 and optional ownership export v1 into a local database after a deterministic dry run.

The proposal must preserve approved business context verbatim, raw snapshots and analytical provenance; validate every identifier and reference before mutation; distinguish create, exact no-op, and conflict; transact the entire restore; and expose no external execution. Existing portable formats, engine registries, and result envelopes must remain unchanged.

## Simulation questions

1. Is project export v1 self-contained enough to restore portfolio identity without inventing metadata?
2. Does its current runtime validator reject version skew, duplicate IDs, dangling references, cross-project records, truth scores, secrets, and private paths?
3. Can project and ownership graphs be reconciled without discarding raw evidence or silently preferring one copy?
4. Can a dry run report the exact operation and conflicts without creating a database project?
5. Is apply atomic under a deliberate mid-restore failure, and is exact replay a no-op?
6. Can a fixed-project MCP host carry a complete backup without changing exact values, weakening hashes, or consuming unreasonable model context?

## Simulation evidence

The pre-implementation tests failed in four material ways against v0.3:

- Project export v1 omitted the portfolio record, so a standalone restore would have to invent its name or break the stable portfolio identifier invariant.
- The project export privacy guard accepted a recommendation with a dangling finding reference.
- The same guard accepted an unsupported format version.
- No distinct preview and atomic apply operations existed.

These are capability gaps, not reasons to mutate project export v1. Adding portfolio fields to that format would violate its frozen top-level contract and existing compatibility tests.

## Revised decision

Add `seomonster-workbench-backup/v1` as a self-contained wrapper around unchanged project export v1 and ownership export v1. The wrapper carries the exact portfolio record, canonical component hashes, storage schema version, an explicit create-or-exact-no-op restore policy, and an always-false external-execution capability.

Before either preview or apply, runtime validation must:

- validate the complete project graph, including version, duplicate IDs, project/portfolio lineage, snapshot/evidence/finding/recommendation/decision/plan/experiment references, provenance, timestamps, states, dependencies, and no-causation/no-execution invariants;
- validate the ownership graph and require its shared sites, snapshots, and evidence to be byte-for-byte equivalent to the project component;
- verify canonical component hashes, privacy restrictions, approved-claim policy, and the exact project identity configured by an MCP host;
- return deterministic create, no-op, or conflict reporting without mutation.

Apply runs one SQLite transaction. It may create a missing project or return an exact no-op. It does not perform partial merge, overwrite, rename, downgrade, deletion, conflict resolution, or external execution. Any different existing record or incomplete target state rejects the entire operation.

Expose backup export and restore preview/apply through the operator CLI only. Do not register model-facing backup tools. An actual Codex CLI simulation normalized one JSON number from `16.0` to `16` while constructing a structured tool argument. The restore validator correctly rejected the resulting component/provenance hash mismatch, and the attempt consumed excessive model context. Relaxing canonical hashes would make the evidence boundary weaker, so the MCP proposal is rejected. A future MCP design must use an opaque staged handle or resource transport that preserves exact file bytes without model transcription.

GA4 property binding, rule replay/diff, cross-project merge, partial repair, and backup encryption remain separate increments. This keeps the slice coherent and prevents weak restore semantics from expanding into silent reconciliation.

## Consequences

Existing export consumers and the then-current fifteen-tool MCP registry remained compatible in 0.4. The later 0.5 replay-preview tool is additive and does not transport backup content. The backup duplicates the ownership subgraph's cited evidence and snapshots by design so the validator can detect disagreement. Backup files are local, portable, privacy-checked JSON but are not encrypted or cryptographically signed. Operators must protect them as potentially sensitive business records.
