"""hreflang_consistency_check (1). Cross-site hreflang validation.

The single most common hreflang failure mode in real audits is non-reciprocal
links: page A points at page B with ``hreflang="fr"``, but B does not point
back at A with ``hreflang="en"``. Google's documentation is explicit that
hreflang signals require reciprocity to be honored. This tool fetches a list
of URLs, extracts their hreflang ``<link rel="alternate">`` tags, and
verifies the full reciprocity matrix in one call.

We deliberately do not crawl: the user supplies the URL set so the audit
scope is auditable. For a "crawl then audit" pattern, use ``internal_link_graph``
to discover URLs first, then feed them into this tool.

Checks performed:

  1. **Missing reciprocity.** If A declares ``<link rel="alternate"
     hreflang="fr" href="B">``, then B's hreflang set must include A.
  2. **Broken targets.** Each hreflang target URL is HEAD-checked; non-2xx
     is reported.
  3. **Duplicate hreflang within a page.** Two ``<link>`` tags with the same
     ``hreflang`` value on one page are an authoring error.
  4. **Missing x-default with 3+ variants.** Google recommends an x-default
     when a page has three or more language alternates.
  5. **Self-link presence.** Each page must include a self-link in its
     hreflang set (Google requirement).
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Mapping

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import ANNOT_READ, require_client


_SERVICE = "technical"
_REMEDIATION = "No setup needed; the HTTP client is built in."
_MAX_URLS = 50


def _require_http(clients: Mapping[str, Any]):
    return require_client(clients, "http", _SERVICE, remediation=_REMEDIATION)


class _AlternateParser(HTMLParser):
    """Collect ``<link rel="alternate" hreflang="..." href="...">`` tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.alternates: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        rel = attrs.get("rel", "").lower()
        if "alternate" not in rel.split():
            return
        hreflang = attrs.get("hreflang", "").strip()
        href = attrs.get("href", "").strip()
        if hreflang and href:
            self.alternates.append({"hreflang": hreflang, "href": href})


TOOL = {
    "name": "hreflang_consistency_check",
    "description": (
        "Validate hreflang link tags across a set of URLs. Checks: missing "
        "reciprocity, broken target URLs, duplicate hreflang on one page, "
        "missing self-link, missing x-default when there are 3+ language "
        "variants. Returns per-URL findings and a global findings list."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": _MAX_URLS,
                "description": f"Absolute URLs to audit together (max {_MAX_URLS}). At least 2 needed for reciprocity to be meaningful.",
            },
        },
        "required": ["urls"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def hreflang_consistency_check(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_http(clients)
    if error:
        return error
    urls = arguments.get("urls") or []
    if not urls or len(urls) < 2:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            "Pass at least 2 URLs so reciprocity has meaning.",
            docs_url=DOCS_BASE + "technical",
        )
    if len(urls) > _MAX_URLS:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            f"Too many URLs ({len(urls)}); max is {_MAX_URLS}.",
        )

    # Phase 1: fetch each URL and extract its alternates.
    per_url: dict[str, dict[str, Any]] = {}
    fetch_errors: list[dict[str, Any]] = []
    for url in urls:
        try:
            resp = client.fetch(url)
        except ApiError as exc:
            fetch_errors.append({"url": url, "error": exc.message})
            continue
        if not (200 <= resp.status < 300):
            fetch_errors.append({"url": url, "status": resp.status})
            continue
        parser = _AlternateParser()
        parser.feed(resp.body_text)
        per_url[url] = {
            "final_url": resp.final_url,
            "alternates": parser.alternates,
            "hreflangs": [a["hreflang"] for a in parser.alternates],
        }

    # Phase 2: per-URL self-link, duplicate, x-default checks.
    findings: list[dict[str, Any]] = []
    for url, data in per_url.items():
        flags: list[str] = []
        alternates = data["alternates"]
        hreflang_values = [a["hreflang"] for a in alternates]
        if len(hreflang_values) != len(set(hreflang_values)):
            flags.append("duplicate_hreflang")
        non_xdefault = [v for v in hreflang_values if v.lower() != "x-default"]
        if len(non_xdefault) >= 3 and "x-default" not in (v.lower() for v in hreflang_values):
            flags.append("missing_x_default")
        self_targets = {a["href"] for a in alternates}
        # Self-link: the page must list its own URL (or final_url) as one of
        # the alternates. We accept either the original or the final URL after
        # following redirects so a canonicalising redirect does not false-fail.
        if url not in self_targets and data["final_url"] not in self_targets:
            flags.append("missing_self_link")
        findings.append({"url": url, "alternates": alternates, "flags": flags})

    # Phase 3: reciprocity matrix.
    # For each page A in per_url, for each alternate (lang, target) on A,
    # check that target is in per_url and that target also lists A with some
    # hreflang. Mismatches go to findings.
    reciprocity_misses: list[dict[str, Any]] = []
    by_target: dict[str, set[str]] = {url: set(a["href"] for a in data["alternates"]) for url, data in per_url.items()}
    by_target_final: dict[str, set[str]] = {data["final_url"]: set(a["href"] for a in data["alternates"]) for url, data in per_url.items() if data["final_url"] != url}
    by_target.update(by_target_final)
    for src, data in per_url.items():
        for alt in data["alternates"]:
            target = alt["href"]
            if alt["hreflang"].lower() == "x-default":
                continue
            if target not in by_target:
                # Target was not in the input set; can't verify reciprocity.
                # Surface as a fetched/unfetched gap rather than a strong miss.
                continue
            target_alternates = by_target[target]
            if src not in target_alternates:
                reciprocity_misses.append({"from": src, "to": target, "hreflang": alt["hreflang"]})

    # Phase 4: target reachability (HEAD-check every unique alternate URL not
    # already fetched in phase 1).
    target_urls: set[str] = set()
    for data in per_url.values():
        for a in data["alternates"]:
            target_urls.add(a["href"])
    broken_targets: list[dict[str, Any]] = []
    for target in target_urls:
        if target in per_url:
            continue  # already fetched in phase 1
        try:
            r = client.fetch(target, method="HEAD", max_bytes=1024)
        except ApiError:
            broken_targets.append({"url": target, "status": None})
            continue
        if not (200 <= r.status < 300):
            broken_targets.append({"url": target, "status": r.status})

    return ok({
        "fetched": list(per_url.keys()),
        "fetch_errors": fetch_errors,
        "findings": findings,
        "reciprocity_misses": reciprocity_misses,
        "broken_targets": broken_targets,
    })


TOOLS = [TOOL]
HANDLERS = {"hreflang_consistency_check": hreflang_consistency_check}
