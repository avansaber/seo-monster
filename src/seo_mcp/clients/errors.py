"""Normalized client errors and upstream-exception mappers.

Clients catch raw upstream failures (googleapiclient ``HttpError``, urllib
``HTTPError``, transport errors) at the network boundary and raise a single
``ApiError`` carrying one of the closed ``ErrorCode`` values. Tools then turn an
``ApiError`` into the standard envelope via ``to_envelope``. This keeps the
mapping logic in one tested place and keeps tools free of upstream library types.
"""

from __future__ import annotations

import re
from typing import Any

from ..errors import DOCS_BASE, ErrorCode, err


# Error codes whose remediation points at the auth docs section.
_AUTH_CODES = {
    ErrorCode.AUTH_MISSING,
    ErrorCode.AUTH_INVALID,
    ErrorCode.SCOPE_INSUFFICIENT,
}


class ApiError(Exception):
    """An upstream failure normalized to one ErrorCode plus remediation."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        remediation: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation
        self.details = details

    def to_envelope(self, service: str) -> dict[str, Any]:
        anchor = "auth" if self.code in _AUTH_CODES else "errors"
        return err(
            self.code,
            service,
            self.message,
            remediation=self.remediation,
            docs_url=DOCS_BASE + anchor,
            details=self.details,
        )


def _status_of(exc: Exception) -> int | None:
    """Best-effort HTTP status extraction across googleapiclient HttpError
    (``exc.resp.status``), urllib HTTPError (``exc.code``), and test fakes
    (``exc.status_code``)."""
    resp = getattr(exc, "resp", None)
    candidates = (
        getattr(resp, "status", None),
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
    )
    for value in candidates:
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


_ACTIVATION_RE = re.compile(r"https://console\.(?:developers|cloud)\.google\.com/[^\s'\"]+")


def map_google_exception(exc: Exception) -> ApiError:
    """Map a Google API client exception to a normalized ApiError.

    Checks the textual markers the reference gsc.py relies on (scope-insufficient
    and service-disabled) before falling back to HTTP status mapping.
    """
    text = str(exc)
    status = _status_of(exc)

    if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in text or "insufficient authentication scopes" in text:
        return ApiError(
            ErrorCode.SCOPE_INSUFFICIENT,
            "The Google token is missing a scope this tool needs.",
            remediation=(
                "Re-consent with the broader scope set (the indexing scope is "
                "required for indexing requests, the writable webmasters scope "
                "for sitemap submit). See README > Auth."
            ),
            details={"raw": text[:500]},
        )

    if "SERVICE_DISABLED" in text or "has not been used in project" in text:
        match = _ACTIVATION_RE.search(text)
        return ApiError(
            ErrorCode.SERVICE_DISABLED,
            "A required Google Cloud API is not enabled for this project.",
            remediation=(
                "Enable the API in the Cloud Console (see activation_url), wait "
                "a few minutes for it to propagate, then retry."
            ),
            details={"activation_url": match.group(0) if match else None, "raw": text[:500]},
        )

    # GSC URL Inspection returns 403 with these markers when the inspected
    # URL falls outside the property's verified scope. That is a scope
    # mismatch, NOT bad credentials: routing it to AUTH_INVALID sent users on
    # a wild-goose chase debugging their OAuth setup (see round-2 feedback
    # 7a.i). Surface it as NOT_FOUND with a remediation that points at the
    # property-scope concept.
    lowered = text.lower()
    if status == 403 and any(
        m in lowered
        for m in (
            "not under the verified",
            "not under the property",
            "url is not under",
            "outside the property",
            "forbidden for this site",
        )
    ):
        return ApiError(
            ErrorCode.NOT_FOUND,
            "The URL is outside the configured property's scope.",
            remediation=(
                "Pass a site_url whose prefix matches this URL, or use a broader "
                "'sc-domain:' property that covers it. The credentials are fine; "
                "the property just does not include this URL."
            ),
            details={"status": 403, "raw": text[:500]},
        )

    if status in (401, 403):
        return ApiError(
            ErrorCode.AUTH_INVALID,
            "Google rejected the credentials or denied access to this resource.",
            remediation=(
                "Confirm the authenticated account (or service-account email) has "
                "access to the property, and that the credentials are valid."
            ),
            details={"status": status, "raw": text[:500]},
        )
    if status == 400:
        return ApiError(
            ErrorCode.INVALID_INPUT,
            "Google rejected the request as invalid (HTTP 400).",
            remediation="Check dimension/metric names, date formats, and filters.",
            details={"status": 400, "raw": text[:500]},
        )
    if status == 404:
        return ApiError(
            ErrorCode.NOT_FOUND,
            "The requested resource was not found or is not visible to these credentials.",
            details={"status": 404, "raw": text[:300]},
        )
    if status == 429:
        return ApiError(
            ErrorCode.RATE_LIMITED,
            "Google API rate limit hit. Retry after a short delay.",
            details={"status": 429, "raw": text[:300]},
        )

    return ApiError(
        ErrorCode.UPSTREAM_ERROR,
        f"Unexpected Google API error: {text[:300]}",
        details={"status": status},
    )


def map_http_status(status: int, body: str, *, service: str) -> ApiError:
    """Map a plain HTTP status (PSI / Cloudflare via urllib) to an ApiError."""
    lowered = body.lower()
    if status in (401, 403):
        return ApiError(
            ErrorCode.AUTH_INVALID,
            f"{service} rejected the credentials (HTTP {status}).",
            remediation="Check the API key / token is correct and authorized.",
            details={"status": status, "body": body[:500]},
        )
    if status == 400 and ("api key not valid" in lowered or "api_key_invalid" in lowered):
        return ApiError(
            ErrorCode.AUTH_INVALID,
            f"{service} reports the API key is not valid.",
            remediation="Generate a valid API key and set it in the configuration.",
            details={"status": 400, "body": body[:500]},
        )
    if status == 400:
        return ApiError(
            ErrorCode.INVALID_INPUT,
            f"{service} rejected the request as invalid (HTTP 400).",
            details={"status": 400, "body": body[:500]},
        )
    if status == 404:
        return ApiError(
            ErrorCode.NOT_FOUND,
            f"{service} resource not found (HTTP 404).",
            details={"status": 404, "body": body[:300]},
        )
    if status == 429:
        # Service-specific remediation. PSI's anonymous quota is shared across
        # all callers without a key, and in practice often instant-429: telling
        # the user "retry later" without mentioning the key is misleading.
        if "pagespeed" in service.lower():
            remediation = (
                "Set PSI_API_KEY for per-project quota (free; create one in "
                "Google Cloud Console after enabling the PageSpeed Insights API). "
                "Anonymous PSI uses a shared quota that is often exhausted."
            )
        else:
            remediation = "Retry after a short delay."
        return ApiError(
            ErrorCode.RATE_LIMITED,
            f"{service} rate limit hit (HTTP 429).",
            remediation=remediation,
            details={"status": 429, "body": body[:300]},
        )
    return ApiError(
        ErrorCode.UPSTREAM_ERROR,
        f"{service} returned HTTP {status}.",
        details={"status": status, "body": body[:500]},
    )
