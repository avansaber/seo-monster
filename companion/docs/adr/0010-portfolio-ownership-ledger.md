# ADR 0010: Keep observed cross-site visibility separate from declared ownership

- Status: accepted
- Date: 2026-09-09

## Context

A portfolio can have several pages visible for the same query for legitimate or harmful reasons. Rankings alone cannot establish intended ownership, user intent, cannibalization, or the correct route. Fuzzy topic inference would make replay and review harder and could turn incomplete provider data into an overconfident governance claim.

## Decision

The first ownership rule detects only exact queries after NFKC normalization, case folding, and whitespace collapse. Punctuation, stems, synonyms, and topics remain distinct. A signal requires comparable latest GSC query/page evidence attributed to at least two configured site scopes; most-specific origin/path scope wins. Comparability uses resolved response dates and normalized defaults/aliases, not an unresolved `days` request. Latest selection includes attributable zero-row snapshots, so new no-data evidence removes old visibility instead of preserving or converting it to zero. The record preserves all page observations, evidence and snapshot identifiers, measurement scope, timestamps, calculation, rule version, caveats, impact, and confidence. Missing metrics remain null and make impact unavailable. Stale, unknown, degraded, or partial inputs reduce confidence.

Intended query/topic ownership is a human-declared assignment. A resolution is one of `accept`, `differentiate`, `consolidate`, or `route` over the conflict's exact participant and evidence set. A new participant or newer evidence set creates a new conflict and does not inherit the old resolution. Assignment and decision supersession is explicit; one current decision is allowed per exact conflict. Ambiguous legacy state is surfaced rather than silently resolved, and plans require the sole current decision on an active conflict.

Final assignments and decisions are operator CLI operations. MCP exposes inspection plus pending decision and plan drafts only. Plans contain no executable steps and no external system client exists.

Ownership history is exported as `seomonster-workbench-ownership/v1`. The schema types the ownership records, states, and execution gates; runtime validation checks duplicate identities and site, snapshot, evidence, conflict, decision, supersession, dependency, and provenance references. Existing `seomonster-workbench-export/v1` remains at schema version 1; SQLite storage migration numbers do not alter either portable contract.

## Consequences

The rule is intentionally narrow and may miss semantically related conflicts. In exchange, its output is deterministic, explainable, provider-neutral after capture, and safe to review. Regional/language policy, migration transitions, fuzzy candidate discovery, and portable import/merge remain later increments.
