# Competitive capability matrix

Evidence basis and access date are recorded in [`AUDIT.md`](AUDIT.md). “Absent” means not described in the inspected official shipped or three-month roadmap surfaces, not proof about private plans.

| Capability | SEOmonster today | OpenSEO shipped/roadmap | Classification | Decision |
|---|---|---|---|---|
| Data sources | Broad first-party and free core: GSC, GA4, PSI, CrUX, HTTP/crawl signals, Cloudflare; optional DataForSEO, Open PageRank, and AI engines | GSC, GA4, crawl/Lighthouse, AI/search and competitive data, with DataForSEO central to self-hosted research | **SEOmonster advantage** | Preserve provider breadth and honest degradation. |
| Technical auditing | Composable URL, robots, sitemap, redirect, schema, hreflang, linking, Lighthouse/CrUX tools | Site-audit UI, crawl history, optional Lighthouse; JS rendering explicitly not planned | **parity** | Integrate findings, do not rebuild a crawler. |
| Content planning | GSC opportunities, gaps, cluster map, brief data, content workflows | Keyword research/clustering skills, project context, content workflows planned | **parity** | Consume output; no new generic content generator. |
| Rank and AI visibility | Rank attribution and sampled AI-citation tracking, but no durable product surface | Shipped rank tracking, AI visibility, prompt explorer; more prompt tracking planned | **OpenSEO advantage** | Do not build a rank tracker. Ingest snapshots from available providers. |
| Project memory | Engine calls are primarily stateless | Shared project context with goals, positioning, competitors, key pages, and preferences | **OpenSEO advantage** | Store decision evidence, not another generic workspace profile. |
| Portfolio management | Explicit page/query/topic ownership, exact-query cross-site visibility conflicts, deterministic replay, and routing decisions | Multiple projects/workspaces; no inspected cross-site page/keyword ownership surface or published near-term roadmap item | **differentiated opportunity** | Implemented locally as an evidence-backed ownership ledger; recheck before public comparison. |
| Workflow orchestration | 13 MCP prompts over small composable tools | MCP plus repeatable Agent Skills and an in-app agent | **parity** | Integrate through a stable envelope; reject prompt-count competition. |
| Review and approval | Engine has per-tool safety controls but no durable review ledger | Human-facing review UI; inspected surfaces do not describe typed plans with dependencies, rollback, and separate decision/execution states | **differentiated opportunity** | **Build:** draft plans and record approval state while keeping execution absent. |
| Provenance | Provider caveats and transparent envelopes, but no durable recommendation-to-input graph | Inspected surfaces show source data and reports but do not describe portable rule/calculation lineage or deterministic replay | **differentiated opportunity** | **Build:** content-addressed snapshots, evidence links, rules, calculations, and caveats. |
| Longitudinal measurement | Point and comparison tools; no experiment record | Rank and crawl history ship; scheduled reports planned | **differentiated opportunity** | **Build:** baseline-to-outcome ledger explicitly denying unsupported causation. |
| Provider cost | Useful first-party/free workflows and optional paid enhancement | Self-hosted research requires DataForSEO; hosted use adds a service margin | **SEOmonster advantage** | Retain provider cost metadata and null/unavailable states. |
| Self-hosting | Local stdio package, user-owned credentials | Docker and Cloudflare self-hosting | **parity** | Stay local-first; defer hosted multi-user service. |
| UX and collaboration | MCP-host UX only | Modern web application and shipped multi-user workspace support | **OpenSEO advantage** | Do not imitate the UI. Begin with a focused CLI inspection surface. |
| Extensibility | Python tool modules, injected clients, MCP prompts | MIT TypeScript application, MCP, skills, plugin surfaces | **integrate** | Use versioned protocol adapters and portable JSON rather than internal imports. |

## Capabilities demonstrated beyond the inspected OpenSEO surface

The implementation, rather than feature-count rhetoric, supports the differentiation claim:

1. **Replayable analytical provenance.** `gsc_baseline.json` produces stable snapshot, evidence, finding, and recommendation identifiers on repeated ingestion. Each recommendation carries input evidence IDs, capture time, rule ID/version, exact calculation, and caveats. Inspection returns the untouched source envelope.
2. **Non-causal experiment continuity.** The demo binds a recommendation to baseline evidence, a dated observation window, later evidence for the same subject, and per-metric deltas. The stored interpretation states that causation is not established.
3. **Typed approval separation.** A plan contains dependencies, rollback notes, and a pending decision. Approval can be recorded locally, but the product has no executor and policy still denies external writes.

Only the first two are required to establish the wedge. The third is the safety mechanism that makes them operationally useful.

## Explicit rejects

- Generic analytics dashboard
- Standalone rank tracker
- Standalone keyword database or keyword-gap page
- Backlink or competitive-data clone
- More prompts as the primary product
- OpenSEO-compatible visual imitation or product-language reuse
- Remote hosted MCP in the initial wedge
