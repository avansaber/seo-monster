# SEOmonster Workbench

SEOmonster Workbench is an experimental, open-source companion for SEOmonster. It turns saved SEOmonster results into a local evidence and decision record: source snapshots, deterministic findings, inspectable recommendations, cross-site page/query ownership, non-executing action plans, append-only deployment observations, and observational experiment comparisons.

It is not another SEO dashboard. SEOmonster remains the data and analysis engine; the workbench owns durable provenance and workflow continuity above the public result-envelope boundary.

## What the first slice proves

- A saved `{ok, data, error}` SEOmonster result can be ingested without importing SEOmonster internals.
- Raw source envelopes remain available next to normalized evidence.
- The same snapshot and rule version reproduce the same evidence, finding, and recommendation identifiers.
- An opportunity board exposes impact, confidence, effort, caveats, affected pages, inputs, and the exact calculation.
- Recommendation, pending decision, approval, and execution are separate states.
- A baseline and later outcome can be compared without claiming causation.
- Comparable GSC snapshots can expose exact-query visibility across configured sites without inferring ownership or cannibalization.
- Human-declared query/topic ownership, exact-participant decisions, and rollback-aware plans remain distinct from observed signals.
- A complete project can be exported as documented JSON without credentials or private source paths.
- A self-contained backup can be previewed, transactionally restored into an absent project, and replayed as an exact no-op without merge or overwrite.
- A stored raw snapshot can be re-run against the installed rule version in preview-only mode, exposing exact analytical drift and dependent records without mutation.
- A change performed elsewhere can be recorded from a local fixture with its source, frozen plan, approval-timestamp alignment, verification assertion, and caveats; the workbench still cannot execute it.
- Approved business context is preserved as source material. The workbench has no automated truth score for website claims.

## Install and run locally

From the SEOmonster repository root:

```sh
python -m venv companion/.venv
companion/.venv/bin/python -m pip install -e companion
```

Run the complete offline example:

```sh
companion/.venv/bin/sm-workbench \
  --db /tmp/seomonster-workbench-demo.db \
  demo \
  --fixtures companion/evaluation/fixtures \
  --output /tmp/seomonster-workbench-export.json
```

Run the evaluation harness:

```sh
companion/.venv/bin/sm-workbench evaluate \
  --fixtures companion/evaluation/fixtures
```

The CLI installation has no required runtime dependencies. MCP capture and serving use the optional extra and require MCP Python SDK 2.3 or later within major version 2:

```sh
companion/.venv/bin/python -m pip install -e "companion[mcp,test]"
```

Capture requires a matching SDK 2 engine build. The saved `{ok,data,error}` JSON boundary remains independent of engine imports. A failed provider call stays unavailable with unknown cost; it does not become an empty successful observation.

Run tests in the MCP runtime environment. Keep engine and companion lint in separate environments because their Ruff version ranges differ:

```sh
companion/.venv/bin/python -m pytest -q companion/tests
python -m venv companion/.lint-venv
companion/.lint-venv/bin/python -m pip install -e "companion[dev]"
companion/.lint-venv/bin/ruff check --isolated companion/src companion/tests companion/scripts
```

Install the matching engine in a separate integration environment when running engine contract tests. See [compatibility](docs/COMPATIBILITY.md) for the protocol boundary and [host validation](docs/HOST_VALIDATION.md) for qualification scope.

## Manual workflow

```sh
sm-workbench --db workbench.db init \
  --project docs \
  --name "Documentation SEO" \
  --business-context business-context.json \
  --site https://example.com

sm-workbench --db workbench.db ingest \
  --project docs \
  --file saved-seomonster-result.json

sm-workbench --db workbench.db board --project docs
sm-workbench --db workbench.db inspect --recommendation REC_ID
sm-workbench --db workbench.db replay-preview --project docs --snapshot SNAPSHOT_ID
sm-workbench --db workbench.db plan --project docs --recommendation REC_ID
sm-workbench --db workbench.db deployment-record \
  --project docs --plan PLAN_ID --file deployment-observation.json
sm-workbench --db workbench.db deployment-inspect \
  --project docs --observation OBSERVATION_ID
sm-workbench --db workbench.db change-export \
  --project docs --output change-ledger-v1.json
```

