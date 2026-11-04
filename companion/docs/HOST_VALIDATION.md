# SDK 2 qualification scope

Current protocol acceptance targets SDK 2.3.0, the engine's 70-tool/13-prompt contract and the companion's 17-tool local facade. Automated fixtures cover stdio, loopback HTTP, capture failures and the complete evidence/decision/export workflow. Native-host installation, plugin acceptance and hosted authentication require their own qualification. Earlier dated host-run records below retain the versions actually observed and are not a new SDK 2 host-installation result.

# Simulation and OpenAI host validation, 0.6.0

Validation dates: 2026-09-09 and 2026-09-12. This is a local implementation record, not a hosted ChatGPT or public plugin release claim.

## 0.6 deployment-observation increment

Simulation rejected immutable plan revisions as the first change because the legacy compatibility contract mutates plan/recommendation state and decisions do not directly identify a plan. A parallel revision table would create competing approval histories. The corrected slice adds only append-only observations of changes performed outside the workbench, with a frozen plan snapshot, source and artifact metadata, operator verification assertion, rollback reference, caveats, and approval decisions whose asserted timestamps predate the observation. Legacy decisions have no separate immutable recording time, so this ordering is not treated as attested authorization chronology.

The simulation also found that backup/v1 was coupled to SQLite schema 2. Storage now migrates forward to schema 3, current backup output is version 2 with a separately hashed change-ledger/v1 component, and valid backup/v1 remains accepted as an empty change ledger. Project export v1 and ownership/v1 are unchanged.

Deployment import is CLI-only and reads a bounded, duplicate-key-rejecting local JSON file. MCP adds one read-only fixed-project inspection tool, bringing the registry to seventeen tools: ten reads and seven local-record writes. No MCP import, approval, deployment, URL fetch, or external executor exists.

Seventeen focused simulations cover idempotency, no-execution behavior, approval-timestamp ordering, late/backdated-approval history freezing, unsafe/ambiguous input, stored-row tampering, bounded loading, cross-project scope, multi-plan linkage, replay dependency closure, missing-database side effects, non-overwriting CLI export, backup v2 round trip, and legacy backup/v1 restore. Independent Web-growth review passed the final bounded candidate after reproducing and driving seven fixes. The full source suite passed 690 tests; the clean wheel passed 112 tests with two expected engine-only skips and two upstream warnings.

Codex CLI 0.147.0 then exercised the independently built 0.6.0 wheel against a new synthetic database. All fourteen required project, evidence, replay, plan, experiment, export, ownership, and deployment-observation MCP tool selections returned successful structured envelopes; no non-Workbench tool was selected. Persisted-state checks found one draft recommendation plan, one pending `agent_draft` ownership decision, one draft ownership plan, one observational experiment with a +19 clicks delta, and one deployment observation marked `no_preceding_approval_timestamp`. The host reported causation as unestablished, approved context unchanged, and no external write. Raw host logs remain in a private temporary directory and are not distributable artifacts.

## 0.5 rule replay increment

Version 0.5 adds deterministic, preview-only replay of one immutable stored raw snapshot under the installed ruleset. It verifies snapshot identity and project/site scope, re-runs normalization and rules in memory, validates generated provenance, and reports installed/stored versions, canonical digests, added/removed/changed identifiers, and dependent decisions, plans, and experiments. Human recommendation state is validated but excluded from the analytical comparison, so approval is preserved and does not appear as rule drift. Historical ruleset execution, automatic migration, approval, and external execution remain unavailable.

The first simulation run produced five failures. Four established product gaps; one exposed an invalid test helper and was corrected before production code. A second test mistake attempted a nonexistent direct decision API instead of the real plan-approval workflow and was also corrected. Fourteen final increment scenarios cover deterministic and non-mutating replay, same-ID tampering, simulated installed-rule version churn, approval preservation, complete recommendation and ownership dependency impact, corrupted, missing, cross-project, invalid-workflow-state, and malformed-provenance rejection, unavailable GA4 evidence, CLI behavior, and refusal to create a missing database. Stdio and authenticated localhost HTTP exercise the same fixed-project read contract.

