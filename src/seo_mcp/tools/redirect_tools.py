"""redirect_chain_audit (1). Walks the redirect chain for a URL without
following 3xx automatically, returning every hop plus a synthesis of issues
the chain has: length > 1, mixed protocol hops (https -> http), loops,
non-2xx terminus.

This is one of the most common SEO triage requests for migrations and CMS
changes: "where does this URL actually end up, and how many hops does it
cost?". Browsers and crawlers both treat long chains as a soft signal of
neglect; Google's documentation suggests no more than five hops total.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import ANNOT_READ, require_client


_SERVICE = "technical"
_REMEDIATION = "No setup needed; the HTTP client is built in."


def _require_http(clients: Mapping[str, Any]):
    return require_client(clients, "http", _SERVICE, remediation=_REMEDIATION)


TOOL = {
    "name": "redirect_chain_audit",
    "description": (
        "Walk the redirect chain for a URL without auto-following 3xx. "
        "Returns every hop (status, location, elapsed_ms) and flags issues: "
        "chain longer than 1, mixed-protocol hops (https -> http), loops, "
        "or non-2xx terminus. Cap defaults to 10 hops."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL to audit."},
            "max_redirects": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 10,
                "description": "Maximum hops to follow before flagging the chain as too long.",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def redirect_chain_audit(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_http(clients)
    if error:
        return error
    url = arguments.get("url")
    if not url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url is required.", docs_url=DOCS_BASE + "technical")
    max_redirects = int(arguments.get("max_redirects") or 10)
    try:
        resp = client.fetch(url, max_redirects=max_redirects)
    except ApiError as exc:
        # A loop or max-hop overflow lands here. Surface it with the partial
        # chain the client recorded in details, if any.
        return exc.to_envelope(_SERVICE)
    hops = [
        {"url": h.url, "status": h.status, "location": h.location, "elapsed_ms": h.elapsed_ms}
        for h in resp.redirect_chain
    ]
    findings: list[str] = []
    if len(hops) > 1:
        findings.append("long_chain")
    if not (200 <= resp.status < 300):
        findings.append("non_2xx_terminus")
    # Mixed-protocol = any hop downgrades https to http.
    if _has_protocol_downgrade(url, hops):
        findings.append("protocol_downgrade")
    return ok({
        "url": url,
        "final_url": resp.final_url,
        "final_status": resp.status,
        "hop_count": len(hops),
        "hops": hops,
        "findings": findings,
    })


def _has_protocol_downgrade(start_url: str, hops: list[dict[str, Any]]) -> bool:
    prev_scheme = urlparse(start_url).scheme
    for hop in hops:
        target = hop.get("location") or ""
        scheme = urlparse(target).scheme or prev_scheme
        if prev_scheme == "https" and scheme == "http":
            return True
        prev_scheme = scheme
    return False


TOOLS = [TOOL]
HANDLERS = {"redirect_chain_audit": redirect_chain_audit}
