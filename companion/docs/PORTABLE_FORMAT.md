# Portable formats

`seomonster-workbench-export/v1` is the user-owned interchange format produced by `sm-workbench export`. Its JSON Schema is `schemas/export-v1.schema.json`.

`seomonster-workbench-ownership/v1` is a separate ownership-history format produced by `sm-workbench ownership-export`. Its schema is `schemas/ownership-v1.schema.json`. The formats are separate so SQLite storage migration 2 and ownership evolution do not add fields to the established project export v1 contract.

`seomonster-workbench-change-ledger/v1` is the separate append-only change history produced by `sm-workbench change-export`. Its schema is `schemas/change-ledger-v1.schema.json`.

`seomonster-workbench-backup/v2` is the self-contained restore format produced by `sm-workbench backup`. Its schema is `schemas/backup-v2.schema.json`. It wraps the project, ownership, and change-ledger formats, the exact portfolio record, canonical component hashes, and an explicit restore policy. Historical backup/v1 remains a supported restore input.

## Contents

- `project`: name, portfolio reference, approved business context, and claim policy
- `sites`: canonical sites in project scope
- `snapshots`: full saved-result captures, public arguments, provider/cost metadata, hash, freshness, and basename locator
- `evidence`: normalized values or explicit unavailability plus provenance
- `findings`: versioned rule outputs, calculations, caveats, impact/confidence/effort, and affected pages
- `recommendations`: decision candidates and their complete analytical provenance
- `decisions`: pending or approved human decision records
- `action_plans`: local-only steps, dependencies, rollback notes, and execution availability
- `experiments`: hypothesis, baseline, observation window, outcome, deltas, and the non-causal interpretation
- `semantics`: durable interpretation rules for claims, provenance, causality, and execution

## Guarantees

- Arrays are deterministically ordered by stable identifier.
- JSON object keys are sorted when written.
- Snapshot, evidence, finding, and recommendation identifiers are content/rule-derived.
- Missing numeric values remain `null` or an unavailable evidence record; they are not changed to zero.
- Approved business context is preserved as parsed JSON and never receives a `truth_score` property.
- Export validation rejects populated secret-like keys and strings containing common private home-directory paths.
- Export v1 never represents execution as available.
- Project export v1 keeps `schema_version: 1`; the independent SQLite schema version is not substituted into this field.

## Ownership/v1 contents and guarantees

- Project and configured sites, ownership assignments, current and historical conflicts, pending/recorded decisions, and non-executing ownership plans
- The exact evidence and source snapshots referenced by conflicts or assignment bases
- Exact participant and evidence sets on every decision; a changed set is a different open conflict
- Explicit separation of observed visibility, human-declared ownership, pending proposals, final operator assertions, and execution
- Missing impressions remain `null`, impact becomes unavailable, and stale/provider-limited signals reduce confidence
- Every plan is `draft`, every step has `external_write: false`, and top-level execution availability is false
- Runtime reference/privacy checks and a versioned JSON Schema

## Change-ledger/v1 contents and guarantees

- Deployment observations imported locally after an action occurred elsewhere; the format cannot execute an action
- Exact source metadata, operations, verification assertion, rollback reference, caveats, observation hash, full-record hash, and stable identity
- A frozen plan snapshot and hash that remain unchanged if the legacy plan is approved later
- An integrity-hashed as-of-import approval snapshot containing decision IDs whose asserted timestamps precede the observation, or `no_preceding_approval_timestamp`; this ordering is not independently attested and later backdated decisions do not rewrite it
- Verification assertions are attributable operator records, not cryptographic proof or causal conclusions

## Backup/v2 contents and guarantees

- Exact portfolio metadata plus complete project export v1, ownership export v1, and change-ledger v1 components
- Canonical SHA-256 hashes for all components and runtime validation of shared project, plan, decision, site, evidence, and snapshot records
- Complete project-graph validation before preview or apply, including versions, record identities/shapes, references, provenance, states, dependencies, causality, privacy, and execution gates
- Deterministic `create`, `noop`, or `conflict` preview with no restored records
- One-transaction create-or-exact-no-op restore; any different, missing, extra, cross-project, or tampered record refuses the operation
- Preserved project and record identifiers; no rename, partial merge, repair, overwrite, deletion, downgrade, approval, or external execution

## Non-guarantees

- SQLite files and exports are not signed. Backup component hashes detect accidental or uncoordinated changes but are not authenticity signatures.
- Provider identity and capture time are asserted metadata, not cryptographically attested.
- Comparing two observations does not establish causation.
- Cross-project or conflict-resolving merge is not implemented in v0.6. Restore accepts only an absent project or exact replay.

## Compatibility

Readers must dispatch on `format_version`. Writers must not add fields to a v1 document whose schema rejects them; additive evolution requires a new schema/format version or a documented compatible schema revision. A breaking semantic change requires a new format major version. SQLite schema versions and portable format versions evolve independently.
