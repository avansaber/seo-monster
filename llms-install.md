# SEOMonster install for AI coding agents (Cline, Cursor, Codex, Claude Desktop)

This file is the install short-form for AI coding agents that follow the
[`llms-install.md` convention](https://github.com/cline/mcp-marketplace).
For human-readable install + auth instructions, see [README.md](README.md).

## What this MCP server gives you

70 tools across **Google Search Console, Google Analytics 4, PageSpeed
Insights (PSI), Cloudflare, IndexNow, Chrome UX Report (CrUX), content-
opportunity intelligence, plus stdlib HTTP tools for technical-SEO checks**
(`inspect_meta`, `check_canonical`, `redirect_chain_audit`,
`mixed_content_check`, `robots_txt_validate`, `sitemap_validate`,
`sitemap_health`, `inspect_schema`, `validate_schema`,
`hreflang_consistency_check`, `internal_link_graph`, `lighthouse_budget`,
`robots_ai_posture`). Cloudflare includes redirect management (single + bulk,
for migrations), a zone SEO-settings audit with remediation, and managed
robots.txt / Content-Signals control (`cf_managed_robots`).

**v0.9 adds the discovery + AI/GEO surface** (all optional providers are
pluggable; the free GSC/GA4/HTTP core works without keys): AI/GEO citation
(`ai_citation_readiness`, `ai_referral_overview`, `ai_citation_track`),
net-new keyword discovery (`gsc_keyword_expand`, `serp_adjacency_expand`,
`keyword_universe`), data-wired content briefs + topic-cluster mapping
(`content_brief_data`, `topic_cluster_map`), internal-link recommendations
(`internal_link_recommend`), rank-change attribution (`rank_change_attribution`),
and on-page SERP-gap analysis (`onpage_serp_gap`). Optional vendors: DataForSEO
(SERP/keyword/competitor-gap/AIO), Open PageRank (free domain authority), and AI
answer-engine keys (Perplexity/OpenAI/Anthropic/Gemini) for citation tracking.

13 workflow prompts: `content_workflow`, `content_brief`, `content_outline`,
`content_article`, `content_performance`, `seo_setup_audit`, `weekly_review`,
`technical_seo_audit`, `pre_deploy_check`, `structured_data_audit`,
`content_audit`, `post_deploy_verify`, `migration_check`.

Every tool carries MCP standard annotations (`readOnlyHint`,
`destructiveHint`, `idempotentHint`, `openWorldHint`). Reads are always
available; the routine writes (sitemap submit, indexing request, IndexNow) are
on by default; the higher-risk Cloudflare writes (cache purge, redirect
create/delete/bulk, settings update, managed robots.txt configure/disable) are
gated behind `SEO_MCP_ALLOW_DESTRUCTIVE=true`.

## Install

### Option A: `uvx` (recommended for Cline / Cursor / Codex)

Add to the host's MCP config (`cline_mcp_settings.json`, `.cursor/mcp.json`,
or `~/.codex/config.toml`):

```json
{
  "mcpServers": {
    "seomonster": {
      "command": "/absolute/path/to/uvx",
      "args": ["seo-monster"],
      "env": {
        "SEO_MCP_GOOGLE_OAUTH_CLIENT": "/Users/me/.config/seo-monster/client_secret.json",
        "SEO_MCP_GOOGLE_TOKEN": "/Users/me/.config/seo-monster/token.json"
      }
    }
  }
}
```

Find the absolute path to `uvx` with `which uvx`. GUI hosts do not read
your shell profile, so MCP configs need the full path.

### Option B: `.mcpb` bundle (Claude Desktop)

Download `seo-monster-*.mcpb` from the latest
[GitHub release](https://github.com/avansaber/seo-monster/releases) and
open it. Claude Desktop installs the extension and opens a configuration
form. See README "Claude Desktop (recommended): `.mcpb` bundle" for the
full step-by-step (enable extension, fill credentials, restart, start
new chat).

## One-time auth (Google APIs)

Run from a terminal **before** invoking the server from an AI host:

```sh
seo-monster auth
```

This opens a browser for Google OAuth consent and writes the token to
`~/.config/seo-monster/token.json` with mode `0600`. Subsequent server
invocations from the AI host use the cached token; no browser flow runs
inside the MCP subprocess (which would time out before a real user
finishes the consent).

PSI / Cloudflare / IndexNow keys are optional. Set the corresponding
env vars only for the services you intend to use:

| Env var | Service | Required? |
|---|---|---|
| `SEO_MCP_GOOGLE_OAUTH_CLIENT` | GSC + GA4 + Indexing | yes (for those tools) |
| `SEO_MCP_GOOGLE_TOKEN` | GSC + GA4 + Indexing | yes (path; created by `seo-monster auth`) |
| `SEO_MCP_GSC_DEFAULT_SITE` | GSC | optional (default site) |
| `SEO_MCP_GA4_PROPERTY_ID` | GA4 | optional (default property) |
| `PSI_API_KEY` | PSI + CrUX | optional but strongly recommended |
| `CF_API_TOKEN` | Cloudflare | required for CF tools only |
| `CF_ZONE` | Cloudflare | optional (default zone) |
| `SEO_MCP_INDEXNOW_KEY` | IndexNow | required for IndexNow tools only |
| `SEO_MCP_INDEXNOW_KEY_LOCATION` | IndexNow | optional |
| `SEO_MCP_ALLOW_DESTRUCTIVE` | Cloudflare writes (cache purge, redirects, settings, managed robots.txt) | required to unlock; default off |
| `DATAFORSEO_LOGIN` + `DATAFORSEO_PASSWORD` | DataForSEO (SERP/PAA, keyword volume+difficulty+intent, competitor gap, AIO) | optional (v0.9) |
| `OPENPAGERANK_API_KEY` | Open PageRank (free competitor domain authority) | optional (v0.9) |
| `PERPLEXITY_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | AI answer-engines for `ai_citation_track` | optional (v0.9; any subset) |
| `GOOGLE_ADS_DEVELOPER_TOKEN` + `GOOGLE_ADS_CUSTOMER_ID` | Google Ads volume (alt to DataForSEO; needs adwords-scope consent) | optional (v0.9) |

## Discovery

After install, the AI host should call `system_status` to discover what's
configured and what's reachable. With `probe: true` it makes one cheap
live call per configured service to confirm credentials work.

## License

MIT. See [LICENSE](LICENSE).
