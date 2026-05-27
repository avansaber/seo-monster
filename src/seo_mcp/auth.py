"""Auth helpers: Google scope/method logic and the Cloudflare token resolver.

This module stays import-light (no Google libraries, no network). The heavy
credential building lives in ``clients/google_auth.py`` and is imported lazily
only when a tool actually needs to talk to Google.
"""

from __future__ import annotations

from .config import Config


# OAuth scopes requested by default. Sitemap submit and indexing request are
# available by default (un-gated), so the standard consent includes the
# writable webmasters scope and the indexing scope plus GA4 read. A read-only
# consent (webmasters.readonly + analytics.readonly) is documented in the
# README for users who want least privilege and skip the two write tools.
SCOPE_WEBMASTERS = "https://www.googleapis.com/auth/webmasters"
SCOPE_WEBMASTERS_READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
SCOPE_INDEXING = "https://www.googleapis.com/auth/indexing"
SCOPE_ANALYTICS_READONLY = "https://www.googleapis.com/auth/analytics.readonly"

DEFAULT_SCOPES = [SCOPE_WEBMASTERS, SCOPE_INDEXING, SCOPE_ANALYTICS_READONLY]


class MissingGoogleAuth(Exception):
    """Raised when a Google tool runs but no credential is configured. Callers
    convert this into an AUTH_MISSING envelope rather than letting it escape."""


def google_auth_method(config: Config) -> str | None:
    """Which Google auth path is configured.

    OAuth takes precedence (the recommended path); service account is the
    fallback. Returns "oauth", "service_account", or None when neither is set.
    """
    if config.google.oauth_client:
        return "oauth"
    if config.google.credentials:
        return "service_account"
    return None


def google_configured(config: Config) -> bool:
    """True when some Google credential is present."""
    return google_auth_method(config) is not None


def required_scopes(config: Config) -> list[str]:
    """The OAuth scopes the server requests for this configuration.

    Currently the full default set. A future read-only mode would narrow this;
    the seam is here so tools and ``system_status`` share one definition.
    """
    return list(DEFAULT_SCOPES)


def cf_token(config: Config) -> str | None:
    """Resolve the Cloudflare API token (already env-first / file-fallback in
    config). Thin accessor so client code does not reach into the config shape."""
    return config.cf_api_token
