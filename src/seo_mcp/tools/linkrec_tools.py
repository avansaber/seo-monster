"""internal_link_recommend (roadmap Track D, Wave 1).

Recommend specific source->target internal links from high-authority pages to
striking-distance pages, with anchor text. Built on the same BFS crawl as
``internal_link_graph`` plus GSC for target selection. Fully free (HTTP + GSC +
lexical relevance); no LLM, no paid API.

Method (design doc §6 D1):
  * TARGETS: GSC page x query rows in the striking-distance band (default
    position 8-20) with real demand (impressions >= floor). These are the pages
    a link nudge can move onto / up page 1.
  * SOURCES: crawled pages with high internal in-degree (authority proxy) that
    are topically relevant to the target query and do NOT already link it.
  * RELEVANCE: lexical overlap between the source's body terms and the target
    query terms (the LinkStorm-study method; cheap, no embeddings). A floor
    rejects weak links.
  * ANCHOR: the target query as a descriptive phrase. Guarded against
    over-optimization: we de-dupe against anchors that already point at the
    target, and never recommend nofollow (sculpting has been dead since 2009).

Caveat baked in: GSC average position was corrupted by the 2025 impression bug /
&num=100 change, so when in doubt prefer the impressions + clicks demand signal
over the raw position band.
"""

from __future__ import annotations

from collections import deque
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urldefrag, urljoin, urlparse

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations, missing_site_error, require_client, resolve_site
from ._html import content_terms

_SERVICE = "technical"
_HTTP_REMEDIATION = "No setup needed; the HTTP client is built in."
_GSC_REMEDIATION = (
    "Configure Google auth with Search Console access (target selection uses "
    "GSC striking-distance queries). See README > Auth."
)
_MAX_PAGES = 200
_MAX_DEPTH = 3
_ROW_LIMIT = 25000

# Weights for the source ranking (relevance vs internal authority).
_W_RELEVANCE = 0.6
_W_AUTHORITY = 0.4
_PER_SOURCE_CAP = 5  # over-linking guard


