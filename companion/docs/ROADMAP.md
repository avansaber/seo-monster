# Phased roadmap

## Four public delivery parts

Planning update: 2026-10-03. These are proposed increments, not completed releases. The sequence follows a source-contract review and a 20-case tabletop simulation. Two read-only probes reproduced the multilingual word-count and zone-only Cloudflare recommendation defects. Implementation will add executable regression tests before changing production code.

| Part | Build | Independent public checkpoint |
|---|---|---|
| 1 Engine safety and maintenance | Multilingual extraction and downstream scoring; hostname-aware Cloudflare audit with explicit uncertainty; canonical metadata and provider disclosures; dependency and tracker review | Engine-only clean checkout, registry/envelope tests, reviewed public changes and passing public CI |
| 2 Workbench and local host installation | Complete companion packaging and CI; isolated Claude plugin; portable OpenAI plugin; exact-artifact installation and actual named-host tests | Public source installation and fixture workflow; host-specific compatibility matrix, with unverified surfaces stated explicitly |
| 3 Decision and experiment continuity | One authoritative plan lifecycle; legacy compatibility projection; immutable recording time; multiple observational outcome windows and portable migration | Synthetic lifecycle and multi-window CLI workflow, legacy compatibility, backup/restore and public CI |
| 4 Portfolio coverage and routing | Operator-mapped GSC/GA4/hostname coverage; setup versus measurement states; regional/language policy; migration transitions and conflict diffs; bulk dry-run validation | Public free-data portfolio workflow, deterministic reports, no silent attribution and final tracker dispositions |

Part 1 addresses public issue 12. Part 4 evaluates issue 11's optional provider contract before any integration; existing provider precedence remains unchanged by default and live paid calls are outside acceptance. The marketplace-notice issue needs a prepared acknowledgement, not a feature. Dependency updates require individual validation; MCP 2 remains outside the supported bound until a tested migration exists.

Ship an approved part before expanding into the next major increment. Commit scope must be complete for that checkpoint: companion-dependent CI and the companion package land together in part 2. Preserve other work in the existing checkout and use explicit candidate file manifests. A local commit does not establish public availability.

Every push requires two fresh completed security scans covering the final candidate and artifacts, including credentials, PII, sensitive material and private paths. Public pushes require explicit post-scan approval. Later changes invalidate the scans. Tags, package publication, directory submission, issue/PR mutations and website deployment need approval for their own effects. Private plans, coordination records, databases and raw host logs stay outside public artifacts.

Test Codex CLI, Codex desktop, ChatGPT desktop and Claude Code separately through fresh installation and a complete saved-fixture workflow. Generic MCP tests do not prove host support. Preserve direct MCP, Cursor and MCPB routes; do not replace shared configuration with host-specific syntax. Hosted ChatGPT, OAuth and public directory acceptance remain separate gates and are not prerequisites for public local functionality.

Keep schema identifiers stable and prepare exact schema files for separately approved website hosting. Update website version, date, counts and compatibility statements from delivered evidence, not planned values. Approved business claims remain unchanged. External execution remains absent.

The backlog sections below retain their detailed contracts and defer/reject decisions. Durable GA4 bindings, MCP backup transport, cross-project merge, automatic schedules, paid caching, historical runtime, causal statistics, fuzzy ownership, hosting and UI are outside these four bounded parts unless a later decision explicitly changes scope.

## Build now: evidence-to-decision slice

- Saved public-envelope adapter with provider and cost metadata
- Local SQLite schema and migration ledger
- Project, site, portfolio, snapshot, evidence, finding, recommendation, decision, action-plan, and experiment entities
- Deterministic rules for striking distance, same-query page routing, content-decay signals, and migration-route review
- Opportunity board and exact evidence/calculation inspection through the CLI
- Non-executing typed plans with dependencies, decision state, and rollback notes
- Baseline/outcome experiment comparisons with no causal claim
- Portable export with privacy and truth-score guards
- Offline scenarios and parent MCP compatibility checks
- OpenAI host-compatibility plan with separate protocol, Codex-local, ChatGPT-desktop, hosted-ChatGPT, complete-plugin, and publication gates
- Portfolio page/query/topic ownership with deterministic cross-site visibility conflicts, exact-participant decisions, evidence drill-down, and separate portable export
- Append-only local deployment observations with frozen plan/approval snapshots, separate portable change-ledger export, and no executor
- Backup v2 including the change ledger, with backward-compatible backup v1 restore

## Detailed backlog themes

The four public delivery parts above govern the current proposed delivery order. The following themes preserve the existing backlog detail; they do not place backup transport or durable GA4 bindings ahead of the engine-safety and public-installation parts.

