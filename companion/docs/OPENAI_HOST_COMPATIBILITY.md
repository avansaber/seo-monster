# ChatGPT and Codex compatibility plan

- Status: local implementation 0.6.0; protocol and actual Codex CLI evidence/ownership/replay workflow tested; deployment-observation inspection added read-only; model-facing backup/import transport rejected; desktop/plugin/hosted gates remain open
- Access date for OpenAI documentation: **2026-09-09**

## Outcome we require

SEOmonster must remain usable as a local-first MCP server in Codex while gaining an optional, portable plugin path for hosted ChatGPT and Codex surfaces. “MCP-compatible” is only a protocol-level statement. “Works in ChatGPT/Codex” may be claimed only after the relevant host-specific gate below passes and records the host version, plugin version, tool-contract digest, test prompts, and results.

The companion must normalize the same `seomonster-result/v1` capture regardless of which approved host transport produced it. No host integration may bypass the saved-result boundary, provenance requirements, or approval policy.

## Current OpenAI surface model

Official OpenAI documentation currently distinguishes these paths:

- Local Codex clients support stdio and Streamable HTTP MCP servers. The ChatGPT desktop app, Codex CLI, and Codex IDE extension share the same Codex MCP configuration.
- Hosted ChatGPT web does not read local Codex configuration. It receives remote MCP tools through plugins.
- A portable Agent Plugin uses root `plugin.json` and optional root `mcp.json`; OpenAI-specific presentation and registered-server mappings belong under `extensions.com.openai`.
- A remote plugin endpoint must use Streamable HTTP, normally at `/mcp`, and be reachable through public HTTPS or the Secure MCP Tunnel during development. Public submission requires a public production endpoint.
- Tools need stable names, descriptions, input/output schemas, and accurate safety annotations. Tool-selection and complete-plugin behavior must be evaluated separately.

