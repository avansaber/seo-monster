# ADR 0002: Use a versioned saved-result protocol

- Status: accepted
- Date: 2026-09-09

## Context

A durable record needs capture time, call arguments, provider availability/cost, freshness, and the raw result. The current SEOmonster envelope alone intentionally does not carry every capture concern.

## Decision

Define `seomonster-result/v1` in `schemas/saved-result-v1.schema.json`. Require tool, arguments, capture timestamp, provider metadata, freshness, and the untouched `{ok,data,error}` result. Accept additive fields. Hash the complete capture. Unknown successful tools become opaque evidence; failed envelopes become explicit unavailable evidence.

## Consequences

Replay has a closed input and provider limits remain visible. Producers must supply honest metadata. Breaking changes require a parallel adapter major version. The current runtime supports saved files only; a live MCP producer is a later adapter, not a storage shortcut.