1. **Workflow completeness and portability.** The self-contained backup wrapper and operator restore are implemented. Read-only rule replay/diff is also implemented for the installed ruleset, with dependency-impact reporting and no migration/apply path. Add an opaque staged-handle or resource transport before considering MCP backup/restore. GA4 property-to-site binding is deferred until many-to-many scope, hostname evidence, supersession, portability, backup, and rule-migration semantics are designed together. Cross-project merge remains separate; unknown provider scope and cost remain explicit.
2. **Decision and experiment maturity.** Local deployment-observation import is implemented and never executes. Next, design an event-source migration for immutable plan-specific draft, approval, supersession, and withdrawal history; then add observation scheduling metadata, confounder notes, multi-window comparisons, and decision/audit timelines. All conclusions remain observational unless an external experimental design establishes otherwise.
3. **Portfolio governance depth.** Add operator-declared regional/language routing policy, migration ownership transitions, cross-snapshot conflict diffs, and bulk dry-run validation. Preserve exact attribution; fuzzy topic discovery may suggest a review candidate but can never become ownership without an explicit declaration.

## Simulation-first delivery gate

Every increment follows this order before its implementation is considered ready:

1. **Specify the proposed contracts and invariants.** Record the intended entity, protocol, compatibility, uncertainty, provenance, approval, and no-external-write behavior without treating the proposal as implemented.
2. **Build adversarial offline simulations first.** Cover normal operation plus malformed imports, version skew, duplicate or contradictory records, cross-project references, missing/stale/provider-limited data, partial failure, malicious content, attempted execution without approval, and deterministic replay.
3. **Run the simulations against the current implementation.** Record expected failures as evidence of the gap; distinguish a missing capability from a regression or an invalid product assumption.
4. **Revise the plan before production code.** Remove weak scope, correct entity and migration semantics, update ADRs/threat model/portable contracts, and define exact acceptance tests from what the simulation exposed.
5. **Implement the smallest corrected slice.** Keep recommendation, approval, and external execution separate; preserve the SEOmonster public adapter boundary and all existing registry/envelope contracts.
6. **Rerun focused, full, clean-package, and independent validation.** Require deterministic fixture replay, parent tests, lint, MCP handshake/registry tests, clean-wheel tests, privacy/archive checks, and Web-growth review when available.

An increment cannot move from `planned` to `implemented` merely because code exists. It moves only when the revised acceptance tests pass and the implementation report records the exact results, remaining limitations, and any hypothetical conclusions.

## Functionality-first release policy

The open-source CLI, portable files, SQLite ledger, and MCP tools are the product. UI/UX is deferred until the decision, experiment and portfolio contracts have complete offline scenarios, useful real-world fixture validation, and at least one full portfolio workflow that can be completed without hidden manual state. A future UI should expose those contracts rather than create a parallel application layer.

Local Codex support is already verified. Additional desktop-plugin prompt selection can be tested opportunistically, but it does not outrank core SEO functionality. Hosted ChatGPT, OAuth, public endpoints, cloud accounts, billing, and multi-user infrastructure are not roadmap prerequisites.

## Defer

- Multi-user hosting and collaboration until tenant isolation and authenticated approval are designed
- Import/merge of complete exports until conflict semantics and migration compatibility are stable
- GA4 property-to-site binding until operator assertion, shared-property scope, hostname dimensions, supersession, portability, and rule migration are one coherent contract
- Causal experiment design, statistical power calculations, and change/deployment correlation until real user workflows establish requirements
- Paid-provider caching and budget controls beyond captured cost metadata
- Automated schedules and alerts; reproducible snapshots come first
- Similarity-based topic discovery and fuzzy conflict grouping until exact-query ownership is validated with real portfolio fixtures
- Public hosting, Secure MCP Tunnel registration, OAuth-provider setup, ChatGPT workspace sharing, and plugin-directory submission until explicitly approved
- Graphical UI/API work until the portfolio, portability, decision, and experiment contracts reach the functionality-first release gate

## Reject

- Generic dashboard, rank tracker, keyword tool, backlink explorer, or crawler clone
- Feature-count parity as a roadmap metric
- More MCP prompts as primary differentiation
- Automated fact-checking or truth scoring of approved website claims
- Automatic causation claims from before/after movement
- External execution embedded in the initial workbench
- Claiming ChatGPT or Codex product compatibility from generic MCP protocol tests alone
- Remotely exposing SEOmonster's complete write-capable registry by default
- Treating hosted infrastructure, account systems, or UI polish as evidence of SEO operating-system depth
- OpenSEO code, visual design, interface structure, assets, or product-language imitation
- Private planning documents or workstation paths in distributable artifacts

## Decision gates

Each increment must pass the simulation-first delivery gate and keep parent SEOmonster tests and the external MCP compatibility contract green. OpenAI host claims require the matching evidence gate in `OPENAI_HOST_COMPATIBILITY.md`; a passing handshake is not a substitute for a real-host workflow. Any external-write capability requires a separate ADR, authenticated approval model, preview/diff, idempotency and rollback semantics, threat-model update, and explicit user authorization before implementation or use.
