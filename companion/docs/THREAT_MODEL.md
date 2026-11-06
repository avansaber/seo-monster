# Concise threat model

## SDK 2 boundary controls

The MCP facade validates tool names and arguments before local operations, returns fixed failure envelopes and removes request/exception values from SDK diagnostics. Capture uses an explicit four-tool read allowlist, rejects writes before process creation, refuses remote schema references and does not retry provider calls. Failed observations retain their source errors and unknown cost. HTTP uses loopback binding, bearer authentication, explicit Host checks, all-Origin rejection and bounded bodies. The CLI's optional-MCP diagnostics do not install packages or start authentication automatically.

## Assets and trust boundaries

Assets are approved business context, provider-derived SEO data, analytical provenance, human ownership/decisions, deployment observations, local backups, and future credentials. Inputs cross from SEOmonster/provider captures, a local deployment-observation document, or a backup into validation, then into a local SQLite ledger and exports. Version 0.6 includes local MCP transports, operator-only capture and deployment-observation imports, portfolio ownership, transactional restore, and read-only rule replay preview, but no external executor.

## Implemented 0.4 controls (supersede v0.1 inventory below)

- Stdio and HTTP tools use a configured, fixed project. IDs are checked before reads or writes. No tool takes a filesystem path, arbitrary engine tool name, remote URL, approver identity or tenant selector.
- Capture attribution checks configured hostnames, GSC argument/result properties and page dimensions before commit; generated affected pages must belong to configured hosts. Outbound migration destinations and competitive SERP URLs remain inert observations, not owned pages. Legacy GSC captures with page-level attribution but no explicit property retain an unknown-property caveat and low confidence. GA4 numeric property-to-site ownership is not independently attested in this slice.
- HTTP binds only `127.0.0.1`; constant-time bearer validation, all-Origin rejection and SDK Host checks precede access. Bodies are bounded to 2 MB with a 10-second receive timeout; returned data is bounded to 4 MB. This is not a public authentication design.
- Seven local-record tools advertise write semantics. Ownership MCP writes create pending proposals/plans only. Final ownership assignments, decisions, deployment-observation import, backup, and restore are not model-facing tools. The tenth read tool can inspect an already imported observation. No approval or external execute tool is registered. External-write attempts fail as unknown tools; the core capability policy also denies every execution mode.
- Rule replay verifies the stored raw snapshot and project scope before executing installed deterministic code in memory. It reports digests, changed identifiers, and dependent records but exposes no apply/migration operation. Human workflow state is excluded only after validating its allowed states. Historical rulesets are not downloaded or dynamically executed.
- Cross-site signals require exact comparable query/page evidence from two configured site scopes. They are labeled observations, never inferred ownership or cannibalization. Decisions retain the exact pages/evidence; changed participants reopen the conflict, and inactive conflicts cannot produce new plans.
- Each local mutation is transactional; validation failures roll it back. Generated recommendation provenance is checked before ingestion commits. Replay cannot reset a human decision. Experiments cannot mix projects, scopes, overlapping known periods or non-later captures, or replace a recorded outcome.
- Privacy guards reject common secret keys, credential-bearing URLs, bearer strings and home/private paths; they are conservative pattern checks, not a guarantee that arbitrary free text is free of personal information. Claims are not rewritten or scored by these guards.
- Operator-CLI backup input is bounded, must match exact version/record shapes and component hashes, and is validated for complete project/portfolio/reference/provenance consistency before target inspection. Preview restores no records. Apply is one transaction and refuses partial merge, overwrite, rename, deletion, downgrade, cross-project identity, incomplete targets, and mismatched shared ownership evidence. Backup payloads are excluded from MCP because a host may normalize exact JSON values while transcribing tool arguments. Hashes detect changes but do not authenticate the producer; backups remain sensitive local business records.
- Deployment-observation import accepts strict bounded JSON, rejects secret-like values and private paths, and links only to a same-project plan. It freezes the plan and approval-timestamp snapshot but relies on operator-asserted source, timestamps, and verification metadata. It does not prove authorization chronology, an external change, or a postcondition. No URL is fetched and no target is written. Inspection and idempotent re-import validate stored hashes before returning a row.
- The capture command reviews a four-tool allowlist before spawning an operator-configured executable, checks current input schemas and read-only hints, sets a timeout and retains raw envelope/capture receipts. It cannot route writes. Provider reads can consume quota; captured cost remains unknown unless explicitly supplied.
- MCP result text can contain hostile fetched instructions. Server instructions and the bundled skill identify it as inert untrusted content. This reduces prompt confusion but cannot prove that every host model will resist all injection; future host adversarial evaluations remain necessary.
- Loopback authentication does not provide OAuth, tenant isolation, per-user approval, rate limits or production audit logging. Do not publish or tunnel this listener as a production service. Multi-user exposure requires a new reviewed boundary.

