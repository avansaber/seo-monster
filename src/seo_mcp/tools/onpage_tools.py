"""On-page technical-SEO tools (3): ``inspect_meta``, ``check_canonical``,
``mixed_content_check``.

These tools fetch a single URL via the shared ``HttpClient`` and parse the
resulting HTML with the stdlib ``html.parser``. No third-party HTML parser
dependency: the surface we care about (head meta, link rel, og/twitter,
hreflang, h1 count, hrefs) is small enough that stdlib does the job and
keeps the install footprint flat.

All three tools are read-only. They fail closed when the fetched URL is
unreachable or returns non-2xx (other than 3xx for ``redirect_chain_audit``,
which lives in a separate module).
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import ANNOT_READ, require_client


_SERVICE = "technical"
_REMEDIATION = "No setup needed; the HTTP client is built in."


def _require_http(clients: Mapping[str, Any]):
    return require_client(clients, "http", _SERVICE, remediation=_REMEDIATION)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class _HeadParser(HTMLParser):
    """Collect the on-page SEO surface in one pass.

    We do not build a DOM; we accumulate the handful of attributes SEO tools
    actually look at. ``in_head`` lets us limit some collectors (title) to the
    document head; body-level attribute collectors (h1 counts, hrefs, image
    refs) intentionally span the whole document.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self._collecting_title = False
        self.meta_description: str | None = None
        self.meta_robots: str | None = None
        self.canonical: str | None = None
        self.og: dict[str, str] = {}
        self.twitter: dict[str, str] = {}
        self.hreflang: list[dict[str, str]] = []
        self.h1_count = 0
        self.links: list[dict[str, str]] = []
        self.imgs: list[dict[str, str]] = []
        self.scripts: list[dict[str, str]] = []
        self.forms: list[dict[str, str]] = []
        self.iframes: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        if tag == "title":
            self._collecting_title = True
            return
        if tag == "meta":
            name = attrs.get("name", "").lower()
            prop = attrs.get("property", "").lower()
            content = attrs.get("content", "")
            if name == "description" and self.meta_description is None:
                self.meta_description = content
            elif name == "robots" and self.meta_robots is None:
                self.meta_robots = content
            elif prop.startswith("og:"):
                self.og.setdefault(prop, content)
            elif name.startswith("twitter:"):
                self.twitter.setdefault(name, content)
            return
        if tag == "link":
            rel = attrs.get("rel", "").lower()
            href = attrs.get("href", "")
            if "canonical" in rel and self.canonical is None:
                self.canonical = href
            elif "alternate" in rel and "hreflang" in attrs:
                self.hreflang.append({"hreflang": attrs.get("hreflang", ""), "href": href})
            return
        if tag == "h1":
            self.h1_count += 1
            return
        if tag == "a":
            href = attrs.get("href", "")
            if href:
                self.links.append({"href": href, "rel": attrs.get("rel", "")})
            return
        if tag == "img":
            src = attrs.get("src", "")
            srcset = attrs.get("srcset", "")
            if src or srcset:
                self.imgs.append({"src": src, "srcset": srcset, "alt": attrs.get("alt", "")})
            return
        if tag == "script":
            src = attrs.get("src", "")
            if src:
                self.scripts.append({"src": src})
            return
        if tag == "form":
            action = attrs.get("action", "")
            if action:
                self.forms.append({"action": action, "method": attrs.get("method", "get")})
            return
        if tag == "iframe":
            src = attrs.get("src", "")
            if src:
                self.iframes.append({"src": src})
            return

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._collecting_title = False

    def handle_data(self, data: str) -> None:
        if self._collecting_title:
            self.title = (self.title or "") + data


def _parse(html: str) -> _HeadParser:
    parser = _HeadParser()
    parser.feed(html)
    return parser


def _fetch_html(client, url: str):
    """Fetch a URL, raise ApiError if status != 2xx, return (response, parsed)."""
    resp = client.fetch(url)
    if not (200 <= resp.status < 300):
        raise ApiError(
            ErrorCode.UPSTREAM_ERROR,
            f"Fetch of {url!r} returned HTTP {resp.status}.",
            details={"status": resp.status, "final_url": resp.final_url},
        )
    return resp, _parse(resp.body_text)


# ---------------------------------------------------------------------------
# inspect_meta
# ---------------------------------------------------------------------------


