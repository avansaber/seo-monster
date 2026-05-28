"""Helpers shared by tool handlers: client acquisition, input coercion,
and the MCP tool-annotation builder used by every TOOL dict.

Handler contract: ``handle(arguments, config, clients) -> envelope``. ``clients``
is a mapping-like object (a dict in tests, a lazy provider in production) whose
``get(key)`` returns the client or None. For Google-backed clients, building may
raise ``MissingGoogleAuth``; ``require_client`` converts both None and that
exception into an AUTH_MISSING envelope.

Tool annotations: MCP 2025-03-26+ defines four hint fields on every tool that
help hosts decide which calls to auto-approve, which to confirm, and how to
batch retry. Anthropic's Connectors Directory rejects ~30% of submissions for
missing annotations. ``annotations(read, destructive, idempotent, open_world)``
returns the canonical sub-dict every TOOL declares.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..auth import MissingGoogleAuth
from ..config import Config
from ..errors import DOCS_BASE, ErrorCode, err


def annotations(
    *,
    read: bool,
    destructive: bool = False,
    idempotent: bool = True,
    open_world: bool = True,
) -> dict[str, bool]:
    """Build the MCP tool-annotations hint dict.

    Field semantics (verbatim from the MCP spec):
        readOnlyHint:    True when the tool does not modify external state.
        destructiveHint: True when the call performs a destructive update
                         (vs additive). Only meaningful when readOnlyHint=False.
        idempotentHint:  True when calling again with the same args is safe.
        openWorldHint:   True when the tool interacts with arbitrary external
                         entities (any URL, any property), False when its
                         domain is closed (e.g. config-only).
    """
    return {
        "readOnlyHint": read,
        "destructiveHint": destructive,
        "idempotentHint": idempotent,
        "openWorldHint": open_world,
    }


# Common shorthand for the most frequent shape (read-only call against an
# external API). Used by ~80% of the existing tool registry.
ANNOT_READ = annotations(read=True)


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
