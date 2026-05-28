"""IndexNow tools (2). Complements ``gsc_request_indexing`` for the
non-Google engines (Bing, Yandex, Naver, Seznam, Yep). Available by default;
not gated behind SEO_MCP_ALLOW_DESTRUCTIVE because the operation is additive
(a notification, not a destructive change).
"""

from __future__ import annotations

from typing import Any, Mapping

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations, require_client


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
    try:
        resp = client.bulk_submit(urls)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok({"submitted_count": len(urls), "status": resp.get("status"), "accepted": True})


TOOLS = [TOOL_SUBMIT, TOOL_BULK_SUBMIT]
HANDLERS = {"indexnow_submit": indexnow_submit, "indexnow_bulk_submit": indexnow_bulk_submit}
