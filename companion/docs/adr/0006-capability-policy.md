# ADR 0006: Approval is data, not execution authority

- Status: accepted
- Date: 2026-09-09

## Context

SEO workflows can mutate production pages, caches, redirects, sitemaps, and indexing systems. A local recommendation or approval record must not accidentally authorize those operations.

## Decision

Define capabilities as local read, local record, and external write. Permit local work in evaluation, preview, recommendation, and approved modes. Deny external writes in every v0.1 mode. An approved plan remains non-executable because there is no executor. Provide no execute command and no external provider clients.

## Consequences

This slice cannot deploy even by accident. Future execution needs its own ADR and action-bound authenticated approval, preview, idempotency, rollback, and audit receipts. Recording approval remains useful for handoff to a separately authorized system.