TOOL_INSPECT_META = {
    "name": "inspect_meta",
    "description": (
        "Fetch a single URL and return its on-page SEO surface: title, meta "
        "description, meta robots, canonical, Open Graph + Twitter Card "
        "tags, hreflang list, and the H1 count. Read-only HTTP GET."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL to inspect."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def inspect_meta(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_http(clients)
    if error:
        return error
    url = arguments.get("url")
    if not url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url is required.", docs_url=DOCS_BASE + "technical")
    try:
        resp, parsed = _fetch_html(client, url)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    title = (parsed.title or "").strip() or None
    return ok({
        "url": url,
        "final_url": resp.final_url,
        "status": resp.status,
        "title": title,
        "title_length": len(title) if title else 0,
        "meta_description": parsed.meta_description,
        "meta_description_length": len(parsed.meta_description) if parsed.meta_description else 0,
        "meta_robots": parsed.meta_robots,
        "canonical": parsed.canonical,
        "h1_count": parsed.h1_count,
        "open_graph": parsed.og,
        "twitter": parsed.twitter,
        "hreflang": parsed.hreflang,
    })


# ---------------------------------------------------------------------------
# check_canonical
# ---------------------------------------------------------------------------


def _normalize(url: str) -> str:
    """Normalize for canonical comparison: strip fragments and a single
    trailing slash difference, lowercase scheme + host."""
    p = urlparse(url)
    scheme = p.scheme.lower()
    host = p.netloc.lower()
    path = p.path or "/"
    # Collapse a trailing slash for everything except the bare "/".
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    qs = f"?{p.query}" if p.query else ""
    return f"{scheme}://{host}{path}{qs}"


TOOL_CHECK_CANONICAL = {
    "name": "check_canonical",
    "description": (
        "Fetch a URL and analyse its canonical link tag: report whether it "
        "is self-referential, cross-host, protocol-mismatched, or trailing-"
        "slash drift; flag missing canonical, and follow one canonical hop "
        "to confirm it itself returns 2xx."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL to inspect."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def check_canonical(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_http(clients)
    if error:
        return error
    url = arguments.get("url")
    if not url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url is required.", docs_url=DOCS_BASE + "technical")
    try:
        resp, parsed = _fetch_html(client, url)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    canonical = parsed.canonical
    findings: list[str] = []
    if not canonical:
        findings.append("no_canonical")
        canonical_resolved = None
        canonical_status = None
    else:
        canonical_resolved = urljoin(resp.final_url, canonical)
        n_fetched = _normalize(resp.final_url)
        n_canon = _normalize(canonical_resolved)
        if n_fetched != n_canon:
            findings.append("cross_url")
            # Sub-classifications to help triage.
            p_f, p_c = urlparse(n_fetched), urlparse(n_canon)
            if p_f.netloc != p_c.netloc:
                findings.append("cross_host")
            if p_f.scheme != p_c.scheme:
                findings.append("protocol_mismatch")
            if p_f.netloc == p_c.netloc and p_f.path.rstrip("/") == p_c.path.rstrip("/") and p_f.path != p_c.path:
                findings.append("trailing_slash_drift")
        # Confirm the canonical target itself returns 2xx.
        try:
            target = client.fetch(canonical_resolved)
            canonical_status = target.status
            if not (200 <= target.status < 300):
                findings.append("canonical_target_non_2xx")
        except ApiError:
            canonical_status = None
            findings.append("canonical_target_unreachable")
    return ok({
        "url": url,
        "final_url": resp.final_url,
        "canonical_declared": canonical,
        "canonical_resolved": canonical_resolved,
        "canonical_status": canonical_status,
        "is_self_referential": canonical is not None and "cross_url" not in findings,
        "findings": findings,
    })


# ---------------------------------------------------------------------------
# mixed_content_check
# ---------------------------------------------------------------------------


TOOL_MIXED_CONTENT = {
    "name": "mixed_content_check",
    "description": (
        "Fetch an HTTPS page and report any sub-resource references that use "
        "plain http:// (img/script/iframe/form action/anchor href). Mixed "
        "content blocks browsers from running scripts and triggers warnings "
        "on user-facing pages. No-op for http:// pages (returns 'not_https')."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute https:// URL to inspect."},
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def _http_refs(items: list[dict[str, str]], key: str) -> list[str]:
    out: list[str] = []
    for item in items:
        value = item.get(key) or ""
        if value.startswith("http://"):
            out.append(value)
        # srcset is a comma-separated list of "URL descriptor" pairs.
        if key == "src" and item.get("srcset"):
            for piece in item["srcset"].split(","):
                candidate = piece.strip().split(" ", 1)[0]
                if candidate.startswith("http://"):
                    out.append(candidate)
    return out


def mixed_content_check(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_http(clients)
    if error:
        return error
    url = arguments.get("url")
    if not url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url is required.", docs_url=DOCS_BASE + "technical")
    if not url.lower().startswith("https://"):
        return ok({"url": url, "verdict": "not_https", "violations": []})
    try:
        resp, parsed = _fetch_html(client, url)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    violations: dict[str, list[str]] = {
        "img": _http_refs(parsed.imgs, "src"),
        "script": _http_refs(parsed.scripts, "src"),
        "iframe": _http_refs(parsed.iframes, "src"),
        "form_action": _http_refs(parsed.forms, "action"),
    }
    total = sum(len(v) for v in violations.values())
    return ok({
        "url": url,
        "final_url": resp.final_url,
        "verdict": "clean" if total == 0 else "mixed_content_found",
        "total_violations": total,
        "violations": violations,
    })


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


TOOLS = [TOOL_INSPECT_META, TOOL_CHECK_CANONICAL, TOOL_MIXED_CONTENT]
HANDLERS = {
    "inspect_meta": inspect_meta,
    "check_canonical": check_canonical,
    "mixed_content_check": mixed_content_check,
}
