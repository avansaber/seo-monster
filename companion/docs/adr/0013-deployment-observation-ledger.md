# ADR 0013: Append-only deployment observations before plan revisions

- Status: accepted after simulation
- Date: 2026-09-12

## Initial hypothesis

The next decision-loop increment should add immutable action-plan revisions and then attach external deployment records to a selected revision.

## Simulation findings

The existing compatibility contract makes a plan identifier deterministic per recommendation and records approval by mutating both the plan and recommendation state. Adding a second revision history beside that mutable row would create competing answers to which plan was approved. The `decisions` table also identifies the recommendation but does not carry a direct `plan_id`, so historical approval cannot safely be reassigned to a newly invented revision chain.

Simulation also exposed a portability defect: backup/v1 requires SQLite storage schema 2 exactly. A naive migration 3 would therefore reject every earlier valid backup even if the new table were additive.

## Decision

Defer plan revisioning until an event-source migration can retain legacy reads while making plan-specific draft, approval, supersession, and withdrawal events authoritative.

Implement the smaller closed-loop gap as an append-only deployment-observation ledger. It records evidence about a change performed outside the workbench and never performs the change. Import is a local operator CLI action from a strict, privacy-checked JSON document. MCP can list and inspect records but cannot import them.

Each observation stores the project and existing plan identifiers; observation and recording timestamps; environment, source, optional artifact SHA-256, verification assertion, summary, operations, rollback reference, and caveats; the exact decoded plan snapshot and its canonical hash at import time; an integrity-hashed as-of-import approval snapshot containing decision identifiers whose asserted timestamps precede the observation or explicit `no_preceding_approval_timestamp`; canonical source and full-record hashes; and an always-false external-execution capability.

Do not reject a historically observed change merely because no earlier approval timestamp exists. That would erase a safety-relevant fact. Preserve it and label the missing timestamp explicitly. Because the legacy approval API accepts an operator-supplied timestamp and has no separate immutable recording time, the ledger describes asserted timestamp order, not attested authorization chronology. A later approval must not rewrite the frozen plan or approval snapshot.

Add `seomonster-workbench-change-ledger/v1` as a separate portable graph. Keep project export v1 and ownership/v1 unchanged. Add backup/v2 containing the project, ownership, and change-ledger components with independent hashes. Continue validating and restoring backup/v1 into schema 3 as an empty change ledger.

Verification states are operator assertions with source metadata. They are not workbench proof that deployment succeeded, that a postcondition is true, or that an SEO outcome was caused by the change.

## Consequences

The workflow now connects a plan to later external-change evidence without introducing an executor or retroactive approval. Historical plan state survives later mutable legacy approvals. Current backup output advances to v2, while v1 remains a supported input contract. Immutable plan revisions, withdrawal/supersession events, authenticated approver identity, and causal deployment/outcome correlation remain future migrations.