Independent Web-growth review reproduced two additional gaps before the candidate was frozen: ownership-ledger dependents were omitted from replay output, and CLI preview created/migrated a nonexistent database before failing. Both were fixed and regression-tested. The initial security scans were invalidated by those changes and are not release evidence; fresh scans apply only to the rebuilt candidate.

GA4 property-to-site binding was evaluated and deferred. A numeric property identifier does not establish whether one or several configured sites produced a row, and `landingPage` has no hostname. Adding a durable binding also requires operator assertion, supersession, portable/backup representation, row-host precedence, and safe rule-migration semantics. No site is inferred from the property ID, and the installed rule version remains unchanged.

Final checks for this increment:

```sh
PYTHONPATH=companion/src uv run --no-sync pytest -q -p no:cacheprovider tests companion/tests
# 673 passed: 576 engine + 97 companion

uv run --no-sync ruff check --no-cache src tests companion/src companion/tests companion/scripts
# All checks passed!

/tmp/clean-environment/bin/python -m pytest -q -p no:cacheprovider companion/tests
# 95 passed, 2 skipped, 2 upstream deprecation warnings
```

Codex CLI 0.147.0 selected `wb_rule_replay_preview` and all twelve earlier required evidence, plan, experiment, export, and ownership tools. Persisted-state checks passed and no executor was available. The Workbench registry is now sixteen tools: nine reads and seven local-record writes.

## 0.4 portability increment

Version 0.4 adds `seomonster-workbench-backup/v1` as a self-contained wrapper around the unchanged project and ownership formats. Operator CLI commands create the backup, preview a restore, and transactionally apply only create or exact no-op. Full graph, version, identity, reference, provenance, canonical hash, approved-context, privacy, causality, and no-execution invariants are validated before target mutation. Merge, overwrite, rename, deletion, downgrade, repair, and external execution remain unavailable.

Four pre-implementation simulations failed against 0.3 as expected: project export lacked portfolio identity, its generic privacy validator accepted both a dangling recommendation and a future format, and there was no preview/apply boundary. The revised plan preserved both existing export formats and introduced a wrapper instead of mutating a frozen contract. Sixteen adversarial portability scenarios now cover component disagreement, hash tampering, truth-score and secret input, ambiguous/malformed JSON, preview non-mutation, create/restore/no-op, byte-identical replay, conflicting targets, and deliberate mid-restore rollback.

An attempted model-facing MCP backup design was rejected after actual Codex CLI 0.147.0 testing normalized one exact JSON number from `16.0` to `16`. The validator correctly rejected the changed component/provenance hashes, and the payload consumed excessive model context. Hashing was not weakened. Backup export, preview, and apply are absent from the MCP registry and are regression-tested as unknown tools. The Workbench facade therefore remains fifteen tools: eight reads and seven local-record writes.

The corrected source and installed-wheel Codex host workflows each selected all twelve required evidence, experiment, and ownership tools, produced the expected local draft/observational records, and had no executor. A harness invocation with a Python interpreter missing the optional MCP dependency also exposed a misleading handshake failure; the script now fails early with a direct installation diagnostic.

Final local checks for this increment:

```sh
PYTHONPATH=companion/src uv run --no-sync pytest -q -p no:cacheprovider tests companion/tests
# 659 passed: 576 engine + 83 companion

uv run --no-sync ruff check --no-cache src tests companion/src companion/tests companion/scripts
# All checks passed!

/tmp/clean-environment/bin/python -m pytest -q -p no:cacheprovider companion/tests
# 78 passed, 2 skipped, 2 upstream deprecation warnings
```

The installed CLI completed backup → create preview → restore → exact no-op → backup, and the two backup files were byte-identical with the same SHA-256. The portable plugin and bundled skill validators passed after the MCP boundary correction. A fresh wheel and source archive were built locally; no publish, commit, or push followed.

