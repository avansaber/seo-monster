"""internal_link_graph (1). Small-scale internal-linking analysis.

BFS crawl starting from one URL, bounded by ``max_depth`` and ``max_pages``.
Stays within the same host (no cross-domain). Extracts every internal
``<a href>`` from each page, follows them in subsequent BFS rounds, and
emits a per-page in-degree / out-degree summary plus orphan + broken
internal-link findings.

Hard caps:

  * ``max_depth`` default 2, ceiling 4 (the schema rejects higher).
  * ``max_pages`` default 50, ceiling 200.
  * Each fetched body is HttpClient-capped (10 MiB default) so a misconfigured
    pagination loop cannot wedge the server.

This tool is **not** a replacement for a full crawler like Screaming Frog
or Sitebulb. Its job is the fast "where are my orphans, do I have broken
internal links, how deep is my structure?" question on a small section of
a site - the kind of check that fits in an MCP session.
"""

from __future__ import annotations

from collections import deque
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urldefrag, urljoin, urlparse

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import ANNOT_READ, require_client


_SERVICE = "technical"
_REMEDIATION = "No setup needed; the HTTP client is built in."
_MAX_DEPTH = 4
_MAX_PAGES = 200


def _require_http(clients: Mapping[str, Any]):
    return require_client(clients, "http", _SERVICE, remediation=_REMEDIATION)


class _LinkExtractor(HTMLParser):
    """Collect every ``<a href>`` on the page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        href = attrs.get("href", "").strip()
        if href:
            self.hrefs.append(href)


def _same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc == urlparse(b).netloc


def _normalize(url: str) -> str:
    """Strip the fragment and any default port. We do NOT drop trailing
    slashes here because they can be semantically significant."""
    url, _ = urldefrag(url)
    return url


TOOL = {
    "name": "internal_link_graph",
    "description": (
        "Small BFS crawl from a starting URL within the same host. Returns "
        "per-page in-degree + out-degree, orphan pages (zero in-degree), "
        "broken internal links (4xx/5xx), and depth distribution. Hard caps: "
        f"max_depth ≤ {_MAX_DEPTH}, max_pages ≤ {_MAX_PAGES}. Not a "
        "replacement for a full crawler; sized for in-session triage."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "start_url": {"type": "string", "description": "Absolute http(s) URL to crawl from."},
            "max_depth": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_DEPTH,
                "default": 2,
                "description": f"Max BFS depth. Default 2, ceiling {_MAX_DEPTH}.",
            },
            "max_pages": {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_PAGES,
                "default": 50,
                "description": f"Max pages to fetch. Default 50, ceiling {_MAX_PAGES}.",
            },
        },
        "required": ["start_url"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def internal_link_graph(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_http(clients)
    if error:
        return error
    start_url = arguments.get("start_url")
    if not start_url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "start_url is required.", docs_url=DOCS_BASE + "technical")
    parsed = urlparse(start_url)
    if not parsed.scheme or not parsed.netloc:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, f"start_url must be absolute, got {start_url!r}.")
    max_depth = min(int(arguments.get("max_depth") or 2), _MAX_DEPTH)
    max_pages = min(int(arguments.get("max_pages") or 50), _MAX_PAGES)

    start = _normalize(start_url)
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    seen: set[str] = {start}
    # per-page state: depth, out_degree, in_degree, fetch status
    pages: dict[str, dict[str, Any]] = {}
    broken_links: list[dict[str, Any]] = []
    fetched_count = 0

    while queue and fetched_count < max_pages:
        url, depth = queue.popleft()
        try:
            resp = client.fetch(url)
        except ApiError as exc:
            pages[url] = {"depth": depth, "status": None, "error": exc.message, "out_degree": 0, "in_degree": pages.get(url, {}).get("in_degree", 0)}
            broken_links.append({"url": url, "status": None, "error": exc.message})
            fetched_count += 1
            continue
        prior_in = pages.get(url, {}).get("in_degree", 0)
        page_record = {"depth": depth, "status": resp.status, "in_degree": prior_in, "out_degree": 0}
        if not (200 <= resp.status < 300):
            broken_links.append({"url": url, "status": resp.status})
            pages[url] = page_record
            fetched_count += 1
            continue
        parser = _LinkExtractor()
        parser.feed(resp.body_text)
        out_targets: set[str] = set()
        for raw in parser.hrefs:
            if raw.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            resolved = _normalize(urljoin(resp.final_url, raw))
            if not resolved.startswith(("http://", "https://")):
                continue
            if not _same_host(resolved, start):
                continue
            out_targets.add(resolved)
        page_record["out_degree"] = len(out_targets)
        pages[url] = page_record
        fetched_count += 1
        for tgt in out_targets:
            pages.setdefault(tgt, {"depth": None, "status": None, "in_degree": 0, "out_degree": 0})
            pages[tgt]["in_degree"] += 1
            if depth + 1 <= max_depth and tgt not in seen and fetched_count + len(queue) < max_pages:
                seen.add(tgt)
                queue.append((tgt, depth + 1))

    # Orphans: pages discovered (queued / fetched) with in_degree 0 and not
    # the start URL. We surface only pages we actually fetched, since a queued
    # but-not-yet-visited page tells us nothing useful.
    orphans = [
        url for url, rec in pages.items()
        if rec["in_degree"] == 0 and url != start and rec.get("status") is not None
    ]
    depth_distribution: dict[str, int] = {}
    for rec in pages.values():
        if rec.get("status") is None and rec.get("depth") is None:
            continue
        if rec.get("status") is None:
            continue
        d = rec.get("depth", 0)
        depth_distribution[str(d)] = depth_distribution.get(str(d), 0) + 1

    return ok({
        "start_url": start,
        "pages_fetched": fetched_count,
        "pages_discovered": len(pages),
        "depth_distribution": depth_distribution,
        "broken_links": broken_links,
        "orphans": orphans,
        "pages": [{"url": u, **r} for u, r in pages.items()],
    })


TOOLS = [TOOL]
HANDLERS = {"internal_link_graph": internal_link_graph}
