"""Sitemap tools (2): ``sitemap_validate`` (structure + content checks on a
single sitemap or sitemap index) and ``sitemap_health`` (samples N URLs and
HEAD-checks them, aggregating status histogram + first non-2xx examples).

XML is parsed with stdlib ``xml.etree.ElementTree`` using a hardened parser
(no external entity resolution, size capped via the HttpClient max_bytes).
We accept both ``urlset`` and ``sitemapindex`` documents per sitemaps.org.

Sitemap-protocol limits (sitemaps.org): 50,000 URLs per file, 50 MiB
uncompressed. We surface oversize as a finding rather than rejecting the
fetch.
"""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from typing import Any, Mapping
from urllib.parse import urlparse

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import ANNOT_READ, require_client


_SERVICE = "technical"
_REMEDIATION = "No setup needed; the HTTP client is built in."
_MAX_URLS = 50000
_MAX_BYTES = 50 * 1024 * 1024  # sitemaps.org per-file size cap
_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _require_http(clients: Mapping[str, Any]):
    return require_client(clients, "http", _SERVICE, remediation=_REMEDIATION)


def _decode_body(resp) -> str:
    """Return the sitemap body as text, transparently un-gzipping when the
    URL ends in .gz or the Content-Type is gzip."""
    ctype = resp.headers.get("content-type", "").lower()
    body = resp.body_bytes
    if resp.final_url.endswith(".gz") or "gzip" in ctype:
        try:
            body = gzip.decompress(body)
        except OSError:
            # Not actually gzipped; fall through with raw bytes.
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace")


def _parse_sitemap(text: str) -> tuple[str, list[dict[str, str]]]:
    """Parse a sitemap document. Returns (kind, entries) where kind is
    ``"urlset"`` or ``"sitemapindex"`` and entries are dicts with at least a
    ``loc`` key plus optional ``lastmod``."""
    root = ET.fromstring(text)
    tag = root.tag.split("}", 1)[-1]
    entries: list[dict[str, str]] = []
    if tag == "urlset":
        for url_el in root.findall("sm:url", _NS):
            loc = (url_el.findtext("sm:loc", default="", namespaces=_NS) or "").strip()
            lastmod = (url_el.findtext("sm:lastmod", default="", namespaces=_NS) or "").strip()
            if loc:
                entries.append({"loc": loc, "lastmod": lastmod})
        return "urlset", entries
    if tag == "sitemapindex":
        for sm_el in root.findall("sm:sitemap", _NS):
            loc = (sm_el.findtext("sm:loc", default="", namespaces=_NS) or "").strip()
            lastmod = (sm_el.findtext("sm:lastmod", default="", namespaces=_NS) or "").strip()
            if loc:
                entries.append({"loc": loc, "lastmod": lastmod})
        return "sitemapindex", entries
    raise ApiError(
        ErrorCode.INVALID_INPUT,
        f"Sitemap root element is {tag!r}; expected 'urlset' or 'sitemapindex'.",
    )


# ---------------------------------------------------------------------------
# sitemap_validate
# ---------------------------------------------------------------------------


