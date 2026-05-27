"""Build Google credentials from the resolved config.

OAuth installed-app is the primary path: load the cached token, refresh it
silently when expired, or run the one-time browser consent flow when the token
is missing or its scopes are too narrow (mirrors the reference gsc.py logic).
Service account is the headless alternative.

Google libraries are imported lazily inside the function so that importing this
module (and anything that depends on it) stays cheap and does not require the
Google SDK to be installed for the pure-logic unit tests.
"""

from __future__ import annotations

from typing import Any

from ..auth import MissingGoogleAuth
from ..config import Config


def build_google_credentials(config: Config, scopes: list[str]) -> Any:
    """Return a Google ``Credentials`` object for the configured auth method.

    Raises:
        MissingGoogleAuth: when neither OAuth client nor a service-account key
            is configured.
        FileNotFoundError: when a configured path does not exist.
    """
    if config.google.oauth_client:
        return _oauth_credentials(config, scopes)
    if config.google.credentials:
        return _service_account_credentials(config, scopes)
    raise MissingGoogleAuth(
        "No Google credentials configured. Set SEO_MCP_GOOGLE_OAUTH_CLIENT "
        "(+ SEO_MCP_GOOGLE_TOKEN) for OAuth, or SEO_MCP_GOOGLE_CREDENTIALS for a "
        "service-account key."
    )


def _service_account_credentials(config: Config, scopes: list[str]) -> Any:
    from google.oauth2 import service_account  # lazy import

    return service_account.Credentials.from_service_account_file(
        config.google.credentials, scopes=scopes
    )


def _oauth_credentials(config: Config, scopes: list[str]) -> Any:
    """OAuth installed-app flow with token caching and silent refresh.

    Follows the reference gsc.py ``_get_creds`` pattern: reuse the cached token
    when valid and scope-sufficient, refresh when expired, otherwise run the
    browser consent flow and persist the new token.
    """
    import os

    from google.auth.transport.requests import Request  # lazy imports
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = config.google.token
    if not token_path:
        raise MissingGoogleAuth(
            "OAuth client configured but SEO_MCP_GOOGLE_TOKEN (writable token "
            "path) is not set."
        )

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, scopes)

    scopes_ok = creds is not None and set(scopes).issubset(set(creds.scopes or []))
    if creds and creds.valid and scopes_ok:
        return creds

    if creds and creds.expired and creds.refresh_token and scopes_ok:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(config.google.oauth_client, scopes)
        creds = flow.run_local_server(port=0)

    with open(token_path, "w") as fh:
        fh.write(creds.to_json())
    return creds
