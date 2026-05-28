"""robots_txt_validate (1). Fetches /robots.txt for a host, parses the
user-agent groups, surfaces the per-group rule listing for the diagnostic,
and optionally verdicts a list of (user_agent, path) probes using the
longest-match semantics of RFC 9309 (which is what Google, Bing, and Yandex
follow in practice).

We do the parse ourselves and run the verdict ourselves. The stdlib
``urllib.robotparser`` uses first-match semantics, which diverges from
RFC 9309 (and from what Google's crawler actually does): for the rule pair
``Disallow: /admin/`` + ``Allow: /admin/public``, RFC 9309 says
``/admin/public/page`` is allowed because the longer pattern wins, but the
stdlib says disallowed because Disallow was declared first. Our verdict
implements the proper longest-match-wins with Allow-breaks-ties rule.
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
    "name": "robots_txt_validate",
    "description": (
        "Fetch /robots.txt for a site, parse user-agent groups + "
        "Allow/Disallow + Crawl-delay + Sitemap, and return the structured "
        "ruleset. Optionally probe a list of (user_agent, url) pairs and "
        "return per-probe allow/deny verdicts using RFC 9309 longest-match."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "site_url": {
                "type": "string",
                "description": "Any URL on the target host; /robots.txt is fetched relative to it.",
            },
            "probes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "user_agent": {"type": "string"},
                        "url": {"type": "string"},
                    },
                    "required": ["user_agent", "url"],
                    "additionalProperties": False,
                },
                "description": "Optional list of (user_agent, url) pairs to verdict against the rules.",
            },
        },
        "required": ["site_url"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def robots_txt_validate(arguments, config, clients) -> dict[str, Any]:
    client, error = _require_http(clients)
    if error:
        return error
    site_url = arguments.get("site_url")
    if not site_url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "site_url is required.", docs_url=DOCS_BASE + "technical")
    parsed = urlparse(site_url)
    if not parsed.scheme or not parsed.netloc:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, f"site_url must be absolute, got {site_url!r}.")
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = client.fetch(robots_url)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    if resp.status == 404:
        return ok({
            "robots_url": robots_url,
            "status": 404,
            "verdict": "no_robots_txt",
            "groups": [],
            "sitemaps": [],
            "probes": [],
            "findings": ["no_robots_txt"],
        })
    if not (200 <= resp.status < 300):
        return err(
            ErrorCode.UPSTREAM_ERROR,
            _SERVICE,
            f"robots.txt fetch returned HTTP {resp.status}.",
            details={"status": resp.status, "robots_url": robots_url},
        )
    text = resp.body_text
    groups, sitemaps = _parse_robots(text)
    findings: list[str] = []
    if not groups:
        findings.append("empty_ruleset")
    probes_out: list[dict[str, Any]] = []
    for probe in arguments.get("probes") or []:
        ua = str(probe["user_agent"])
        target = str(probe["url"])
        verdict, matched = _verdict(groups, ua, target)
        probes_out.append({
            "user_agent": ua,
            "url": target,
            "allowed": verdict,
            "matched_rule": matched,
        })
    return ok({
        "robots_url": robots_url,
        "status": resp.status,
        "groups": groups,
        "sitemaps": sitemaps,
        "probes": probes_out,
        "findings": findings,
    })


def _parse_robots(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (groups, sitemaps). A group is a contiguous block sharing one
    or more ``User-agent:`` declarations, followed by Allow/Disallow lines and
    optional ``Crawl-delay``. ``Sitemap:`` lines are collected globally because
    robots.txt allows them anywhere."""
    groups: list[dict[str, Any]] = []
    sitemaps: list[str] = []
    current_uas: list[str] = []
    current_rules: list[dict[str, str]] = []
    current_crawl_delay: int | None = None
    in_block = False

    def flush() -> None:
        nonlocal current_uas, current_rules, current_crawl_delay, in_block
        if current_uas:
            groups.append({
                "user_agents": current_uas,
                "rules": current_rules,
                "crawl_delay": current_crawl_delay,
            })
        current_uas = []
        current_rules = []
        current_crawl_delay = None
        in_block = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            continue
        directive, _, value = line.partition(":")
        directive = directive.strip().lower()
        value = value.strip()
        if directive == "sitemap":
            sitemaps.append(value)
            continue
        if directive == "user-agent":
            if in_block:
                # End of previous group when we hit a UA after rules.
                flush()
            current_uas.append(value)
            continue
        if directive in ("allow", "disallow"):
            in_block = True
            current_rules.append({"type": directive, "path": value})
            continue
        if directive == "crawl-delay":
            in_block = True
            try:
                current_crawl_delay = int(float(value))
            except ValueError:
                pass
    flush()
    return groups, sitemaps


