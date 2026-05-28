# Contributing to SEOMonster

Short conventions for the next person.

## Internal strategy docs do not go in the repo

Strategy proposals, listing-submission drafts, brand assets, research notes,
phased build plans, validation responses: these stay private to the
maintainer. They live under `~/.local/share/seo-monster-private/` on the
maintainer's machine, or inside a `.private/` subdirectory of a local clone.
Both locations are gitignored.

The repo enforces this in three places:

1. `.gitignore` lists the specific filenames we have used in the past
   (`PLAN.md`, `RESEARCH-AND-PROPOSAL.md`, `LISTINGS-PLAN.md`, `marketing/`,
   `.private/`). A `git add` of any of these is silently dropped.
2. `pyproject.toml` declares a `[tool.hatch.build.targets.sdist]` exclude
   block for the same patterns, so even an accidentally-tracked planning
   doc never reaches a published PyPI sdist.
3. `.github/workflows/release.yml` runs a "reject internal docs" step at the
   start of every tag-driven release. Any tag push with one of those
   patterns tracked fails the workflow before anything is built or
   uploaded.

If a future internal doc has a different filename, add it to all three
places in the same commit.

## Public docs that stay in the repo

- `README.md`: user-facing install, tool reference, configuration.
- `CHANGELOG.md`: per-release notes, each Added/Changed/Deprecated item
  tagged with the explicit validation checklist a testing pass should
  cover.
- `PRIVACY.md`: data-flow statement, deletion path, upstream policy links.
- `DESIGN.md`: architecture, tool surface, error envelope, the design
  decisions that future contributors need.
- `CONTRIBUTING.md`: this file.
- `LICENSE`: MIT.

## Tests

```sh
uv venv && uv pip install -e ".[dev]"
uv run pytest -q
```

Tests are fully offline. They mock at the client layer, so no network and no
credentials are needed.

## Release

Tag-driven. Pushing a tag of the form `v*` triggers
`.github/workflows/release.yml`, which verifies the version stamps in
`pyproject.toml`, `manifest.json`, and `src/seo_mcp/__init__.py` all match
the tag, rejects any internal planning docs in the tag tree, runs `pytest`,
builds the wheel + sdist + `.mcpb`, scans the artifacts for credential
patterns, and attaches all three to the GitHub release for the tag.

PyPI publishing is currently still a manual `uv publish` step from the
maintainer's shell. Switching to PyPI Trusted Publishing is a one-time
setup at `pypi.org/manage/project/seo-monster/settings/publishing/`; the
commented step at the bottom of the workflow becomes active once enabled.
