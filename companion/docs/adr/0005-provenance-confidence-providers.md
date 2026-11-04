# ADR 0005: Make provenance and uncertainty structural

- Status: accepted
- Date: 2026-09-09

## Context

Provider caveats, partial failures, stale data, and transformations are easily lost in summaries. Missing data is often misread as zero. This concern is separate from the truth of approved website claims.

## Decision

Every evidence record stores snapshot, source tool, arguments/hash, envelope hash, capture time, provider/cost metadata, freshness, transform/version, and caveats. Every material recommendation stores input evidence IDs, timestamp, rule ID/version, exact calculation, and caveats. Availability is explicit. Stale, unknown, degraded, and partial inputs reduce confidence. Approved business claims are preserved and never scored.

## Consequences

Recommendations are inspectable and replayable. Payloads are larger. Provider metadata is asserted until signed capture is designed. Confidence describes the analytical basis, never the truthfulness of business content.