## Outcome

The companion provides a separate, optional MCP/Agent Plugin integration above the unchanged SEOmonster engine. Version 0.3 ships fifteen fixed-project Workbench tools: seven accurately annotated local-record writes and eight reads. Ownership MCP writes create pending proposals/plans only; final ownership assignment and resolution remain operator CLI operations. No approval or external executor is exposed. The operator capture command uses a reviewed four-tool engine read allowlist and public MCP envelopes, never engine internals.

The plugin-creator and skill-creator guidance informed the isolated package structure, compatibility manifest, installation prerequisites and focused evidence-review skill. Portable manifests passed the official Agent Plugin 1.0.0 schemas; the local plugin and skill validators also passed. The official schemas were fetched for validation only, not vendored. No OpenSEO code, product language, assets or visual design were reused. Runtime dependencies remain separately distributed under their upstream licenses.

## Simulation findings fixed

| Reproduced risk | Resolution and active regression control |
|---|---|
| Replay reset an approved recommendation | Immutable derivation inserts retain human state; replay-after-approval test |
| Experiment could reference another project's recommendation | Project and evidence-lineage checks before creation |
| Omitted freshness became current | Defaults to unknown; confidence and caveats reflect uncertainty |
| Contradictory tabular signals became observed | Explicit contradictory evidence; no recommendation generated |
| Reload silently replaced approved business context | Exact reload only; changed context is refused without rewriting claims |
| Baseline could be compared to itself | Requires later capture, matching subject/type/tool/scope and non-overlapping known periods |
| GSC/GA4 fixtures did not match actual result shapes | Added real-shape GSC prior/current and GA4 dimensions/metrics-array normalization tests |
| Plan revisions and export overwrite were silent | Existing changed plans and existing export files are refused |
| Multi-record decisions could partially commit | Shared transaction and deliberate mid-approval failure/rollback regression |
| Generated provenance could become incomplete | Runtime derivation validation before commit, tested by deliberately deleting calculation provenance |
| Common secret names escaped export checks | Expanded key/value/path patterns; rejected input rolls back MCP writes without echoing sensitive values |
| A later comparison could overwrite a recorded outcome | A different outcome requires a separate experiment |
| Parent JSON ignore policy hid fixtures and manifests | Narrow reviewed-file exceptions plus Git-ignore packaging regression |
| Host interpreted dependencies as prose steps | First host run safely refused it and recovered; input-schema description now specifies existing plan IDs |
| Engine status carries a private config path | Privacy rejection retained; real-engine smoke uses a relative empty fixture config; documented live-status limitation |
| Unrelated-site captures contaminated a project | Argument/result property and page-host checks before commit; all generated affected pages must be in project scope |
| Export checksum described canonical payload rather than file bytes | SHA-256 now covers exact UTF-8 export bytes, verified against the file |
| Newer incompatible data hid a valid outcome | Lookup filters tool, type, resolved parameters, argument scope and measurement overlap |
| SQLite schema 2 leaked into the existing project export v1 | Project export remains `schema_version: 1`; ownership uses a separate ownership/v1 format and regression schema check |
| An old ownership decision could still produce a plan after participants changed | Changed evidence/participants create a new open conflict; inactive conflicts refuse new plans |
| Cross-site visibility could be mistaken for intended ownership | Observation, human declaration, pending proposal, final operator assertion and execution are separate entities/states |
| Missing or partial ownership metrics could become a confident numeric impact | Missing values stay null, impact becomes unavailable, and stale/provider-limited evidence reduces confidence |
| A `days` alias could hide incompatible resolved response windows | Canonical scope prefers response-resolved dates and dimensions before comparison |
| A newer zero-row capture left older visibility active | Property coverage is recorded even with no rows; later no-data supersedes covered evidence without becoming zero |
| Multiple final decisions could silently compete | New decisions require explicit supersession; malformed legacy parallel currents are exposed as ambiguous |
| A superseded decision could still produce a plan | Plans require the sole current decision on an active conflict |
| Ownership exports accepted weak or cross-project references | Runtime validation checks typed states, uniqueness, project lineage, evidence/snapshot links and plan dependencies |
| A narrow URL-prefix property retired evidence from an ancestor scope | URL-prefix coverage is one-way containment; `/docs` cannot retire root `/blog`, while root covers nested scopes |

