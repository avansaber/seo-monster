# ADR 0009: Separate local Codex transport from the hosted ChatGPT plugin

- Status: accepted
- Date: 2026-09-09

## Context

SEOmonster is a local-first stdio MCP server. Local Codex clients can start stdio servers, but hosted ChatGPT consumes remote MCP tools through plugins and does not read local Codex configuration. Existing MCPB, Cursor, MCP Registry, and client configuration manifests do not constitute a portable OpenAI Agent Plugin.

Treating all MCP hosts as equivalent would create false compatibility claims and could expose SEOmonster write-capable tools without the remote authentication, tenant isolation, safety annotations, and approval controls they require.

## Decision

Keep the existing local stdio distribution unchanged for Codex CLI, Codex IDE, and the ChatGPT desktop Codex host. Add a separate portable Agent Plugin package backed by a thin Streamable HTTP facade for hosted ChatGPT and plugin-capable Codex surfaces.

The facade consumes SEOmonster only through its public MCP/result-envelope contract, materializes `seomonster-result/v1`, and initially exposes a curated read-only tool set plus non-executing Workbench workflows. It cannot import `seo_mcp` internals or route external writes. Local and hosted transports must pass the same normalization, replay, provenance, uncertainty, and write-denial tests.

Host compatibility is established by host-specific gates. A protocol handshake alone is insufficient evidence for a ChatGPT or Codex product claim.

## Consequences

SEOmonster remains local-first and independently releasable. Hosted ChatGPT support adds operational work for Streamable HTTP, OAuth, tenant isolation, and security review without forcing those concerns into the engine. The plugin can package skills and a remote connection for both OpenAI products, while existing users retain the direct local path.

No remote endpoint registration, tunnel, workspace publication, public submission, or production deployment is authorized by this ADR.

## 0.3 implementation refinement

The plugin currently ships stdio; the same Workbench contract also runs on authenticated loopback HTTP for testing. Seven of fifteen tools write local records and are annotated accordingly. Ownership tools may inspect and save pending proposals/plans but cannot declare final ownership. “Externally read-only” must not be mislabeled as globally read-only. No approval tool, arbitrary engine-tool proxy, URL fetcher, caller-selected database path or tenant argument is exposed. A separate operator capture CLI invokes four reviewed engine reads through the public MCP protocol. Remote production authorization remains deferred; a bearer-protected localhost listener is not a multi-user service.

## 0.4 implementation refinement

The facade remains at fifteen tools: eight reads and seven local-record writes. An actual Codex CLI simulation of model-mediated backup transport changed an exact JSON number representation (`16.0` to `16`), correctly invalidating component/provenance hashes and consuming excessive context. Backup and restore therefore remain operator-CLI-only. The facade explicitly excludes backup export, preview, and apply until an opaque staged-handle or resource transport can preserve exact bytes without model transcription. Hash validation is not weakened to accommodate a host.

The root portable manifests are canonical; the Codex-specific compatibility manifest supplies presentation and skills while no `extensions.com.openai` block is needed yet. Installation is not performed automatically. No marketplace, app ID or remote registration is fabricated.

## 0.5 implementation refinement

The facade adds `wb_rule_replay_preview` as its ninth read tool, bringing the total to sixteen while retaining seven local-record writes. The tool accepts only a snapshot identifier within the configured project, verifies the immutable raw capture, runs installed deterministic rules in memory, and reports analytical drift and dependent records. It cannot load historical code, apply a migration, rewrite human state, approve, or execute. This is an additive fixed-project inspection contract, unlike the rejected model-transcribed backup payload.

## 0.6 implementation refinement

The facade adds `wb_deployment_observation_inspect` as its tenth read tool, bringing the total to seventeen while retaining seven local-record writes. It can inspect only an observation already in the configured project. Deployment-observation import remains operator-CLI-only because it asserts an external event and reads a local file. The MCP tool cannot select a path, create an observation, approve a plan, or execute the described operation.
