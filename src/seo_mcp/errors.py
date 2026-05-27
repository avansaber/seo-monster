"""The result envelope and the closed set of error codes.

Every tool returns the same shape so the host AI reads one stable contract:

    success: {"ok": true,  "data": {...}, "error": null}
    failure: {"ok": false, "data": null, "error": {...}}

A tool never raises to the transport. Upstream failures (missing creds, 403,
429, disabled API) are converted into an ``error`` object the AI can act on.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


# Base for the ``docs_url`` field. Anchors map to README sections.
DOCS_BASE = "https://github.com/avansaber/seo-mcp#"


class ErrorCode(StrEnum):
    """Closed set of error codes. Documented once in the README."""

    AUTH_MISSING = "AUTH_MISSING"
    AUTH_INVALID = "AUTH_INVALID"
    SCOPE_INSUFFICIENT = "SCOPE_INSUFFICIENT"
    DESTRUCTIVE_DISABLED = "DESTRUCTIVE_DISABLED"
    CONFIRM_REQUIRED = "CONFIRM_REQUIRED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_INPUT = "INVALID_INPUT"
    RATE_LIMITED = "RATE_LIMITED"
    SERVICE_DISABLED = "SERVICE_DISABLED"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"


def ok(data: Any) -> dict[str, Any]:
    """Build a success envelope."""
    return {"ok": True, "data": data, "error": None}


def err(
    code: ErrorCode | str,
    service: str,
    message: str,
    *,
    remediation: str | None = None,
    docs_url: str | None = None,
    details: Any = None,
) -> dict[str, Any]:
    """Build a failure envelope.

    Args:
        code: one of ``ErrorCode``.
        service: which data source the error relates to ("gsc", "ga4", "psi",
            "cf", or "general").
        message: human-readable summary.
        remediation: optional, what the user should do to fix it.
        docs_url: optional link, usually ``DOCS_BASE + anchor``.
        details: optional structured extra (upstream body, activation URL, ...).
    """
    return {
        "ok": False,
        "data": None,
        "error": {
            "code": str(code),
            "service": service,
            "message": message,
            "remediation": remediation,
            "docs_url": docs_url,
            "details": details,
        },
    }