class _CrawlParser(HTMLParser):
    """Collect body text + (href, anchor_text) pairs in one pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._in_a = False
        self._a_href = ""
        self._a_text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag == "a":
            attrs = {k.lower(): (v or "") for k, v in attrs_list}
            href = attrs.get("href", "").strip()
            if href:
                self._in_a = True
                self._a_href = href
                self._a_text = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip > 0:
            self._skip -= 1
            return
        if tag == "a" and self._in_a:
            self.links.append((self._a_href, " ".join(" ".join(self._a_text).split())))
            self._in_a = False
            self._a_href = ""
            self._a_text = []

    def handle_data(self, data: str) -> None:
        if self._skip > 0:
            return
        self._text.append(data)
        if self._in_a:
            self._a_text.append(data)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self._text).split())


def _norm(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    if not p.scheme:
        return url
    netloc = p.netloc.lower()
    path = p.path or "/"
    return f"{p.scheme}://{netloc}{path}" + (f"?{p.query}" if p.query else "")


def _same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()


TOOL = {
    "name": "internal_link_recommend",
    "description": (
        "Recommend specific internal links (source page -> target page, with "
        "anchor text) from high-authority pages to striking-distance pages. "
        "Crawls from start_url for the internal link graph (authority + existing "
        "links) and uses GSC to find striking-distance targets (default position "
        "8-20 with real impressions). Ranks sources by lexical relevance to the "
        "target query + internal in-degree, skips pages that already link the "
        "target, balances anchor text, and never suggests nofollow. Free; "
        "read-only; does not guarantee a ranking change."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "start_url": {"type": "string", "description": "Absolute http(s) URL to crawl from for the link graph."},
            "site_url": {"type": "string", "description": "GSC property for target selection. Defaults to the configured default site."},
            "days": {"type": "integer", "minimum": 1, "maximum": 240, "description": "GSC window. Defaults to 28."},
            "max_pages": {"type": "integer", "minimum": 1, "maximum": _MAX_PAGES, "description": f"Max pages to crawl. Default 50, ceiling {_MAX_PAGES}."},
            "position_min": {"type": "number", "description": "Striking-distance lower bound (default 8)."},
            "position_max": {"type": "number", "description": "Striking-distance upper bound (default 20)."},
            "impressions_min": {"type": "integer", "minimum": 1, "description": "Min impressions for a target query. Default 30."},
            "relevance_floor": {"type": "number", "description": "Min source/target query lexical overlap (0-1). Default 0.34."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Max recommendations. Default 25."},
        },
        "required": ["start_url"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def _crawl(client: Any, start_url: str, max_pages: int) -> dict[str, dict[str, Any]]:
    """BFS the same host. Returns fetched-page records keyed by normalized URL:
    {in_degree, terms:set, out:set(norm), title}. Also records in-degree for
    discovered-but-unfetched targets."""
    start = _norm(start_url)
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    seen: set[str] = {start}
    pages: dict[str, dict[str, Any]] = {}
    fetched = 0
    while queue and fetched < max_pages:
        url, depth = queue.popleft()
        try:
            resp = client.fetch(url)
        except ApiError:
            fetched += 1
            continue
        if not (200 <= resp.status < 300):
            fetched += 1
            continue
        parser = _CrawlParser()
        parser.feed(resp.body_text)
        out: set[str] = set()
        for href, _anchor in parser.links:
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            resolved = _norm(urljoin(resp.final_url, href))
            if not resolved.startswith(("http://", "https://")):
                continue
            if not _same_host(resolved, start):
                continue
            out.add(resolved)
        prior_in = pages.get(url, {}).get("in_degree", 0)
        rec = pages.setdefault(url, {})
        rec.update({
            "in_degree": prior_in,
            "terms": content_terms(parser.text),
            "out": out,
            "anchors": [(t, a) for (h, a) in parser.links for t in [_norm(urljoin(resp.final_url, h))] if a],
            "fetched": True,
        })
        fetched += 1
        for tgt in out:
            t = pages.setdefault(tgt, {"in_degree": 0, "fetched": False})
            t["in_degree"] = t.get("in_degree", 0) + 1
            if depth + 1 <= _MAX_DEPTH and tgt not in seen and (fetched + len(queue)) < max_pages:
                seen.add(tgt)
                queue.append((tgt, depth + 1))
    return pages


def internal_link_recommend(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    http, error = require_client(clients, "http", _SERVICE, remediation=_HTTP_REMEDIATION)
    if error:
        return error
    gsc, error = require_client(clients, "gsc", "gsc", remediation=_GSC_REMEDIATION)
    if error:
        return error

    start_url = arguments.get("start_url")
    if not start_url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "start_url is required.", docs_url=DOCS_BASE + "technical")
    parsed = urlparse(start_url)
    if not parsed.scheme or not parsed.netloc:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, f"start_url must be absolute, got {start_url!r}.")

    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()

    days = int(arguments.get("days", 28))
    max_pages = min(int(arguments.get("max_pages") or 50), _MAX_PAGES)
    position_min = float(arguments.get("position_min", 8))
    position_max = float(arguments.get("position_max", 20))
    impressions_min = int(arguments.get("impressions_min", 30))
    relevance_floor = float(arguments.get("relevance_floor", 0.34))
    limit = int(arguments.get("limit", 25))

    # 1. GSC striking-distance targets (page x query).
    today = date.today()
    body = {
        "startDate": (today - timedelta(days=days)).isoformat(),
        "endDate": today.isoformat(),
        "dimensions": ["page", "query"],
        "rowLimit": _ROW_LIMIT,
        "type": "web",
        "dataState": getattr(config, "gsc_data_state", "final"),
    }
    try:
        resp = gsc.search_analytics(site, body)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    targets: list[dict[str, Any]] = []
    for r in resp.get("rows", []):
        keys = r.get("keys") or []
        if len(keys) < 2:
            continue
        page, query = keys[0], keys[1]
        position = float(r.get("position", 0) or 0)
        impressions = float(r.get("impressions", 0) or 0)
        if not (position_min <= position <= position_max):
            continue
        if impressions < impressions_min:
            continue
        targets.append({
            "page": _norm(page),
            "page_raw": page,
            "query": query,
            "position": round(position, 1),
            "impressions": int(impressions),
        })

    if not targets:
        return ok(_empty(site, start_url, position_min, position_max, impressions_min, relevance_floor,
                         "No striking-distance targets found in the GSC window for that band."))

    # 2. Crawl the link graph.
    pages = _crawl(http, start_url, max_pages)
    fetched = {u: rec for u, rec in pages.items() if rec.get("fetched")}
    if not fetched:
        return ok(_empty(site, start_url, position_min, position_max, impressions_min, relevance_floor,
                         "Crawl fetched no pages (start_url unreachable or non-HTML)."))
    max_authority = max((rec.get("in_degree", 0) for rec in fetched.values()), default=0) or 1

    # anchors already pointing at each target (over-optimization de-dupe).
    anchors_to: dict[str, set[str]] = {}
    for rec in fetched.values():
        for tgt, atext in rec.get("anchors", []):
            if atext:
                anchors_to.setdefault(tgt, set()).add(atext.strip().lower())

    # 3. Build recommendations.
    recs: list[dict[str, Any]] = []
    for tgt in targets:
        qterms = content_terms(tgt["query"])
        if not qterms:
            continue
        anchor = tgt["query"]
        anchor_dup = anchor.strip().lower() in anchors_to.get(tgt["page"], set())
        for src_url, rec in fetched.items():
            if src_url == tgt["page"]:
                continue
            if tgt["page"] in rec.get("out", set()):
                continue  # already links the target
            matched = qterms & rec.get("terms", set())
            if not matched:
                continue
            relevance = len(matched) / len(qterms)
            if relevance < relevance_floor:
                continue
            authority = rec.get("in_degree", 0)
            score = round(_W_RELEVANCE * relevance + _W_AUTHORITY * (authority / max_authority), 4)
            anchor_type = "exact_match" if relevance >= 1.0 else "partial_match"
            recs.append({
                "source_url": src_url,
                "target_url": tgt["page_raw"],
                "target_query": tgt["query"],
                "target_position": tgt["position"],
                "target_impressions": tgt["impressions"],
                "anchor_suggestion": anchor,
                "anchor_type": anchor_type,
                "anchor_already_used": anchor_dup,
                "relevance": round(relevance, 3),
                "source_in_degree": authority,
                "matched_terms": sorted(matched),
                "score": score,
            })

    recs.sort(key=lambda r: r["score"], reverse=True)
    # per-source cap (over-linking guard) then global limit.
    per_source: dict[str, int] = {}
    capped: list[dict[str, Any]] = []
    for r in recs:
        n = per_source.get(r["source_url"], 0)
        if n >= _PER_SOURCE_CAP:
            continue
        per_source[r["source_url"]] = n + 1
        capped.append(r)
        if len(capped) >= limit:
            break

    return ok({
        "site_url": site,
        "start_url": start_url,
        "pages_crawled": len(fetched),
        "targets_found": len(targets),
        "recommendation_count": len(capped),
        "recommendations": capped,
        "weights": {"relevance": _W_RELEVANCE, "authority": _W_AUTHORITY},
        "filters_applied": {
            "position_band": [position_min, position_max],
            "impressions_min": impressions_min,
            "relevance_floor": relevance_floor,
            "per_source_cap": _PER_SOURCE_CAP,
        },
        "caveats": [
            "Relevance is lexical (query-term overlap), not semantic; review each "
            "suggestion in context before adding it.",
            "GSC average position was corrupted by the 2025 impression bug / "
            "num=100 change; when uncertain, trust the impressions + clicks "
            "demand over the raw position band.",
            "anchor_already_used flags targets that already have this exact anchor "
            "elsewhere -- vary the wording to avoid anchor over-optimization. "
            "Never use nofollow on internal links (sculpting has been dead since "
            "2009).",
        ],
    })


def _empty(site, start_url, pmin, pmax, imin, floor, reason) -> dict[str, Any]:
    return {
        "site_url": site,
        "start_url": start_url,
        "recommendation_count": 0,
        "recommendations": [],
        "note": reason,
        "filters_applied": {
            "position_band": [pmin, pmax],
            "impressions_min": imin,
            "relevance_floor": floor,
        },
    }


TOOLS = [TOOL]
HANDLERS = {"internal_link_recommend": internal_link_recommend}