The first six regressions were run before their fixes and all six failed as expected. Missing information is never converted to zero. Website claim text remains approved source material; none of these checks assess its truth.

## Automated evidence

Pre-0.3 baseline: 624 tests passed (576 engine + 48 companion); lint clean. Original pre-companion engine baseline is recorded in `AUDIT.md`.

```sh
.venv/bin/python -m pytest -q tests companion/tests -p no:cacheprovider
# 643 passed: 576 engine + 67 companion

.venv/bin/ruff check src tests companion/src companion/tests companion/scripts --no-cache
# All checks passed!
```

The parent handshake checks all 70 tools and 13 prompts in order, complete metadata/schema/annotation digests, successful `system_status`, and unknown-tool safe `{ok,data,error}` behavior. An additional real-engine capture uses `probe=false` and an empty fixture config.

The transport suite performs initialize/list/call and the complete workflow through actual stdio subprocesses and authenticated localhost HTTP. It validates structured envelopes and metadata, rejects missing authentication, hostile Origin, oversized requests, cross-project IDs and excluded approval/execution tools, and compares deterministic snapshot/recommendation IDs across transports. It is offline apart from localhost sockets.

The fixture harness passes all nine original scenarios with zero failures and all four write-denial checks true. Nineteen ownership tests add exact-query replay, cross-site and path-prefix scope, resolved-window comparisons, zero-row retirement, human declarations, decision supersession and ambiguity, plan/export references, participant reopening, stale/partial/missing data, Unicode normalization, CLI workflow, migration, and deliberate provenance/write-gate regressions. The CLI demo still produces three baseline opportunities and a non-executing plan, then an observational comparison: clicks +19, impressions +230, CTR approximately +0.0077, position -1.5. It explicitly notes unequal measurement windows and does not claim causation.

## Build and clean installation

Local wheel and source archive builds passed. The source archive includes the schemas, fixtures, plugin fallback dotfiles, skill, license, scripts and documentation; the wheel contains the runtime package and entry points. No database, credentials, private planning files, model transcripts or local configuration are included. The plugin still requires the separately installed MCP extra.

Reproduce with fresh output directories:

```sh
uv build --project companion --out-dir /tmp/seomonster-workbench-dist
uv venv /tmp/seomonster-workbench-clean
uv pip install --python /tmp/seomonster-workbench-clean/bin/python \
  '/tmp/seomonster-workbench-dist/seomonster_workbench-0.3.0-py3-none-any.whl[mcp]' pytest
/tmp/seomonster-workbench-clean/bin/python -m pytest -q companion/tests -p no:cacheprovider
/tmp/seomonster-workbench-clean/bin/sm-workbench evaluate --fixtures companion/evaluation/fixtures
```

The clean environment resolved MCP 1.30.0; the existing engine environment uses MCP 1.27.1. Engine-only compatibility checks are intentionally skipped when SEOmonster is absent from the clean companion environment; they pass in the parent environment. Fresh dependencies currently emit Starlette/AnyIO test-client deprecation warnings; transport tests still pass. These warnings are upstream maintenance signals, not suppressed failures.

Final 0.3 clean-wheel result: **65 passed, 2 skipped, 2 upstream deprecation warnings in 2.09s**. The two skips are exactly the engine-only tests. Archive inspection checked 79 entries and privacy-validated 22 distributable fixture/contract/plugin/example JSON documents; no database, bytecode, private directory, real credential, or virtual environment was present. Git packaging checks use repository-root-relative paths, and `git status` lists the required manifests, schemas and fixtures as untracked/eligible rather than ignored. No files were staged or committed.

