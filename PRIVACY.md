# Privacy

> Updated: 2026-10-03. Covers the SEOMonster 0.9.3 engine and the local unreleased Part 1 changes. Optional Workbench persistence is described separately below.

SEOMonster is a local, open-source MCP server. **AvanSaber Inc. does not collect
or store your usage, credentials, queries, or tool calls.** The engine stores
Google OAuth credentials locally when you authorise Google access. The optional
Workbench stores the records you choose in a local database. Provider requests
and your MCP host's logs are separate from maintainer collection.

Local storage is under your control: the Google OAuth token cache supports
your authorised provider requests, and the optional Workbench keeps the records
you ask it to retain. Neither sends those local files to AvanSaber. The cache,
database, exports and host logs are listed below so you can manage their
retention separately from provider requests and maintainer collection.

## What SEOMonster does (and does not) collect

SEOMonster does **not**:

- Phone home, send analytics, or emit telemetry of any kind.
- Read your data into any service operated by AvanSaber Inc.
- Persist your queries, results, or call history in the engine. The optional
  Workbench can retain records you choose in its local database.
- Embed any tracker, advertising tag, third-party SDK, or fingerprinting
  code.

There is no SEOMonster-operated server, no SEOMonster-issued account, no
SEOMonster-issued login. The maintainers cannot read your data because
there is no connection through which to read it.

## What SEOMonster does access on your behalf

When you invoke a tool, the SEOMonster process contacts the relevant upstream
APIs using the credentials **you** supplied. Composite tools may resolve a
property, page or hostname through several requests. These are provider calls,
not telemetry to the maintainers.

| Service | What is sent | Authority |
|---|---|---|
| Google APIs (Search Console, Indexing, Analytics Data and Admin, Google Ads) | Property, report, keyword or configuration arguments and the configured OAuth credentials. | Your Google account and enabled API access; Google Ads also uses the developer token you supply. |
| PageSpeed Insights and Chrome UX Report (CrUX) | Requested page URL or origin, form-factor/report arguments and the optional API key. | Your configured Google API key when required; anonymous PSI requests remain available. |
| Cloudflare API v4 | The query you build, plus the Bearer API token you configured. | The Cloudflare API token you generated and pasted into `CF_API_TOKEN` (or the `.mcpb` form). |
| IndexNow endpoint (`api.indexnow.org`) | The URLs to notify and your IndexNow key. | The IndexNow key you generated and configured. |
| DataForSEO (`api.dataforseo.com`) | Keyword/SERP arguments, location/language options and configured credentials. | Your optional provider account; calls may incur provider charges. |
| Open PageRank (`openpagerank.com`) | Requested domains and the configured API key. | Your optional provider account and key. |
| Perplexity, OpenAI, Anthropic and Gemini | Requested prompts, model/options and configured API credentials; answer and citation data return to your local tool. | Your optional engine accounts; usage may incur provider charges. |
| Requested websites and discovered crawl/SERP URLs | HTTP page, robots, sitemap, redirect and IndexNow verification requests; the destination can see the request and user agent. | URLs selected by you or followed by the requested workflow, subject to the HTTP client's checks. |

The AI provider destinations are `api.perplexity.ai`, `api.openai.com`,
`api.anthropic.com` and `generativelanguage.googleapis.com`. Provider payloads
contain the arguments required by the selected tool, not a copy of the full
conversation. Your MCP host controls what conversation context it sends to a
tool. Do not include private information in keyword or prompt arguments unless
you intend that provider to receive it.

## Optional Workbench persistence

The separately installed Workbench companion stores approved context, saved
captures, evidence, plans, decisions and observational experiment records in
your local SQLite database. Its MCP server defaults to
`~/.local/share/seomonster-workbench/workbench.db`; its CLI defaults to
`.seomonster-workbench.db` in the current directory. You can choose another
path. Operator-requested exports and backups create local
files. Its local MCP tools include record writes, but it has no external
executor. That user-owned persistence is separate from the engine's no-telemetry
policy. Review local backups, exports and host logs before sharing them.

