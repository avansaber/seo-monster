# Product brief

## Primary user

A technical SEO lead or owner-operator responsible for a small portfolio of related websites, using agents and multiple data providers but still accountable for deciding what changes are safe and whether later movement supports continued investment.

## High-value job to be done

When an SEO engine surfaces many plausible actions, help me choose one defensibly, inspect exactly why it was suggested, preserve the decision and proposed rollback, and compare later evidence without rewriting history or overstating causation.

## Problem

SEOmonster can collect and compute strong point-in-time evidence. The missing layer is continuity across runs: what exact input produced a recommendation, which rule and calculation transformed it, what a person decided, what plan was approved, and what was observed later. Generic workspaces and dashboards do not close that loop.

## Smallest coherent wedge

An **evidence-first SEO decision ledger**:

1. Capture public SEOmonster result envelopes as content-addressed snapshots.
2. Normalize them into evidence without discarding the raw input.
3. Run deterministic, versioned opportunity rules.
4. Present recommendations with impact, confidence, effort, caveats, affected pages, and evidence drill-down.
5. Draft a typed, non-executing action plan and record a distinct decision state.
6. Join baseline and later evidence in an observational experiment record.
7. Detect exact-query cross-site visibility conflicts from comparable evidence while preserving the distinction between observation and human-declared ownership.
8. Record explicit query/topic page assignments and exact-participant routing decisions with non-executing plans.
9. Export the project graph and ownership history through separate versioned portable formats.

This wedge implements portable analytical provenance, experiment continuity, and a portfolio ownership ledger. It deliberately does not infer topics, intent, ownership, or cannibalization from rankings; those meanings remain human assertions.

## Product principles

- This is an open-source, local-first operating system layer. Functional depth, inspectable data, reproducibility, portability, and composable CLI/MCP contracts take priority over UI polish or hosted-service infrastructure.
- CLI, JSON export, and MCP are first-class product surfaces, not temporary scaffolding for a future SaaS application.
- Approved website and business claims are source material. They are preserved, made extractable, and never assigned an automated truth score.
- Analytical provenance applies only to workbench observations, transformations, findings, and recommendations.
- Unknown, missing, stale, contradictory, partially failed, and provider-limited values remain explicit states.
- Recommendations are not approvals. Approvals are not executions. An executor is not present in v0.4. Backup restore is a local persistence operation, not approval or external execution.
- Free and first-party evidence remains useful without a paid provider.
- Raw inputs and portable exports belong to the user.
- SEOmonster can release independently because the adapter consumes only the public envelope.

## Non-goals

- Reimplementing SEOmonster tools or provider clients
- Crawling the web, calling live providers, or changing websites
- Building a generic analytics dashboard, rank tracker, keyword explorer, backlink tool, or content generator
- Automated truth, quality, or credibility scoring of approved website claims
- Automated causal attribution
- Multi-user hosting, billing, or a collaboration suite
- SaaS infrastructure, hosted accounts, cloud workspaces, or UI-led differentiation
- A substantial graphical interface before the underlying portfolio, decision, experiment, and portability workflows are complete and stable
- Visual or language parity with OpenSEO

## Success measures

Initial product measures are conformance measures, not invented market metrics:

- 100% of material generated recommendations reference at least one evidence record, timestamp, rule/calculation version, calculation, and caveat collection.
- Same snapshot hash plus same rule version yields the same finding/recommendation identifiers.
- Missing provider data never appears as numeric zero unless the source explicitly observed zero.
- Evaluation, preview, and recommendation modes produce zero external writes.
- Approved business context round-trips byte-equivalently as parsed JSON and gains no truth-score field.
- A user can move from saved result to inspected recommendation, draft plan, baseline, outcome comparison, and portable export in one local workflow.
- A multi-site user can reproduce an exact-query conflict, inspect every input, declare ownership, record an exact-participant resolution, and export the history without any external write capability.
- SEOmonster's existing MCP registry, prompt registry, handshake, result envelope, test suite, and lint baseline remain green.
- ChatGPT/Codex compatibility claims are withheld until the matching protocol, host, and complete-plugin gates have recorded evidence; local stdio and hosted transport must normalize the same capture deterministically.

Later outcome measures, to be validated with users, include time to reconstruct a recommendation, percentage of accepted recommendations with complete provenance, and percentage of post-change reviews that explicitly distinguish observation from causal inference.

## Differentiation claim

The inspected OpenSEO surfaces already cover generic workspaces, human UI, research, rank/crawl history, MCP, skills, and collaboration. The official surfaces inspected on 2026-09-09 did not describe portable recommendation-level evidence lineage with deterministic replay, a baseline-to-plan-to-outcome experiment ledger, or explicit page/query/topic ownership and routing across related sites. The workbench implements all three locally. This is a scoped evidence conclusion, not a claim that OpenSEO cannot add them; it must be rechecked before public comparison copy is published.
