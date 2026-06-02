"""IndexNow tools (2). Complements ``gsc_request_indexing`` for the
non-Google engines (Bing, Yandex, Naver, Seznam, Yep). Available by default;
not gated behind SEO_MCP_ALLOW_DESTRUCTIVE because the operation is additive
(a notification, not a destructive change).
"""

from __future__ import annotations

from typing import Any, Mapping

from ..clients.errors import ApiError
from ..clients.indexnow import _host_of
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations, preflight_get, require_client


_SERVICE = "indexnow"
_REMEDIATION = (
    "Set SEO_MCP_INDEXNOW_KEY to a key you generated (any 8-128 hex string "
    "works) and host the key file at https://<your-host>/<key>.txt with the "
    "key as the file body so the engines can verify ownership. See "
    "https://www.indexnow.org/documentation."
)
_MAX_BULK = 10000


def _require(clients: Mapping[str, Any]):
    return require_client(clients, "indexnow", _SERVICE, remediation=_REMEDIATION)


def _verify_key_file(clients, config, host: str | None) -> dict[str, Any] | None:
    """Pre-flight the IndexNow key file before notifying. api.indexnow.org
    accepts a well-formed submission and returns 200/202 without verifying the
    key file; the engines verify it lazily and silently drop the URL if it's
    missing or wrong (tester FEEDBACK §20 §3c/§3d). We catch it up front.

    Returns an error envelope to abort, or None to proceed (also None when the
    key/host is unknown or no HTTP client is wired)."""
    key = getattr(config, "indexnow_key", None)
    if not key or not host:
        return None
    # Mirror IndexNowClient._key_location_for_host: validate the file the submit
    # will actually reference (the per-host root), honoring a configured
    # key_location only when it is on THIS host. Validating the configured host's
    # file for a foreign submission would pass and wave a doomed submit through
    # to a 422 (FEEDBACK §25).
    configured = getattr(config, "indexnow_key_location", None)
    loc = configured if (configured and _host_of(configured) == host) else f"https://{host}/{key}.txt"
    pf = preflight_get(clients, loc)
    if pf is None:
        return None  # no HTTP client wired (minimal unit test); skip
    status, body, reason = pf
    if status is None or not (200 <= status < 300):
        detail = f"returned HTTP {status}" if status is not None else f"was unreachable ({reason})"
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            f"The IndexNow key file at {loc} {detail}. Search engines verify "
            "ownership by fetching it, so they will reject this submission even "
            "though api.indexnow.org reports success. Submission blocked.",
            remediation=_REMEDIATION,
            docs_url=DOCS_BASE + "indexnow",
            details={"key_location": loc, "preflight_status": status},
        )
    matched = key.strip() == body.strip() or key.strip() in [ln.strip() for ln in body.splitlines()]
    if not matched:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            f"The IndexNow key file at {loc} does not contain your key "
            "(SEO_MCP_INDEXNOW_KEY). The engines will reject the submission. Blocked.",
            remediation=_REMEDIATION,
            docs_url=DOCS_BASE + "indexnow",
            details={"key_location": loc},
        )
    return None


TOOL_SUBMIT = {
    "name": "indexnow_submit",
    "description": (
        "Submit a single URL to IndexNow (Bing + Yandex + Naver + Seznam + "
        "Yep). Complements gsc_request_indexing for the non-Google engines. "
        "Requires SEO_MCP_INDEXNOW_KEY plus a key-verification file at "
        "https://<host>/<key>.txt."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Full absolute URL. Required."},
            "skip_preflight": {"type": "boolean", "description": "Bypass the key-file verification pre-flight (default false)."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=False, destructive=False),
}


def indexnow_submit(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    url = arguments.get("url")
    if not url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url is required.", docs_url=DOCS_BASE + "indexnow")
    if not arguments.get("skip_preflight"):
        verify_error = _verify_key_file(clients, config, _host_of(url))
        if verify_error:
            return verify_error
    try:
        resp = client.submit(url)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok({"url": url, "status": resp.get("status"), "accepted": True})


TOOL_BULK_SUBMIT = {
    "name": "indexnow_bulk_submit",
    "description": (
        "Submit multiple URLs to IndexNow in one batched request. All URLs "
        "must share the same host (IndexNow rejects mixed-host batches with "
        f"HTTP 422). Cap: {_MAX_BULK} URLs per call (IndexNow's documented "
        "limit)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": _MAX_BULK,
                "description": f"Absolute URLs sharing one host (max {_MAX_BULK}).",
            },
            "skip_preflight": {"type": "boolean", "description": "Bypass the key-file verification pre-flight (default false)."},
        },
        "required": ["urls"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=False, destructive=False),
}


def indexnow_bulk_submit(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    urls = arguments.get("urls") or []
    if not urls:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "urls must be a non-empty list.", docs_url=DOCS_BASE + "indexnow")
    if len(urls) > _MAX_BULK:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            f"Too many URLs ({len(urls)}); max is {_MAX_BULK} per call.",
            docs_url=DOCS_BASE + "indexnow",
        )
    if not arguments.get("skip_preflight"):
        verify_error = _verify_key_file(clients, config, _host_of(urls[0]))
        if verify_error:
            return verify_error
    try:
        resp = client.bulk_submit(urls)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok({"submitted_count": len(urls), "status": resp.get("status"), "accepted": True})


TOOLS = [TOOL_SUBMIT, TOOL_BULK_SUBMIT]
HANDLERS = {"indexnow_submit": indexnow_submit, "indexnow_bulk_submit": indexnow_bulk_submit}
