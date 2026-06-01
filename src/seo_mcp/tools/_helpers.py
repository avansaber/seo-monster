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


def preflight_get(clients: Mapping[str, Any], url: str) -> tuple[int | None, str, str | None] | None:
    """Pre-flight a URL with the shared branded HttpClient before a blind
    upstream write (sitemap submit, IndexNow notify). Both upstreams accept a
    submission and report success without checking that the referenced resource
    actually exists, so a 404 sitemap or an unhosted key file silently fails
    downstream (tester FEEDBACK §20 §1b/§3c/§3d).

    Returns:
      - ``None`` when no HTTP client is wired, meaning skip the check. Production
        always registers "http" (build_http_client needs no config), so it always
        pre-flights; this branch only spares minimal unit tests from injecting one.
      - ``(status, body_text, None)`` on a completed fetch (status may be 4xx/5xx).
      - ``(None, "", reason)`` on a transport failure (host unreachable / timeout).

    Uses the branded User-Agent baked into HttpClient: Cloudflare's Browser
    Integrity Check 403s the default urllib UA, which would make a CF-fronted
    sitemap or key file look unreachable (FEEDBACK §12c.ii)."""
    try:
        http = clients.get("http")
    except Exception:
        http = None
    if http is None:
        return None
    from ..clients.errors import ApiError

    try:
        resp = http.fetch(url, method="GET", follow_redirects=True, max_bytes=65536)
    except ApiError as exc:
        return None, "", exc.message
    except Exception as exc:  # pre-flight must never crash the actual write
        return None, "", str(exc)
    return resp.status, resp.body_text, None


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