def _verdict(groups: list[dict[str, Any]], user_agent: str, url: str) -> tuple[bool, dict[str, str] | None]:
    """RFC 9309 verdict. Returns ``(allowed, matched_rule_or_None)``.

    Algorithm:
      1. Pick the most specific group whose user_agents include a prefix of
         our agent (case-insensitive). If none match, use the ``*`` group.
      2. Among that group's rules, find every pattern that matches our path.
         Pick the longest pattern. Ties: ``Allow`` beats ``Disallow`` (Google's
         documented behaviour).
      3. If no rule matches: allowed.
    """
    path = urlparse(url).path or "/"
    if urlparse(url).query:
        path = f"{path}?{urlparse(url).query}"
    group = _pick_group(groups, user_agent)
    if group is None:
        return True, None
    best: tuple[int, str, dict[str, str]] | None = None  # (pattern_len, type_priority, rule)
    for rule in group["rules"]:
        pattern = rule["path"]
        if not pattern:
            continue
        if not _pattern_matches(pattern, path):
            continue
        priority = 1 if rule["type"] == "allow" else 0  # allow > disallow on tie
        key = (len(pattern), priority)
        if best is None or key > (best[0], best[1]):
            best = (len(pattern), priority, rule)
    if best is None:
        return True, None
    rule = best[2]
    return rule["type"] == "allow", rule


def _pick_group(groups: list[dict[str, Any]], user_agent: str) -> dict[str, Any] | None:
    """Return the most-specific UA-matching group, else the ``*`` group, else None."""
    ua_lower = user_agent.lower()
    specific: tuple[int, dict[str, Any]] | None = None
    star: dict[str, Any] | None = None
    for g in groups:
        for ua in g["user_agents"]:
            ua_clean = ua.strip().lower()
            if ua_clean == "*":
                star = g
            elif ua_clean and ua_lower.startswith(ua_clean):
                if specific is None or len(ua_clean) > specific[0]:
                    specific = (len(ua_clean), g)
    if specific is not None:
        return specific[1]
    return star


def _pattern_matches(pattern: str, path: str) -> bool:
    """Match a robots.txt pattern against a URL path.

    Supports ``*`` (any sequence, including empty) and a trailing ``$`` (end
    anchor). Plain patterns are prefix matches.
    """
    end_anchored = pattern.endswith("$")
    if end_anchored:
        pattern = pattern[:-1]
    if "*" not in pattern:
        if end_anchored:
            return path == pattern
        return path.startswith(pattern)
    # Glob with *: walk the parts left to right.
    parts = pattern.split("*")
    pos = 0
    # First part must match at the start (prefix).
    if not path.startswith(parts[0]):
        return False
    pos = len(parts[0])
    for piece in parts[1:-1]:
        idx = path.find(piece, pos)
        if idx == -1:
            return False
        pos = idx + len(piece)
    last = parts[-1]
    if end_anchored:
        return path.endswith(last) and len(path) - pos >= len(last)
    return last == "" or path.find(last, pos) != -1


TOOLS = [TOOL]
HANDLERS = {"robots_txt_validate": robots_txt_validate}