## Actual Codex host evidence

Codex CLI **0.147.0**, Python **3.14.0**, macOS arm64. The new 0.3 synthetic host run passed the expanded workflow. Successful tool events and persisted experiment/plan/ownership state are checked, not just model narration.

```sh
python companion/scripts/codex_smoke.py --run --fixtures companion/evaluation/fixtures
# Source/editable installation lane: passed

/tmp/seomonster-workbench-clean/bin/python companion/scripts/codex_smoke.py \
  --run --installed --fixtures companion/evaluation/fixtures
# Installed-wheel lane: passed
```

The 0.3 run successfully selected all twelve required tools: project-get, board, recommendation inspection, plan draft, experiment start/compare, project export, ownership board/inspection, pending ownership decision draft, ownership plan draft, and ownership export. The stored ownership decision remained `pending` with `agent_draft` provenance; both plans remained drafts; the expected +19 observed clicks comparison was recorded; and no executor was available. The host explicitly kept observed visibility separate from ownership/cannibalization. Raw Codex events remain in private temporary directories, not distributable artifacts. One workflow run is not a comprehensive tool-selection evaluation.

## Established versus still open

- Established: local companion installation, deterministic evidence, ownership, backup, and installed-rule replay workflows; engine protocol compatibility; 16-tool stdio/HTTP conformance; portable manifest/skill validation; CLI operator flow; and actual Codex CLI use of the replay and pending ownership workflows.
- Not established: desktop app plugin installation, complete plugin/new-chat skill behavior, hosted ChatGPT developer-mode authentication, multi-user isolation or production directory availability.
- The HTTP listener is intentionally loopback-only, bearer-protected and single-owner. OAuth, tenant authorization, production rate controls and public HTTPS/tunnel registration are not implemented or authorized by these tests.
- Privacy patterns are conservative safeguards, not a guarantee that arbitrary free text has no personal information. Local database tampering and cryptographic source attestation remain out of scope.
- Full import/merge, authenticated human approval, deployment-record import, causal experiment design, and deeper regional/migration portfolio policy remain roadmap items. An accessible UI is a later thin-client phase after those contracts stabilize.
- The competitive conclusion remains limited to OpenSEO's official shipped/roadmap descriptions inspected on 2026-09-09: portable recommendation-level replayable lineage, the evidence-linked observational experiment ledger, and explicit cross-site page/query/topic ownership/routing were not described there. This does not establish absence from private code/plans or customer demand.
- Web-growth independently identified and retested ownership scope/window, zero-row retirement, decision supersession, superseded-plan and portable-reference failures. Its final path-containment probe reported conflict counts **1 -> 1 -> 0** before the empty `/docs` capture, after it, and after an empty root capture; both no-data records remained unavailable. Its focused ownership suite passed **19 tests**, its complete independent suite passed **643 tests**, and Ruff and `git diff --check` were clean. It found no remaining actionable bug in the requested ownership scope. GA4 numeric property-to-site binding remains a disclosed residual limitation; this scoped sign-off does not establish hosted ChatGPT or multi-user readiness.

## Next three increments

1. Mature immutable plan revisions, deployment-record import, observation metadata, confounder notes, and multi-window experiment comparisons.
2. Specify GA4 operator bindings, shared-property scope, hostname precedence, supersession, portability, and rule-migration semantics before implementation.
3. Add regional/language and migration-transition portfolio governance plus cross-snapshot conflict diffs and bulk dry-run validation.

UI/UX and hosted ChatGPT infrastructure are deliberately outside these next increments. The open-source CLI, MCP, local ledger, deterministic fixtures, and portable formats remain the primary product surfaces. Desktop plugin acceptance may continue as a bounded compatibility check, not as the product roadmap driver.

No push, deployment, release, external repository creation, public registration, provider mutation, sitemap submission or indexing request was performed. Existing unrelated worktree changes were preserved.
