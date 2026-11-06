# Current audit

Access date for all web sources: **2026-09-09**.

## SEOmonster baseline before companion changes

| Item | Recorded baseline |
|---|---|
| Branch | `chore/self-updating-tests-badge` |
| Commit | `827fa2a60e5957b00c1e7c79c53ac431c58d806b` |
| Initial worktree | Clean (`git status --short --branch` showed only the branch line) |
| Package/version | `seo-monster` 0.9.3; import package `seo_mcp` |
| Python | 3.11 or newer |
| MCP dependency | `mcp>=1.0.0,<2` |
| Tool registry | 70 registered tools |
| Prompt registry | 13 prompts |
| Offline tests | 576 passed in 0.82 seconds |
| Lint | `ruff check src/ tests/`: all checks passed |
| MCP smoke | stdio initialize succeeded; server name `seo-mcp`; 70 tools; 13 prompts; `system_status` returned a successful envelope |

The baseline commands were:

```sh
git status --short --branch
git rev-parse HEAD
uv run --no-sync pytest -q
uv run --no-sync ruff check src/ tests/
```

## Existing architectural contracts

- One MCP server instance owns one authoritative tool registry, prompt registry, and dispatcher.
- Every advertised tool is dispatchable. Unknown tools return a safe `INVALID_INPUT` envelope.
- Tool results use the stable shape `{"ok": true, "data": ..., "error": null}` or `{"ok": false, "data": null, "error": ...}`. Provider failures are converted rather than raised through the transport.
- Clients are lazy and injected at the dispatcher seam, which keeps the suite offline and mockable.
- Configuration path precedence is explicit argument, `SEO_MCP_CONFIG`, then the default path. Individual environment values override TOML values. Missing or malformed optional configuration degrades safely.
- The two console scripts, `seo-monster` and the compatibility alias `seo-mcp`, remain intact.
- Tool annotations declare read-only, destructive, idempotent, and open-world hints. Cloudflare write operations use destructive mode and, for higher-blast-radius actions, matching confirmation tokens. Other public write tools remain part of SEOmonster's current surface and are not called by the companion.
- Package, manifest, release workflow, and registry alignment are already regression-tested.

The companion therefore attaches at the public serialized result envelope. It does not change `src/seo_mcp`, the registries, prompts, configuration resolution, handlers, or clients.

## Repository material inspected

- `README.md`, `DESIGN.md`, `CHANGELOG.md`, `pyproject.toml`, package manifests, and CI/release workflows
- Current and historical local planning documents, used only to understand prior architectural decisions and not copied into distributable artifacts
- `src/seo_mcp/server.py`, `prompts.py`, `config.py`, `errors.py`, tool registries, provider clients, and test seams
- All test modules and the existing smoke, prompt, manifest, config, error, and write-gate tests

## OpenSEO current product and roadmap

Primary sources inspected:

- Product home: <https://openseo.so/>
- Feature catalog: <https://openseo.so/features>
- Site audit: <https://openseo.so/features/site-audit>
- Rank tracking: <https://openseo.so/features/rank-tracking>
- Saved keywords: <https://openseo.so/features/saved-keywords>
- AI visibility: <https://openseo.so/features/ai-brand-visibility>
- MCP feature: <https://openseo.so/features/mcp>
- Agent skills: <https://openseo.so/docs/skills>
- Three-month roadmap: <https://openseo.so/roadmap>
- Repository: <https://github.com/every-app/open-seo>
- Releases: <https://github.com/every-app/open-seo/releases>
- License: <https://raw.githubusercontent.com/every-app/open-seo/main/LICENSE>

Identifiers recorded on the access date:

- Latest published release: **v0.1.7**, release commit `ac9ee48`, shown by GitHub as released on 2026-09-02.
- Latest visible `main` commit: `3632f408528cd588fec98c3a174af8ea0ad205e8`, authored on 2026-09-03.
- Repository package version: `0.1.7`.
- License: MIT, copyright Ben Senescu (2026).

### Established shipped capabilities

The official product and repository surfaces establish keyword research and saved keywords, rank tracking, domain and backlink research, site audits, AI visibility and prompt exploration, GSC and GA4 access, remote MCP, agent skills, projects/workspaces, shared project context, self-hosting, and a human-facing application. The v0.1.6 release records shared goals, positioning, competitors, key pages, and writing preferences. The current commit history also records multi-user workspace roles and invitations, so collaboration is no longer a credible absence even though the public roadmap still lists related teammate work.

OpenSEO's self-hosted research features depend on a DataForSEO key. Its README states that the hosted service adds a percentage to DataForSEO request costs. This is a product-cost difference, not evidence that paid data is unnecessary for all SEO tasks.

### Published near-term roadmap

The three-month roadmap lists teammate access, community skills, bot authentication for audits, improved in-app agent and design consistency, IndexNow, AI prompt tracking, Google Business Profile and Maps tracking, scheduled and custom reports, client sharing, content-writing workflows, agent-readiness checks, and Bing Webmaster Tools. It explicitly does not plan a keyword-gap page, advanced JavaScript-rendered audits, or keyword-density optimization.

### Scoped absence finding

The inspected official feature catalog, MCP page, skills catalog, v0.1.6/v0.1.7 release notes, repository README, and three-month roadmap do **not describe** these capabilities:

1. A portable evidence graph that binds each material recommendation to immutable raw inputs, source time, transformation/rule version, exact calculation, caveats, and deterministic replay.
2. An experiment ledger that joins baseline evidence, a separately approved plan, later observed evidence, and a non-causal comparison record.
3. Explicit page/query/topic ownership and routing decisions across related sites, with conflicts bound to exact observed participants and evidence.

Repository-scoped searches for evidence graph, provenance, deterministic replay, experiment ledger, cross-site ownership, and approval/rollback concepts produced no relevant OpenSEO implementation result. This supports a bounded competitive conclusion: these capabilities were absent from the inspected shipped and published near-term surfaces. It is not a claim about private work, unindexed branches, or future releases.

## Hypothesis disposition

| Hypothesis | Result |
|---|---|
| Roughly 70 tools and 13 prompts | Confirmed exactly: 70 and 13. |
| Local-first MCP engine spanning GSC, GA4, PageSpeed/CrUX, Cloudflare, IndexNow, technical/on-page, content, and AI-citation workflows | Confirmed by registries, clients, tests, and documentation. |
| Live first-party data, composability, envelopes, caveats, controlled remediation are strengths | Confirmed, with the qualification that write gating differs by tool. |
| Durable state, longitudinal evidence, workflow continuity, collaborative review, experiments, portfolio governance, and focused inspection are gaps | Partly confirmed. Durable workbench state, provenance, and experiment continuity are gaps. SEOmonster already has portfolio-oriented analysis tools. Collaboration is an engine gap but OpenSEO already ships it. |
| OpenSEO ships the generic product layer | Confirmed and broader than the initial hypothesis because shared project context, GA4, and multi-user workspaces now ship. |
| A dashboard, rank tracker, keyword tool, or more prompts would differentiate | Rejected. Those are parity work at best. |
