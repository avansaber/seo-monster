# ADR 0012: Rule replay preview before GA4 binding

- Status: accepted after simulation
- Date: 2026-09-12

## Candidate scope

The next increment evaluates two adjacent provenance gaps: explicit GA4 property-to-site attribution and deterministic preview of the current rules against a stored raw snapshot.

A numeric GA4 property identifier does not prove which configured website produced a row. A property may cover one site, several sites, or several hostnames while a report may contain only `landingPage` rather than `hostName`. The workbench must not infer a binding from a path or from project membership. Until an operator-declared, portable binding contract exists, GA4 evidence must state that site attribution is unavailable.

Rule replay should reconstruct an `AdaptedResult` from the immutable stored raw capture, run the current normalizer and rules without saving anything, validate all provenance and affected-page invariants, and compare the preview with stored evidence, findings, and recommendations. It must expose current and stored rule versions, canonical digests, added/removed/changed identifiers, and dependent decisions/plans/experiments that would require review. It must never silently migrate, approve, delete, or execute.

## Questions for simulation

1. Does current GA4 evidence explicitly distinguish a numeric property from proven site attribution?
2. Can a stored raw snapshot be replayed without creating or changing any database record?
3. Can the preview detect both rule-version drift and same-ID content tampering?
4. Does it report human and experiment records that depend on replaced derived identifiers?
5. Are cross-project, missing, or corrupted snapshots rejected before analysis?
6. Is the result deterministic over the same snapshot and installed rule version?

## Simulation evidence

All five initial checks failed against 0.4. Four were product gaps: GA4 evidence did not carry an explicit binding state and no replay/diff service existed. One was a simulation defect: the first non-mutation check called a nonexistent in-memory export helper instead of the established portable export builder. That test was corrected before production code.

The GA4 proposal is not a safe small addition. A numeric property can legitimately cover multiple configured sites, while `landingPage` alone has no hostname. A durable binding needs many-to-many scope, row-level hostname precedence, explicit operator assertion and supersession, a separate portable contract or versioned backup component, and rule-version transition semantics. Adding an `unavailable` field to normalized evidence also changes content-addressed identifiers; doing that under the existing rule version would violate deterministic replay, while a global rule-version bump could duplicate derived records before migration semantics exist.

## Revised decision

Implement only a read-only replay preview in this increment. Keep the installed ruleset unchanged. Reconstruct the exact adapter input from the stored raw snapshot, validate content identity and project/site scope, run the current normalizer and rules in memory, validate generated provenance, and compare analytical record projections. Human recommendation state is excluded from the analytical comparison so approval cannot look like rule drift.

The result reports current and stored versions, canonical digests, added/removed/changed identifiers for evidence, findings, and recommendations, plus decisions, plans, and experiments that depend on affected analytical records. Preview never inserts, updates, deletes, approves, or executes. There is no apply command. Expose it through the CLI and one accurately read-only fixed-project MCP tool because the host passes only a snapshot identifier, not a provenance-sensitive bulk payload.

Dependency reporting traverses direct evidence and downstream decision links, including ownership assignments, conflicts, decisions, and plans that cite replayed evidence. The `replay-preview` command requires an existing database file before constructing the store. Both behaviors have dedicated regressions.

GA4 property-to-site binding remains deferred until its entity, supersession, portability, backup, row-host, and rule-migration contracts are designed together. Property ID alone remains explicitly documented as insufficient evidence; the workbench does not infer a site from it.

## Consequences

The database schema, saved-result adapter, project/ownership/backup formats, existing tool inputs, and installed rule version remain unchanged. Replay can reveal drift or tampering but cannot reproduce a historical ruleset that is no longer installed and cannot repair the ledger. A future rule migration must consume this preview and introduce explicit supersession rather than relying on re-ingestion.

## Initial boundary

Do not create a GA4 binding table until one-to-many property scope, row-level hostname evidence, supersession, portability, backup compatibility, and conflict semantics are specified together. Do not add replay apply/migration.
