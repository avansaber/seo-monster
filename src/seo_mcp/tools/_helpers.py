"""Helpers shared by tool handlers: client acquisition, input coercion.

Handler contract: ``handle(arguments, config, clients) -> envelope``. ``clients``
is a mapping-like object (a dict in tests, a lazy provider in production) whose
``get(key)`` returns the client or None. For Google-backed clients, building may
raise ``MissingGoogleAuth``; ``require_client`` converts both None and that
exception into an AUTH_MISSING envelope.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..auth import MissingGoogleAuth
from ..config import Config
from ..errors import DOCS_BASE, ErrorCode, err


def require_client(
    clients: Mapping[str, Any],
    key: str,
    service: str,
    *,
    remediation: str,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Return ``(client, None)`` when available, else ``(None, error_envelope)``."""
    message: str | None = None
    try:
        client = clients.get(key)
    except MissingGoogleAuth as exc:
        client = None
        message = str(exc)
    if client is None:
        return None, err(
            ErrorCode.AUTH_MISSING,
            service,
            message or f"No credentials configured for {service}.",
            remediation=remediation,
            docs_url=DOCS_BASE + "auth",
        )
    return client, None


def resolve_site(arguments: Mapping[str, Any], config: Config) -> str | None:
    """Pick the GSC property: explicit argument, else the configured default."""
    site = arguments.get("site_url")
    if site:
        return str(site)
    return config.gsc_default_site


def missing_site_error() -> dict[str, Any]:
    return err(
        ErrorCode.INVALID_INPUT,
        "gsc",
        "No GSC property specified.",
        remediation=(
            "Pass site_url (e.g. 'sc-domain:example.com' or "
            "'https://www.example.com/'), or set SEO_MCP_GSC_DEFAULT_SITE."
        ),
        docs_url=DOCS_BASE + "configuration",
    )
