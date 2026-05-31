"""Build Google credentials from the resolved config.

OAuth installed-app is the primary path: load the cached token and refresh it
silently when expired. The interactive browser consent flow lives in
``run_oauth_consent`` and is **only** invoked from the ``seo-monster auth`` CLI
subcommand, never from the MCP server. Triggering a browser flow from inside
the MCP subprocess produces timeouts in Claude Desktop and other GUI hosts
(see DESIGN.md > Auth and the v0.1.1 release notes for context).

Service account is the headless alternative.

Google libraries are imported lazily inside each function so that importing
this module stays cheap and does not require the Google SDK to be installed
for the pure-logic unit tests.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..auth import MissingGoogleAuth
from ..config import Config


# Chmods applied when the OAuth consent flow writes the cached token. The token
# is refresh-capable, so it has the value of a long-lived credential for the
# requested scopes; keep it user-readable only.
_TOKEN_DIR_MODE = 0o700
_TOKEN_FILE_MODE = 0o600


def build_google_credentials(config: Config, scopes: list[str]) -> Any:
    """Return a Google ``Credentials`` object for the configured auth method.

    Non-interactive: this function never opens a browser. If OAuth is
    configured but no usable cached token is on disk, ``MissingGoogleAuth`` is
    raised with remediation pointing at ``seo-monster auth``.

    Raises:
        MissingGoogleAuth: when neither OAuth client nor a service-account key
            is configured, or when an OAuth token has not been minted yet.
        FileNotFoundError: when a configured path does not exist.
    """
    if config.google.oauth_client:
        return _oauth_credentials_silent(config, scopes)
    if config.google.credentials:
        return _service_account_credentials(config, scopes)
    raise MissingGoogleAuth(
        "No Google credentials configured. Set SEO_MCP_GOOGLE_OAUTH_CLIENT "
        "(+ SEO_MCP_GOOGLE_TOKEN) for OAuth, or SEO_MCP_GOOGLE_CREDENTIALS for "
        "a service-account key."
    )


def _service_account_credentials(config: Config, scopes: list[str]) -> Any:
    from google.oauth2 import service_account  # lazy import

    return service_account.Credentials.from_service_account_file(
        config.google.credentials, scopes=scopes
    )


def _oauth_credentials_silent(config: Config, scopes: list[str]) -> Any:
    """Load + refresh the cached OAuth token. Never opens a browser.

    If the token file does not exist, or it exists but its scopes don't cover
    what we need, raise MissingGoogleAuth with remediation pointing at the
    ``seo-monster auth`` CLI command. The interactive consent flow lives in
    ``run_oauth_consent`` and must be run from a terminal (not from inside an
    MCP host subprocess).
    """
    from google.auth.transport.requests import Request  # lazy imports
    from google.oauth2.credentials import Credentials

    token_path = config.google.token
    if not token_path:
        raise MissingGoogleAuth(
            "OAuth client configured but SEO_MCP_GOOGLE_TOKEN (a writable "
            "token-cache path) is not set."
        )

    if not os.path.exists(token_path):
        raise MissingGoogleAuth(
            f"OAuth token cache not found at {token_path}. Run `seo-monster "
            f"auth` from a terminal once to complete the one-time browser "
            f"consent; that command writes the token and exits. Then retry "
            f"the call from your MCP host."
        )

    creds = Credentials.from_authorized_user_file(token_path, scopes)
    scopes_ok = set(scopes).issubset(set(creds.scopes or []))
    if not scopes_ok:
        raise MissingGoogleAuth(
            "Cached OAuth token does not cover the scopes this tool needs. "
            "Re-run `seo-monster auth` from a terminal to re-consent with the "
            "broader scope set."
        )
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist the refreshed token so the next process can reuse it. Keep
        # the on-disk perms locked down.
        _write_token(token_path, creds)
        return creds
    raise MissingGoogleAuth(
        "Cached OAuth token is unusable (expired without a refresh token). "
        "Re-run `seo-monster auth` from a terminal to mint a new one."
    )


# --- interactive consent (CLI-only) ---------------------------------------


def run_oauth_consent(config: Config, scopes: list[str]) -> Path:
    """Run the interactive InstalledAppFlow and write the cached token.

    Only invoked from the ``seo-monster auth`` CLI subcommand. Must run in a
    terminal (a browser opens for one-time consent). Writes the token with
    0600 perms; the parent directory gets 0700.

    Returns the resolved token path.

    Raises:
        MissingGoogleAuth: when SEO_MCP_GOOGLE_OAUTH_CLIENT or
            SEO_MCP_GOOGLE_TOKEN is not configured.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow  # lazy import

    if not config.google.oauth_client:
        raise MissingGoogleAuth(
            "OAuth consent requires SEO_MCP_GOOGLE_OAUTH_CLIENT (the path to "
            "your Desktop-app client-secrets JSON)."
        )
    if not config.google.token:
        raise MissingGoogleAuth(
            "OAuth consent requires SEO_MCP_GOOGLE_TOKEN (a writable path for "
            "the cached token)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(config.google.oauth_client, scopes)
    creds = flow.run_local_server(port=0)
    return _write_token(config.google.token, creds)


def _write_token(token_path: str, creds: Any) -> Path:
    """Write the OAuth token to ``token_path`` with locked-down perms.

    Best-effort chmod: silently skipped on platforms (e.g. Windows) where
    POSIX mode bits do not apply.
    """
    path = Path(token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, _TOKEN_DIR_MODE)
    except (OSError, NotImplementedError):
        pass
    path.write_text(creds.to_json())
    try:
        os.chmod(path, _TOKEN_FILE_MODE)
    except (OSError, NotImplementedError):
        pass
    return path


def required_token_mode() -> int:
    """Expose the expected file mode for tests."""
    return _TOKEN_FILE_MODE
