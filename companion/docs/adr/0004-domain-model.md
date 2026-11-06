# ADR 0004: Separate observed, inferred, recommended, decided, planned, and measured entities

- Status: accepted
- Date: 2026-09-09

## Context

Collapsing data and action into one issue row loses epistemic state and encourages approval or causal overclaims.

## Decision

Model portfolio, site, project, snapshot, evidence, finding, recommendation, decision, action plan, experiment, ownership assignment, ownership conflict, ownership decision, ownership action plan, and deployment observation separately. Projects store approved business context with a fixed no-truth-score claim policy. Evidence stores observed/unavailable values. Findings are rule outputs. Recommendations are candidates. Decisions record human state. Plans describe local previews, dependencies, and rollback. Experiments reference baseline and outcome evidence. Ownership conflicts are system-generated observations; ownership assignments and final resolutions are explicit operator assertions. Deployment observations preserve operator-provided evidence about changes performed outside the workbench and do not execute them.

## Consequences

The model is more explicit than a task list and supports later graph queries. Storage schema 3 retains JSON ID arrays for finding-to-evidence and ownership-conflict edges and adds append-only deployment observations; a join table may replace the ID arrays in a forward migration. The duplication between generic and ownership-specific decision/plan tables is deliberate: it prevents an observed cross-site signal from being silently treated as an ordinary recommendation or approval.
