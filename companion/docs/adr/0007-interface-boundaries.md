# ADR 0007: Put rules in an application service, not the CLI or future UI

- Status: accepted
- Date: 2026-09-09

## Context

A human inspection surface is valuable, but interface code should not become the product thesis or a second rules engine.

## Decision

`Workbench` is the application boundary for ingest, board, inspection, planning, decisions, experiments, and export. The v0.1 CLI is a thin JSON interface. A future local API will call the same service. A future accessible UI will consume the API and display state; it will not recompute rules, infer missing values, render fetched HTML, or bypass policy.

## Consequences

The current slice stays small and agent-friendly. Rich collaboration UX is deferred. CLI result envelopes make automation errors explicit and keep the interface testable.

