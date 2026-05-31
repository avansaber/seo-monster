"""Structured-data tools (2): ``inspect_schema`` and ``validate_schema``.

Both fetch a URL via the shared HttpClient, extract every JSON-LD block
from the document (``<script type="application/ld+json">``), and report on
the structured data. ``inspect_schema`` is descriptive (what's there);
``validate_schema`` adds per-type required-field checks for the nine
highest-traffic Google Rich Results types.

Why JSON-LD only: Google's own recommendation for new sites since 2019.
Microdata and RDFa are still parseable but represent a shrinking share of
real-world structured data, and supporting them would double the parser
surface for marginal added value. If a future user needs them, both can be
added behind a flag without breaking the API of either of these tools.

Required + recommended fields below are quoted from Google Search Central
as of 2026 and the schema.org Vocabulary. Where Google diverges from
schema.org (e.g. Product `offers` is required by Google but only
recommended by schema.org), we follow Google because Rich Results
eligibility is the user-facing outcome SEO tools are usually optimizing
for.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from typing import Any, Mapping

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import ANNOT_READ, require_client


_SERVICE = "technical"
_REMEDIATION = "No setup needed; the HTTP client is built in."


def _require_http(clients: Mapping[str, Any]):
    return require_client(clients, "http", _SERVICE, remediation=_REMEDIATION)


# ---------------------------------------------------------------------------
# JSON-LD extraction
# ---------------------------------------------------------------------------


class _JsonLdExtractor(HTMLParser):
    """Collect raw text inside every ``<script type="application/ld+json">``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._collecting = False
        self._buffer: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attrs = {k.lower(): (v or "") for k, v in attrs_list}
        ctype = attrs.get("type", "").lower().strip()
        if ctype == "application/ld+json":
            self._collecting = True
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._collecting:
            text = "".join(self._buffer).strip()
            if text:
                self.blocks.append(text)
            self._collecting = False
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._collecting:
            self._buffer.append(data)


def _extract_jsonld(html: str) -> list[Any]:
    """Return a flat list of parsed JSON-LD top-level entities.

    A page can declare multiple ``<script type="application/ld+json">`` blocks,
    and each block can be a single object, an array of objects, or a graph
    wrapper ``{"@context": ..., "@graph": [...]}``. We flatten all three into
    one list so the tool layer just sees entities.
    """
    parser = _JsonLdExtractor()
    parser.feed(html)
    entities: list[Any] = []
    for raw in parser.blocks:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Skip silently rather than failing the whole call: a malformed
            # block is a real-world finding the tool layer surfaces below.
            entities.append({"_parse_error": True, "_raw": raw[:200]})
            continue
        if isinstance(parsed, list):
            entities.extend(parsed)
        elif isinstance(parsed, dict) and isinstance(parsed.get("@graph"), list):
            entities.extend(parsed["@graph"])
            ctx = parsed.get("@context")
            # Promote @context from the wrapper onto entries that don't have
            # their own, so the @type lookup downstream sees full context.
            if ctx is not None:
                for ent in parsed["@graph"]:
                    if isinstance(ent, dict) and "@context" not in ent:
                        ent["@context"] = ctx
        elif isinstance(parsed, dict):
            entities.append(parsed)
        else:
            entities.append(parsed)
    return entities


def _types_of(entity: Any) -> list[str]:
    """Return the @type values for an entity, normalized to a list."""
    if not isinstance(entity, dict):
        return []
    t = entity.get("@type")
    if t is None:
        return []
    if isinstance(t, list):
        return [str(x) for x in t]
    return [str(t)]


def _fetch_html(client, url: str):
    resp = client.fetch(url)
    if not (200 <= resp.status < 300):
        raise ApiError(
            ErrorCode.UPSTREAM_ERROR,
            f"Fetch of {url!r} returned HTTP {resp.status}.",
            details={"status": resp.status, "final_url": resp.final_url},
        )
    return resp


# ---------------------------------------------------------------------------
# inspect_schema
# ---------------------------------------------------------------------------