Sources: [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), [plugin overview](https://learn.chatgpt.com/docs/plugins), [build an MCP server](https://developers.openai.com/plugins/build/mcp-server), [package a plugin](https://developers.openai.com/plugins/build/plugins), and [connect and test a plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt).

## Current repository status

| Surface | Current evidence | Status |
|---|---|---|
| MCP protocol over local stdio | Automated initialization, `tools/list`, `prompts/list`, `system_status`, and `{ok,data,error}` envelope test; 70 tools and 13 prompts pinned for SEOmonster 0.9.3 | Verified at protocol level |
| Codex local MCP | Codex CLI 0.147.0 completed project → board → evidence inspection → draft plan → experiment → comparison → export using synthetic inputs | Actual local host verified; broad prompt-selection evaluation remains open |
| ChatGPT desktop local MCP | Officially shares local Codex MCP configuration; no recorded SEOmonster host run exists | Planned host verification |
| Portable ChatGPT/Codex plugin | Separate portable `plugin.json`/`mcp.json`, Codex fallback and evidence/ownership-review skill; official schemas and local plugin/skill validators pass | Implemented package; marketplace installation/new-chat validation remains open |
| Local Streamable HTTP | Same 17 Workbench tools, bearer auth, localhost-only binding, fixed project, request/response limits and Origin rejection | Automated full-workflow parity includes evidence, replay preview, experiments, ownership and explicit backup/deployment-import absence |
| Hosted ChatGPT remote MCP | No public/tunneled endpoint, OAuth deployment or registered plugin connection | Deferred; not claimed working |

These statuses are intentionally separate. The passing generic MCP test does not establish ChatGPT web, plugin installation, authentication, tool selection, or end-to-end host behavior.

## Architecture decision

Preserve two optional distribution lanes over one versioned tool contract:

1. **Local lane:** Codex CLI, Codex IDE, and the ChatGPT desktop Codex host start the published `seo-monster` package over stdio. This remains local-first and backward compatible.
2. **Companion lane:** a separate MCP server exposes seventeen fixed-project Workbench tools. Ten read records and seven write local records, accurately annotated. Rule replay is preview-only; deployment observations are inspection-only. Ownership writes can only draft pending proposals/plans. The host cannot import a deployment observation, declare final ownership, migrate rules, merge records, overwrite a project, or transport backups. It has no approval or external-execution tool. Stdio is the packaged default; authenticated loopback Streamable HTTP is implemented for transport conformance. A future hosted deployment needs a separate authenticated tenant boundary, not merely a public bind flag.

The hosted lane initially excludes sitemap submission, indexing requests, IndexNow, Cloudflare mutation, deployment, publication, and every other external-write tool. Adding any write tool requires a separate ADR, authenticated per-user authority, accurate annotations, an action-bound confirmation flow, and explicit approval. The local SEOmonster registry remains unchanged so existing clients keep all current commands and tools.

## Deliverables

### Local Codex and ChatGPT desktop

- Add a repo-scoped example `.codex/config.toml` that invokes `uvx seo-monster` without embedding secrets.
- Add a repeatable clean-environment smoke script that performs initialization, lists tools and prompts, calls `system_status`, calls one offline-safe tool or mocked fixture path, verifies stderr/stdout separation, and exits without external writes.
- Run the same direct, indirect, follow-up, negative, unavailable-provider, and prohibited-write prompts in a new Codex session and a new ChatGPT desktop Codex session.
- Record Codex/desktop version, Python and `uv` versions, platform, registry digest, prompts, tool choices, arguments, results, errors, and approval behavior.

### Portable plugin

- Create an isolated `plugins/seomonster-workbench/` package with portable `plugin.json`, `mcp.json`, focused skills, and no secrets or workstation paths.
- Keep portable identity canonical. Use `extensions.com.openai` only for OpenAI presentation and registered MCP mappings; add `.codex-plugin/plugin.json` only if a compatibility fallback remains necessary.
- Validate the Agent Plugins schemas, relative paths, license metadata, starter prompts, and clean-install behavior in CI.
- Keep the existing MCPB, Cursor, and MCP Registry artifacts independent; do not rename or reinterpret them as the OpenAI plugin.

### Hosted read-only MCP facade

Implementation correction: “read-only” refers to **external systems**, not to all local Workbench operations. Project creation, capture ingestion, plan drafts and experiment records are local writes and advertise `readOnlyHint=false`. The facade never proxies live provider calls. An operator-only stdio capture command separately allowlists `system_status`, `gsc_search_analytics`, `gsc_compare_periods`, and `ga4_run_report`, checks their current schemas and read-only hints, and saves the public envelope before ingestion.

The implemented HTTP transport is single-owner and loopback-only, with a minimum-length bearer secret. It is not OAuth, a tenant authentication system, or production hosting. The following production requirements remain release gates:

- Implement Streamable HTTP and initialization instructions over the same stable adapter contract.
- Expose only an allowlisted read-only subset plus non-executing Workbench workflows; accurately annotate all tools and declare structured output schemas.
- Add per-request tenant context, OAuth discovery, authorization checks, rate/cost metadata, timeouts, response-size limits, SSRF controls, redaction, and auditable request IDs before any multi-user test.
- Materialize an immutable saved-result capture before analysis so local and hosted paths replay identically.
- Return explicit unavailable, stale, partial, and provider-limited states; never coerce them to zero or confident findings.

## Required release gates

| Gate | Evidence required | Claim unlocked |
|---|---|---|
| Protocol | Automated stdio and Streamable HTTP initialize/list/call tests, schema validation, envelope regression, no stdout contamination | “MCP protocol compatible” |
| Codex local | Fresh install plus recorded real Codex host workflow using the repo-scoped configuration | “Works with Codex locally” |
| ChatGPT desktop | Fresh install plus recorded real desktop Codex-host workflow | “Works in ChatGPT desktop locally” |
| ChatGPT developer mode | Approved public HTTPS or Secure MCP Tunnel connection, tool scan, auth/error tests, positive/negative tool-selection evals | “Tested in ChatGPT developer mode” |
| Complete plugin | Local marketplace installation, new-chat workflow eval, skill/tool integration, asset/path checks, prohibited-write test | “Plugin works in ChatGPT and Codex” |
| Public directory | Production security review, privacy/terms artifacts, compatibility matrix, approval, and successful OpenAI review | “Publicly available plugin” |

Passing one gate never implies a later gate. Public or workspace publication, production hosting, tunnel registration, or external-system testing requires explicit user approval and is outside the current authorized session.

## Evaluation set

The host harness must reuse the existing offline SEO scenarios and add:

- direct and indirect prompts that should select a particular read tool;
- follow-ups that reuse an earlier project, page, snapshot, or recommendation identifier;
- negative prompts that should not activate the plugin;
- malicious fetched text that must remain inert evidence;
- missing, stale, contradictory, paid-provider-unavailable, and partial-failure responses;
- attempts to select or invoke excluded write tools;
- authentication expiry, insufficient scope, timeout, disconnect, and schema mismatch;
- a complete capture-to-recommendation-to-plan-to-experiment-to-export workflow;
- parity assertions that local stdio and hosted Streamable HTTP captures normalize to the same deterministic evidence and recommendations.

## Build, defer, reject

**Built locally:** 16-tool MCP facade, stdio/HTTP fixture parity, local bearer gate, fixed-project scope, operator-only capture client, validated operator-CLI backup preview/apply, read-only rule replay preview, portable plugin with Codex fallback, schema/path/version checks, evidence/ownership skill and real Codex smoke tooling.

**Simulation corrections incorporated:** preserve approval across replay; refuse silent context/plan revision; reject cross-project and non-later experiment references; keep unknown freshness and contradictory rows explicit; match real GSC/GA4 shapes; make writes atomic; reject incomplete generated provenance before commit; deny export overwrite. The first real host run supplied prose instead of plan dependency IDs, received a safe error and recovered. A later backup-transport run changed `16.0` to `16`, correctly failed hash validation, and demonstrated that full backup payloads do not belong in model-mediated arguments. The input-schema description and CLI-only backup boundary now encode those findings.

Independent Web-growth review added project-site attribution gates, compatible-outcome lookup and file-byte checksum corrections. Required JSON fixtures/manifests are now explicitly trackable despite the parent credential-ignore policy, tested from the repository root. Exact current evidence and remaining host gates are recorded in `HOST_VALIDATION.md`.

**Build locally next:** complete direct/indirect/follow-up/negative prompt-selection cases, desktop plugin-install smoke and full manual test record. Keep account-dependent host setup separate from offline CI. Consider MCP backup/restore only after an opaque staged-handle or resource transport preserves exact bytes without model transcription.

**Defer pending approval:** Secure MCP Tunnel or public HTTPS registration, OAuth provider configuration, live ChatGPT developer-mode connection, workspace sharing, public submission, and production hosting.

**Reject:** claiming ChatGPT support from stdio tests alone; exposing the complete 70-tool registry remotely by default; storing user provider credentials in plugin metadata; importing `seo_mcp` internals into the facade; or weakening approval gates to satisfy a host demo.
