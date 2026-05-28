# Privacy

> Effective date: 2026-05-28. Covers SEOMonster v0.2.0.

SEOMonster is a local, open-source MCP server. **Nothing about your usage,
credentials, queries, or tool calls is collected, transmitted, or stored by
AvanSaber Inc. or by SEOMonster itself.** This document explains what the
software does, what data it touches, who else sees that data, and how to
remove everything when you are done.

## What SEOMonster does (and does not) collect

SEOMonster does **not**:

- Phone home, send analytics, or emit telemetry of any kind.
- Read your data into any service operated by AvanSaber Inc.
- Persist your queries, results, or call history beyond what your MCP host
  retains in its own logs.
- Embed any tracker, advertising tag, third-party SDK, or fingerprinting
  code.

There is no SEOMonster-operated server, no SEOMonster-issued account, no
SEOMonster-issued login. The maintainers cannot read your data because
there is no connection through which to read it.

## What SEOMonster does access on your behalf

When you invoke a tool, the SEOMonster process makes a single outbound HTTPS
request to the relevant upstream API using the credentials **you** supplied.

| Service | What is sent | Authority |
|---|---|---|
| Google APIs (Search Console, Indexing, Analytics Data, PageSpeed Insights) | The query you build, plus the OAuth access token derived from the consent you completed locally. | Your Google account; you authorize via the standard installed-app OAuth consent screen. |
| Cloudflare API v4 | The query you build, plus the Bearer API token you configured. | The Cloudflare API token you generated and pasted into `CF_API_TOKEN` (or the `.mcpb` form). |
| IndexNow endpoint (`api.indexnow.org`) | The URLs to notify and your IndexNow key. | The IndexNow key you generated and configured. |

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

To remove every trace of SEOMonster from your machine:

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

Once these steps complete, there is no SEOMonster-operated state to
delete; the maintainers have nothing to remove because they hold nothing.

## Source-of-truth

This document tracks the v0.2.0 release. Each release's CHANGELOG entry
flags any change that affects what data leaves your machine. Audit the
source on GitHub if any claim here matters to you:
[github.com/avansaber/seo-monster](https://github.com/avansaber/seo-monster).

## Contact

Privacy questions: open an issue at
[github.com/avansaber/seo-monster/issues](https://github.com/avansaber/seo-monster/issues).
Security issues (anything that looks like data could leak): please use the
private vulnerability reporting flow on the same repository rather than a
public issue.
