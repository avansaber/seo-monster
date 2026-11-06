# ADR 0003: Use local SQLite with forward migrations

- Status: accepted
- Date: 2026-09-09

## Context

The wedge needs durable relationships and transactions without a service dependency or paid infrastructure.

## Decision

Use Python's built-in SQLite driver. Record append-only numbered schema migrations in `schema_migrations`. Use foreign keys, parameterized SQL, and content-derived IDs for snapshots and deterministic derived records. Never mutate or compact raw snapshots automatically.

## Consequences

Install remains dependency-free and local-first. SQLite is not a collaboration database or tamper-proof ledger. Multi-user storage is deferred, and published migrations must never be edited in place.