Each upstream service has its own privacy policy and may log requests for
their own purposes (rate limiting, abuse detection, audit). The upstream
services are independent of SEOMonster:

- [Google APIs (privacy policy)](https://policies.google.com/privacy)
- [Cloudflare (privacy policy)](https://www.cloudflare.com/privacypolicy/)
- [Microsoft Bing (IndexNow lead)](https://privacy.microsoft.com/en-us/privacystatement)
- [Yandex (IndexNow)](https://yandex.com/legal/confidential/)

SEOMonster does not interpose on these requests, does not record their
contents, and does not retain a copy.

## What lives on your machine

The following files may be created on your machine by SEOMonster or the host
you run it in. They never leave your machine unless you explicitly copy
them.

- **OAuth token cache:** by default `~/.config/seo-monster/token.json`,
  written with mode `0600` inside a directory created with mode `0700`.
  Contains the cached Google OAuth credentials. Deletable at any time;
  the next Google-backed tool call will prompt you to re-run
  `seo-monster auth` to mint a new one.
- **API key strings in your MCP host's settings:** `PSI_API_KEY`,
  `CF_API_TOKEN`, `SEO_MCP_INDEXNOW_KEY`. When installed as an MCPB
  bundle, secret-typed fields land in the operating system keychain
  (macOS Keychain, Windows Credential Manager). When configured via env
  vars in your MCP host's config file (e.g. `claude_desktop_config.json`,
  `~/.cursor/mcp.json`), the keys live in those files.
- **Optional TOML config:** `~/.config/seo-monster/config.toml` (or the
  `SEO_MCP_CONFIG` path you set). Holds the same values; never read by
  SEOMonster except at startup. Deletable at any time.
- **Optional IndexNow verification file:** if you use the IndexNow tools,
  you publish a file at `https://<your-host>/<key>.txt`. That file lives
  on the web server you control, not on any SEOMonster infrastructure.

## What your MCP host may log

Your MCP host (Claude Desktop, Cursor, Cline, Codex, etc.) is responsible
for the conversation transcript, the tool calls, and any logs it keeps.
SEOMonster does not influence what the host retains. Refer to your host's
privacy policy for what happens to your prompts and tool-call records.

## Data deletion

To remove SEOMonster's local files and revoke its provider access:

1. Remove the bundle from your MCP host:
   - Claude Desktop: Settings > Extensions > SEOMonster > Remove.
   - `uvx` install: simply stop using it; `uvx` caches it in
     `~/.cache/uv/` (or `%LOCALAPPDATA%\uv\` on Windows). Run
     `uv cache clean seo-monster` to drop the cached install.
2. Delete the token cache and config file:
   ```sh
   rm -rf ~/.config/seo-monster
   ```
3. Revoke the credentials you issued from each upstream service:
   - Google: visit [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
     and remove the OAuth client you authorized.
   - Cloudflare: visit
     [dash.cloudflare.com/profile/api-tokens](https://dash.cloudflare.com/profile/api-tokens)
     and delete the API token.
   - PSI: delete the key at
     [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials).
   - IndexNow: remove the verification file from your web server and
     replace the key the next time you reuse the tools.
4. Remove any keys the host may have copied into its keychain entry for
   SEOMonster (Claude Desktop removes them on extension removal; other
   hosts vary).
5. If you installed Workbench, review your chosen SQLite database and any
   exports and backups. Retain the records you need, then move only the exact
   files you choose to Trash. Removing the engine does not remove those
   user-owned records or your MCP host's transcripts.

AvanSaber Inc. holds no copy of these local records. Your MCP host and upstream
providers may retain their own records under their respective policies.

## Source-of-truth

This document tracks the engine and optional local companion scopes above. Each release's CHANGELOG entry
flags any change that affects what data leaves your machine. Audit the
source on GitHub if any claim here matters to you:
[github.com/avansaber/seo-monster](https://github.com/avansaber/seo-monster).

## Contact

Privacy questions: open an issue at
[github.com/avansaber/seo-monster/issues](https://github.com/avansaber/seo-monster/issues).
Security issues (anything that looks like data could leak): please use the
private vulnerability reporting flow on the same repository rather than a
public issue.
