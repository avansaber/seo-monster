# ADR 0001: Keep SEOmonster as an independent engine

- Status: accepted
- Date: 2026-09-09

## Context

SEOmonster has a stable, tested MCP and Python package surface. A product layer that imports its registries, handlers, or clients would couple release cycles and risk existing commands and hosts.

## Decision

Build the workbench as a separate package under `companion/`. Its runtime does not import `seo_mcp`. It consumes a versioned serialized capture of SEOmonster's public result envelope. SEOmonster owns live provider interaction; the workbench owns durable evidence, decisions, and experiments.

## Consequences

SEOmonster can ship independently and removing the companion does not affect it. The adapter cannot silently depend on internal Python objects. Live convenience is deferred until a read-only MCP client can materialize the same capture contract.

