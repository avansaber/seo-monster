"""CLI subcommands for SEOMonster.

The default invocation (``seo-monster`` with no arguments) starts the MCP
server over stdio. The dispatch in ``server.main()`` peels off subcommands
before that. Currently one subcommand is supported:

    seo-monster auth

Run the one-time interactive Google OAuth consent flow from a terminal,
write the cached token to ``SEO_MCP_GOOGLE_TOKEN``, and exit. This must be
run *before* using SEOMonster from a GUI host like Claude Desktop, because
those hosts launch the server as an MCP subprocess that has no way to wait
for a real human to complete browser consent.
"""

from __future__ import annotations

import sys
from typing import Sequence

from .auth import MissingGoogleAuth, required_scopes
from .clients.google_auth import run_oauth_consent
from .config import load_config


def auth_main(argv: Sequence[str] | None = None) -> int:
    """Run the OAuth consent flow. Returns a Unix exit code.

    Usage:
        seo-monster auth

    The flow reuses the same scope set the server requests at runtime, so a
    single consent unlocks every Google-backed tool.
    """
    # argv kept for future flags (e.g. --read-only). Currently unused.
    del argv

    config = load_config()
    method = _which_google_auth(config)
    if method == "service_account":
        print(
            "Service-account auth is configured (SEO_MCP_GOOGLE_CREDENTIALS). "
            "No interactive consent step is needed; the key file IS the credential."
        )
        return 0
    if method is None:
        print(
            "ERROR: no Google auth configured. Set SEO_MCP_GOOGLE_OAUTH_CLIENT "
            "and SEO_MCP_GOOGLE_TOKEN, then re-run `seo-monster auth`.",
            file=sys.stderr,
        )
        return 2

    scopes = required_scopes(config)
    print(
        "Opening a browser for one-time Google OAuth consent. Approve the "
        "requested scopes, then return here."
    )
    try:
        path = run_oauth_consent(config, scopes)
    except MissingGoogleAuth as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Token cached at {path} (0600). MCP server is now ready to use.")
    return 0


def _which_google_auth(config) -> str | None:
    """Mirror of auth.google_auth_method but kept locally so we don't depend
    on import order at CLI startup. OAuth wins when both are configured."""
    if config.google.oauth_client:
        return "oauth"
    if config.google.credentials:
        return "service_account"
    return None