## Original v0.1 threat inventory and forward requirements

| Threat | Current control | Future requirement |
|---|---|---|
| Secrets in captures or exports | Fixtures contain no credentials. Export rejects populated secret-like keys and private home paths. Source locators are basenames. | Add configurable redaction, structured secret scanning, encrypted local storage, and key rotation before live capture automation. |
| SSRF and hostile URLs | v0.1 never fetches a URL. URL strings are stored as data. | A live fetch adapter must enforce scheme/host policy, DNS/IP revalidation, redirect limits, response limits, timeouts, and private/link-local address blocking. |
| Tool or prompt injection in fetched content | Strings are never evaluated, rendered as HTML, or inserted into a model prompt. Common instruction markers add an inert-data caveat. | Encode by default in UI, isolate content from instructions, tag trust origin, and test adversarial render paths. |
| Destructive operations | No external client or execute command exists. Policy denies external writes in evaluation, preview, recommendation, and even approved mode because the executor is absent. | Use authenticated, scoped approvers; typed previews; expiring action-bound approvals; idempotency keys; provider capability allowlists; audit receipts; rollback verification. |
| Approval confusion | Recommendations, pending decisions, approvals, and execution availability are separate fields and records. Approval does not create execution authority. | Prevent confused-deputy use by binding approver, project, plan hash, provider, scope, expiry, and exact operation. |
| Database tampering | Content hashes and deterministic IDs reveal some changes, but SQLite is not tamper-proof. | Add append-only event hashes, signed exports, backup/restore, and integrity verification if evidence becomes compliance-relevant. |
| Cross-project data exposure | Single-user local database and explicit project IDs. | Before multi-user exposure, add tenant scoping to every key/query, row-level authorization, audit logging, invitation controls, and security tests for identifier enumeration. |
| Malicious import or resource exhaustion | JSON shape validation; no code execution; SQLite parameter binding. | Add byte, nesting, row-count, and decompression limits before general import support. |
| Remote MCP authentication and tenant confusion | No hosted endpoint exists in v0.1. | Bind every request to an authenticated user and tenant; validate OAuth discovery, issuer, audience, scope, expiry, and refresh behavior; never accept tenant or approver identity from tool arguments alone. |
| Plugin metadata drift or unsafe tool exposure | Existing host manifests are not treated as an OpenAI plugin and the current host test is protocol-only. | Pin and diff tool names, schemas, annotations, and instructions; rescan metadata; default the hosted facade to a read-only allowlist; require review for each changed action. |
| Remote transport abuse | No Streamable HTTP listener exists in v0.1. | Enforce TLS, origin/host policy, request and response limits, timeouts, concurrency/rate limits, safe logging, request IDs, and denial-of-service protections. |

## Explicit residual risks

- A local user with filesystem access can read or alter the database.
- Marker detection is advisory and not a substitute for isolation in a future UI/model integration.
- Provider metadata is asserted by the capture producer; v0.3 does not cryptographically authenticate it.
- Observational experiment comparisons can still be misinterpreted outside the application, so the non-causal label must remain in exports and future views.
- A future ChatGPT/Codex plugin creates a new remote and multi-user trust boundary; it cannot inherit the local SQLite threat assumptions.