For a project with two or more configured sites, ingest comparable page/query GSC captures and use the ownership ledger:

```sh
sm-workbench --db workbench.db ownership-board --project docs
sm-workbench --db workbench.db ownership-inspect --conflict CONFLICT_ID
sm-workbench --db workbench.db ownership-assign \
  --project docs --key-type query --key "evidence first seo" \
  --owner-url https://example.com/evidence --role primary \
  --intent "Primary product explanation" --rationale "Operator-declared routing intent"
sm-workbench --db workbench.db ownership-decide \
  --project docs --conflict CONFLICT_ID --decision accept \
  --note "Operator accepts the current exact participant set."
sm-workbench --db workbench.db ownership-plan \
  --project docs --decision DECISION_ID \
  --rollback-notes "Restore any separately applied content or routing change."
sm-workbench --db workbench.db ownership-export \
  --project docs --output ownership-v1.json

sm-workbench --db workbench.db backup \
  --project docs --output workbench-backup-v2.json
sm-workbench --db restored.db restore-preview \
  --file workbench-backup-v2.json --project docs
sm-workbench --db restored.db restore \
  --file workbench-backup-v2.json --project docs
```

`ownership-assign` and `ownership-decide` are operator CLI commands. The MCP facade can draft a pending ownership proposal, but it cannot record final operator ownership or approval. A changed evidence/participant set is a new open conflict.

`approve` records a local human decision. It does not unlock or invoke an executor. This version provides no command for deployment, publication, URL or sitemap submission, IndexNow, Cloudflare mutation, or any other external write.

`deployment-record` imports a reviewed local JSON observation after a change occurred elsewhere. It is not a deployment command. The record freezes the plan as seen at import and compares asserted approval timestamps with the observation timestamp; this ordering is not independently attested. See `evaluation/fixtures/deployment_observation.json`. Backup v2 includes this ledger; backup v1 remains accepted for restore.

## Saved-result contract

The adapter input is defined by [`schemas/saved-result-v1.schema.json`](schemas/saved-result-v1.schema.json). Required capture metadata includes the public tool name, call arguments, timestamp, freshness, provider status and cost metadata, and the complete SEOmonster result envelope. See [`evaluation/fixtures/gsc_baseline.json`](evaluation/fixtures/gsc_baseline.json) for an example.

The optional `sm-workbench-capture` client calls four reviewed read-only engine tools over MCP and materializes this contract first. It does not import `seo_mcp` internals. It is an operator command, not a model-facing arbitrary tool proxy. Unknown cost and freshness remain explicit. Capture rejects results containing private workstation paths rather than stripping raw provenance. For a configuration-only status capture, use a safe relative configuration selector; provider read results can be captured independently.

## Codex and ChatGPT integration (0.6)

Install the optional MCP support:

```sh
companion/.venv/bin/python -m pip install -e 'companion[mcp]'
companion/.venv/bin/sm-workbench-mcp --db workbench.db --project docs
```

The second command starts a stdio MCP server; it waits for a host, not interactive terminal input. Configure a local host using `examples/codex.config.toml`, with the **absolute installed executable path** if the desktop app cannot find your virtual environment on PATH. CLI arguments override `SM_WORKBENCH_DB` and `SM_WORKBENCH_PROJECT`; their defaults are a per-user local data directory and project `default`. Do not commit an operator's private configuration.

The isolated plugin package is `plugins/seomonster-workbench`. It contains portable Agent Plugin manifests, a Codex compatibility manifest, and one evidence-review skill. It requires the installed `sm-workbench-mcp` executable; copying the plugin alone does not install Python dependencies. `examples/marketplace.json` is an optional local registration template, not an installed marketplace or a published plugin. No host configuration or marketplace has been changed by this implementation.