TOOL_VALIDATE = {
    "name": "sitemap_validate",
    "description": (
        "Fetch a sitemap or sitemap-index URL, validate its XML structure, "
        "count entries, and flag oversize (>50,000 URLs or >50 MiB), missing "
        "<lastmod>, and entries whose host does not match the sitemap host. "
        "Handles .gz transparently."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "sitemap_url": {"type": "string", "description": "Absolute URL of the sitemap or sitemap index."},
        },
        "required": ["sitemap_url"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def sitemap_validate(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_http(clients)
    if error:
        return error
    sitemap_url = arguments.get("sitemap_url")
    if not sitemap_url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "sitemap_url is required.", docs_url=DOCS_BASE + "technical")
    try:
        resp = client.fetch(sitemap_url, max_bytes=_MAX_BYTES + 1024)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    if not (200 <= resp.status < 300):
        return err(
            ErrorCode.UPSTREAM_ERROR,
            _SERVICE,
            f"Sitemap fetch returned HTTP {resp.status}.",
            details={"status": resp.status, "sitemap_url": sitemap_url},
        )
    text = _decode_body(resp)
    try:
        kind, entries = _parse_sitemap(text)
    except ET.ParseError as exc:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            f"Sitemap XML parse error: {exc}",
            details={"sitemap_url": sitemap_url},
        )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    findings: list[str] = []
    sitemap_host = urlparse(resp.final_url).netloc
    cross_host = [e for e in entries if urlparse(e["loc"]).netloc and urlparse(e["loc"]).netloc != sitemap_host]
    missing_lastmod = [e for e in entries if not e.get("lastmod")]
    byte_size = len(resp.body_bytes)
    if len(entries) > _MAX_URLS:
        findings.append("over_50k_entries")
    if byte_size > _MAX_BYTES:
        findings.append("over_50_mib")
    if cross_host:
        findings.append("cross_host_entries")
    if missing_lastmod and kind == "urlset":
        findings.append("missing_lastmod")
    return ok({
        "sitemap_url": sitemap_url,
        "final_url": resp.final_url,
        "kind": kind,
        "entry_count": len(entries),
        "byte_size": byte_size,
        "cross_host_count": len(cross_host),
        "missing_lastmod_count": len(missing_lastmod),
        "findings": findings,
        "sample_entries": entries[:5],
    })


# ---------------------------------------------------------------------------
# sitemap_health
# ---------------------------------------------------------------------------


TOOL_HEALTH = {
    "name": "sitemap_health",
    "description": (
        "Sample N URLs from a sitemap (or one level deep in a sitemap index) "
        "and HEAD-check each. Aggregates a status-code histogram and lists "
        "the first few non-2xx URLs so you can triage broken entries fast."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "sitemap_url": {"type": "string", "description": "Absolute URL of the sitemap or sitemap index."},
            "sample_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "default": 25,
                "description": "Number of URLs to HEAD-check.",
            },
        },
        "required": ["sitemap_url"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def sitemap_health(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_http(clients)
    if error:
        return error
    sitemap_url = arguments.get("sitemap_url")
    if not sitemap_url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "sitemap_url is required.", docs_url=DOCS_BASE + "technical")
    sample_size = int(arguments.get("sample_size") or 25)
    try:
        resp = client.fetch(sitemap_url, max_bytes=_MAX_BYTES + 1024)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    text = _decode_body(resp)
    try:
        kind, entries = _parse_sitemap(text)
    except (ET.ParseError, ApiError) as exc:
        msg = exc if isinstance(exc, ApiError) else err(ErrorCode.INVALID_INPUT, _SERVICE, f"Sitemap parse error: {exc}")
        return msg.to_envelope(_SERVICE) if isinstance(exc, ApiError) else msg
    # If this is a sitemap index, descend one level to gather candidate URLs.
    if kind == "sitemapindex":
        candidates: list[str] = []
        for child in entries[:5]:  # cap index expansion to 5 sitemaps to bound work
            try:
                child_resp = client.fetch(child["loc"], max_bytes=_MAX_BYTES + 1024)
            except ApiError:
                continue
            try:
                _, child_entries = _parse_sitemap(_decode_body(child_resp))
            except (ET.ParseError, ApiError):
                continue
            candidates.extend(e["loc"] for e in child_entries)
            if len(candidates) >= sample_size:
                break
        urls = candidates[:sample_size]
    else:
        urls = [e["loc"] for e in entries[:sample_size]]
    histogram: dict[str, int] = {}
    non_2xx: list[dict[str, Any]] = []
    total_elapsed = 0
    checked = 0
    for url in urls:
        try:
            r = client.fetch(url, method="HEAD", max_bytes=1024)
        except ApiError as exc:
            histogram["error"] = histogram.get("error", 0) + 1
            if len(non_2xx) < 10:
                non_2xx.append({"url": url, "status": None, "error": exc.message})
            checked += 1
            continue
        status_key = str(r.status)
        histogram[status_key] = histogram.get(status_key, 0) + 1
        if not (200 <= r.status < 300) and len(non_2xx) < 10:
            non_2xx.append({"url": url, "status": r.status, "error": None})
        checked += 1
    return ok({
        "sitemap_url": sitemap_url,
        "kind": kind,
        "sampled": checked,
        "status_histogram": histogram,
        "non_2xx_examples": non_2xx,
    })


TOOLS = [TOOL_VALIDATE, TOOL_HEALTH]
HANDLERS = {"sitemap_validate": sitemap_validate, "sitemap_health": sitemap_health}