TOOL_INSPECT = {
    "name": "inspect_schema",
    "description": (
        "Extract every JSON-LD block from a page and report the schema.org "
        "@type counts plus a sample entity per type. Discovery tool: tells "
        "you what structured data exists. Pair with validate_schema to "
        "check required-field compliance."
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


def inspect_schema(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_http(clients)
    if error:
        return error
    url = arguments.get("url")
    if not url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url is required.", docs_url=DOCS_BASE + "technical")
    try:
        resp = _fetch_html(client, url)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    entities = _extract_jsonld(resp.body_text)
    parse_errors = sum(1 for e in entities if isinstance(e, dict) and e.get("_parse_error"))
    type_counts: dict[str, int] = {}
    samples: dict[str, dict[str, Any]] = {}
    for entity in entities:
        for t in _types_of(entity):
            type_counts[t] = type_counts.get(t, 0) + 1
            if t not in samples:
                samples[t] = entity
    return ok({
        "url": url,
        "final_url": resp.final_url,
        "block_count": len(entities),
        "type_counts": type_counts,
        "samples": samples,
        "parse_errors": parse_errors,
    })


# ---------------------------------------------------------------------------
# validate_schema
# ---------------------------------------------------------------------------


# Required + recommended fields per Google Search Central (2026). Recommended
# fields are surfaced as suggestions, not errors; only missing required fields
# flip a per-entity verdict to "fail".
_RULES: dict[str, dict[str, list[str]]] = {
    "Article": {
        "required": ["headline"],
        "recommended": ["author", "datePublished", "dateModified", "image", "publisher"],
    },
    "NewsArticle": {
        "required": ["headline"],
        "recommended": ["author", "datePublished", "dateModified", "image", "publisher"],
    },
    "BlogPosting": {
        "required": ["headline"],
        "recommended": ["author", "datePublished", "dateModified", "image", "publisher"],
    },
    "Product": {
        "required": ["name"],
        "recommended": ["image", "description", "offers", "review", "aggregateRating", "brand"],
    },
    "FAQPage": {
        "required": ["mainEntity"],
        "recommended": [],
    },
    "BreadcrumbList": {
        "required": ["itemListElement"],
        "recommended": [],
    },
    "Organization": {
        "required": ["name"],
        "recommended": ["url", "logo", "sameAs", "contactPoint"],
    },
    "LocalBusiness": {
        "required": ["name", "address"],
        "recommended": ["telephone", "openingHours", "geo", "priceRange", "url"],
    },
    "Event": {
        "required": ["name", "startDate", "location"],
        "recommended": ["endDate", "organizer", "offers", "image", "performer"],
    },
    "Review": {
        "required": ["reviewRating", "itemReviewed"],
        "recommended": ["author", "datePublished", "reviewBody"],
    },
    "Recipe": {
        "required": ["name", "recipeIngredient", "recipeInstructions"],
        "recommended": ["author", "datePublished", "image", "nutrition", "totalTime"],
    },
}


TOOL_VALIDATE = {
    "name": "validate_schema",
    "description": (
        "Validate every JSON-LD block on a page against the Google Rich "
        "Results required-field set. Per-entity verdict (pass/fail) and a "
        "list of missing required + recommended fields. Covers Article, "
        "NewsArticle, BlogPosting, Product, FAQPage, BreadcrumbList, "
        "Organization, LocalBusiness, Event, Review, Recipe."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL to validate."},
            "types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: restrict checks to these schema.org @types. Default: validate every recognized type.",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def validate_schema(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_http(clients)
    if error:
        return error
    url = arguments.get("url")
    if not url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url is required.", docs_url=DOCS_BASE + "technical")
    type_filter = arguments.get("types")
    type_set = set(type_filter) if type_filter else None
    try:
        resp = _fetch_html(client, url)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    entities = _extract_jsonld(resp.body_text)
    findings: list[dict[str, Any]] = []
    summary = {"pass": 0, "fail": 0, "unknown_type": 0, "parse_error": 0}
    for idx, entity in enumerate(entities):
        if isinstance(entity, dict) and entity.get("_parse_error"):
            summary["parse_error"] += 1
            findings.append({"index": idx, "verdict": "parse_error", "raw_excerpt": entity.get("_raw")})
            continue
        types = _types_of(entity)
        recognized = [t for t in types if t in _RULES]
        if type_set is not None:
            recognized = [t for t in recognized if t in type_set]
        if not recognized:
            summary["unknown_type"] += 1
            findings.append({"index": idx, "types": types, "verdict": "unknown_type"})
            continue
        for t in recognized:
            rules = _RULES[t]
            missing_req = [f for f in rules["required"] if not _has_field(entity, f)]
            missing_rec = [f for f in rules["recommended"] if not _has_field(entity, f)]
            verdict = "pass" if not missing_req else "fail"
            summary[verdict] += 1
            findings.append({
                "index": idx,
                "type": t,
                "verdict": verdict,
                "missing_required": missing_req,
                "missing_recommended": missing_rec,
            })
    return ok({
        "url": url,
        "final_url": resp.final_url,
        "block_count": len(entities),
        "summary": summary,
        "findings": findings,
    })


def _has_field(entity: dict[str, Any], field: str) -> bool:
    """Truthy presence check that tolerates schema.org's loose typing.

    schema.org allows the same field to be a string, an object, or an array.
    A missing field is None; an empty string or empty list counts as missing
    because Google's parser rejects those for Rich Results eligibility.
    """
    value = entity.get(field)
    if value is None:
        return False
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return False
    return True


TOOLS = [TOOL_INSPECT, TOOL_VALIDATE]
HANDLERS = {"inspect_schema": inspect_schema, "validate_schema": validate_schema}