Seventeen MCP tools read evidence or record **local** projects, plans, pending ownership proposals, and experiments. Ten are reads and seven are local-record writes. The new change-ledger tool can inspect an already imported deployment observation but cannot import one; operator observation import remains CLI-only. Rule replay preview receives only a stored snapshot identifier and cannot apply or migrate findings. No tool can approve a plan, declare final ownership, execute an external action, fetch a URL, select a filesystem path, select another project, or carry a backup payload. Backup and restore remain operator-CLI-only so exact JSON bytes and provenance hashes never depend on model-mediated argument transcription. Fetched content remains untrusted data when returned to the host; the skill instructs the model not to follow its embedded instructions.

Optional localhost-only Streamable HTTP uses the same tools. Set `SM_WORKBENCH_HTTP_TOKEN` to a random secret of at least 32 characters through your local secret manager, then run:

```sh
sm-workbench-mcp --db workbench.db --project docs --transport streamable-http --port 8766
```

Clients must send a matching Bearer token to `http://127.0.0.1:8766/mcp`. Browser Origin requests are denied. This is a single-owner development transport, **not a production or multi-user ChatGPT endpoint**. OAuth, public HTTPS/tunnel registration, desktop plugin installation and hosted ChatGPT tests remain separate gates requiring approval.

Codex CLI 0.147.0 has completed an actual synthetic local MCP workflow from the independently built 0.6.0 wheel, including successful structured results from all fourteen required tools and a persisted no-execution check. Automated tests cover stdio/HTTP parity, auth, project scope, safe errors and forbidden writes. See the host plan for the narrower established claims and pending gates.

To explicitly opt into an authenticated Codex smoke test (model inference uses your account; SEO inputs stay synthetic):

```sh
companion/.venv/bin/python companion/scripts/codex_smoke.py --run --fixtures companion/evaluation/fixtures
```

The capture CLI accepts a JSON arguments file and creates a new output file without overwriting one:

```sh
SEO_MCP_CONFIG=companion/evaluation/empty.toml sm-workbench-capture \
  --tool system_status --arguments arguments.json --output status-capture.json
```

Run this example from the repository root with `{}` in `arguments.json`; it uses the empty fixture configuration and performs no probes. The capture command forwards `SEO_MCP_CONFIG`, not the entire host credential environment. Real `system_status` results can include private configuration paths and are then refused by the portable privacy guard; they are not silently rewritten. Use local engine diagnostics for private status inspection. GSC and GA4 read captures require an operator-approved live provider setup and can consume provider quota; they were not used in this session.

## Documentation map

- [`docs/AUDIT.md`](docs/AUDIT.md): current SEOmonster and OpenSEO audit
- [`docs/COMPETITIVE_MATRIX.md`](docs/COMPETITIVE_MATRIX.md): evidence-backed capability matrix
- [`docs/PRODUCT_BRIEF.md`](docs/PRODUCT_BRIEF.md): primary user, job, wedge, non-goals, and success measures
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): component diagram and domain flow
- [`docs/COMPATIBILITY.md`](docs/COMPATIBILITY.md): independent release and migration strategy
- [`docs/OPENAI_HOST_COMPATIBILITY.md`](docs/OPENAI_HOST_COMPATIBILITY.md): ChatGPT/Codex transport, packaging, testing, and release gates
- [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md): local and future multi-user risks
- [`docs/ROADMAP.md`](docs/ROADMAP.md): build, defer, and reject decisions
- [`docs/PORTABLE_FORMAT.md`](docs/PORTABLE_FORMAT.md): import/export semantics
- [`docs/IMPLEMENTATION_REPORT.md`](docs/IMPLEMENTATION_REPORT.md): shipped slice, commands, results, and limitations
- [`docs/adr`](docs/adr): architecture decisions

## License and reuse

The companion is MIT licensed. OpenSEO was studied as a primary competitive source and is also MIT licensed as of the access date in the audit. No OpenSEO code, interface, visual design, fixture, schema, product language, or asset was copied. If deliberate code reuse is considered later, retain the upstream copyright and MIT permission notice and record the exact source commit and files before integration.
