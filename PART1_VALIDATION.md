# Part 1 Engine safety validation

Local candidate prepared on 2026-10-03. This document describes unreleased engine changes, not a public release or a host-installation claim. Package version remains 0.9.3 until release preparation is approved.

## Delivered behaviour

Unicode lexical extraction retains letters, canonical combining marks and internal joiners across word counts, terms, entities and token ratios. English-only stopword filtering remains explicit. Scripts requiring language-specific segmentation carry measurement limits. AI readiness excludes unavailable word-based components rather than assigning zero or a confident score. Content briefs and SERP-gap analysis do not produce word targets from unsupported segmentation.

Cloudflare auditing reads the zone configuration phase and distinguishes zone defaults from supported hostname observations. Exact hostname equality, sets, simple OR combinations and `true` are interpreted without evaluating provider expressions. Later matching SSL settings win in provider order. Unsupported expressions and permission failures remain partial or unavailable evidence. Raw rules, their positions, capture time and calculation version remain inspectable. Origin readiness, other hostname coverage and Page Rules are not verified; no zone-wide Strict fix is supplied.

Canonical metadata and optional-provider disclosures are updated. Local coordination records are explicitly excluded from engine source archives and MCPB bundles. The separate companion remains outside engine distribution.

## Source validation

The unchanged baseline passed 690 combined tests. Nineteen new cases failed before production changes, establishing the extraction and scoped-policy gaps. Further edge, metadata and independent-review cases brought the new coverage to 36 tests. One English fixture expectation was corrected from seven to six lexical tokens after manual counting; production behaviour was not changed to satisfy that mistaken count.

The first revised combined source suite passed 721 tests. Independent review then reproduced a missing weaker-hostname finding under a non-Strict default and loss of scope-specific permission remediation. Both new regression cases failed before their fixes. The revised audit now surfaces the hostname independently of the zone default and preserves remediation. Following the owner's direct instruction in the review session, Web-growth corrected Cloudflare HTTP 403 with provider code 9109 to `SCOPE_INSUFFICIENT` and added further regression coverage. Generic HTTP 403 handling remains unchanged. This is a deliberate semantic correction to that specific safe error, not a claim of byte-identical error behaviour. The final combined source suite passed 726 tests; Ruff and whitespace checks passed. Existing MCP handshake, the 70-tool and 13-prompt registry hashes, input schemas, annotations and result envelopes remain protected. No compatibility fixture was regenerated. No external system was modified.

The privacy review also identified ambiguous no-storage wording. Under the owner's direct instruction to fix the review issues, Web-growth clarified the distinction between maintainer collection and user-owned local storage. The document describes local cache and Workbench retention explicitly and includes user-owned database/export cleanup. Website and business claims outside this authorised privacy clarification remain unchanged.

Installed-artifact acceptance and final source/artifact security reviews are separate gates. Their exact results belong to the release candidate record; this source result alone does not establish public delivery.

## Provider reference

Primary documentation checked on 2026-10-03: [Configuration Rules](https://developers.cloudflare.com/rules/configuration-rules/), [available SSL settings](https://developers.cloudflare.com/rules/configuration-rules/settings/), and [configuration phase API](https://developers.cloudflare.com/rules/configuration-rules/create-api/). Cloudflare documents the `http_config_settings` phase, the `set_config` action and later-rule precedence. The supported parser is deliberately narrower than the full provider expression language.

## Tracker review

| Item | Local disposition |
|---|---|
| Issue 12 | Extraction and downstream regressions implemented; tracker response waits for public delivery approval |
| Issue 11 | Optional provider evaluation remains part 4; no default provider precedence change |
| Issue 1 | Marketplace acknowledgement is prepared as an owner action, with no production change |
| PR 8 | Keep MCP `<2`; the SDK major migration is separate from widening the dependency bound |
| PR 7 | Keep the tested Ruff pin; Ruff 0.16.0 found 166 lint findings on this candidate tree |
| PRs 3 through 6 | Proposed action tags exist upstream; individual historical core jobs passed, informational canaries failed. No merge or workflow upgrade is performed here; fresh candidate workflow acceptance is still required |

GitHub source references: [open issues](https://github.com/avansaber/seo-monster/issues), [MCP proposal](https://github.com/avansaber/seo-monster/pull/8), [Ruff proposal](https://github.com/avansaber/seo-monster/pull/7), and the action proposals [3](https://github.com/avansaber/seo-monster/pull/3), [4](https://github.com/avansaber/seo-monster/pull/4), [5](https://github.com/avansaber/seo-monster/pull/5), [6](https://github.com/avansaber/seo-monster/pull/6). Checked read-only on 2026-10-03. Historical PR checks do not prove the current candidate is merge-ready.

## Remaining public checkpoint

Build and inspect an engine-only candidate without unrelated companion CI hunks or an unavailable companion link. Validate the clean source and exact installed engine wheel, then run two final security scans. Obtain approval before any commit or public push. Confirm public CI and installation afterwards. Registry publication, website metadata deployment and directory submissions each need approval for those acts.

Hosted ChatGPT, local plugin installation in additional hosts and public Workbench packaging remain part 2. Approved business claims, engine configuration precedence and write controls are unchanged.
