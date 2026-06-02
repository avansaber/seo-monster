"""Cloudflare tools (12).

Reads: cf_list_zones, cf_zone_info, cf_list_dns, cf_web_analytics,
cf_settings_audit, cf_list_redirects (read-only).
Writes (gated): cf_purge_cache, cf_purge_cache_all, cf_create_redirect,
cf_delete_redirect, cf_bulk_redirect_upsert, and cf_settings_update sit behind
SEO_MCP_ALLOW_DESTRUCTIVE; the all-purge, bulk upsert, and high-risk settings
changes (ssl_mode / HSTS-raise) additionally require a confirm token (HSTS-raise
also needs acknowledge_hsts_risk). The destructive gate is checked before any
client call, so a blocked write makes zero network requests. Single-redirect
creates pre-flight the target and refuse loops/duplicates; bulk upsert
pre-validates every item locally and rejects the whole batch on any bad item;
cf_settings_update validates locally, classifies HSTS-raise risk, and re-runs
the audit so the caller sees the finding clear. cf_settings_audit findings carry
a machine-readable `fix` hint to chain audit -> cf_settings_update.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..clients.errors import ApiError
from ..config import Config
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations, preflight_get, require_client


_SERVICE = "cf"
_REMEDIATION = (
    "Set CF_API_TOKEN to a Cloudflare API token (Zone:Read; add DNS:Read, "
    "Account Analytics:Read, Cache Purge:Purge, Single Redirect:Edit, and "
    "(for cf_bulk_redirect_upsert) Account Rulesets:Edit + Account Filter "
    "Lists:Edit as needed). See README > Auth."
)


def _require(clients: Mapping[str, Any]):
    return require_client(clients, "cf", _SERVICE, remediation=_REMEDIATION)


def _resolve_zone_name(arguments: Mapping[str, Any], config: Config) -> str | None:
    return arguments.get("zone") or config.cf_zone


def _missing_zone_error() -> dict[str, Any]:
    return err(
        ErrorCode.INVALID_INPUT,
        _SERVICE,
        "No Cloudflare zone specified.",
        remediation="Pass zone (the hostname, e.g. 'example.com') or set CF_ZONE.",
        docs_url=DOCS_BASE + "configuration",
    )


def _destructive_disabled(tool: str) -> dict[str, Any]:
    return err(
        ErrorCode.DESTRUCTIVE_DISABLED,
        _SERVICE,
        f"{tool} is disabled because destructive mode is off.",
        remediation="Set SEO_MCP_ALLOW_DESTRUCTIVE=true to enable the gated Cloudflare write tools (cache purge, redirect management, settings update).",
        docs_url=DOCS_BASE + "destructive-mode",
    )


# --- cf_list_zones --------------------------------------------------------

TOOL_LIST_ZONES = {
    "name": "cf_list_zones",
    "description": "List the Cloudflare zones the API token can see, with status, plan, and id.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": annotations(read=True),
}


def cf_list_zones(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    try:
        zones = client.list_zones()
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    shaped = [
        {"name": z.get("name"), "status": z.get("status"), "plan": z.get("plan", {}).get("name"), "id": z.get("id")}
        for z in zones
    ]
    return ok({"count": len(shaped), "zones": shaped})


# --- cf_zone_info ---------------------------------------------------------

TOOL_ZONE_INFO = {
    "name": "cf_zone_info",
    "description": "Zone overview for a hostname: status, plan, paused, name servers, created/modified.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "zone": {"type": "string", "description": "Zone hostname, e.g. 'example.com'. Defaults to the configured CF_ZONE."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def cf_zone_info(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    zone = _resolve_zone_name(arguments, config)
    if not zone:
        return _missing_zone_error()
    try:
        z = client.zone_info(zone)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok(
        {
            "name": z.get("name"),
            "id": z.get("id"),
            "status": z.get("status"),
            "plan": z.get("plan", {}).get("name"),
            "paused": z.get("paused"),
            "name_servers": z.get("name_servers", []),
            "created_on": z.get("created_on"),
            "modified_on": z.get("modified_on"),
        }
    )


# --- cf_list_dns ----------------------------------------------------------

TOOL_LIST_DNS = {
    "name": "cf_list_dns",
    "description": (
        "List DNS records for a zone (read-only). Useful for verifying the "
        "canonical host, CNAME flattening, and TXT verification records during "
        "SEO migrations."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "zone": {"type": "string", "description": "Zone hostname. Defaults to the configured CF_ZONE."},
            "type": {"type": "string", "description": "Optional record type filter, e.g. 'CNAME', 'TXT', 'A'."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def cf_list_dns(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    zone = _resolve_zone_name(arguments, config)
    if not zone:
        return _missing_zone_error()
    try:
        zone_id, zone_name = client.resolve_zone_id(zone)
        records = client.list_dns(zone_id, record_type=arguments.get("type"))
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    shaped = [
        {
            "type": r.get("type"),
            "name": r.get("name"),
            "content": r.get("content"),
            "ttl": r.get("ttl"),
            "proxied": r.get("proxied"),
            "id": r.get("id"),
        }
        for r in records
    ]
    return ok({"zone": zone_name, "count": len(shaped), "records": shaped})


# --- cf_web_analytics -----------------------------------------------------

TOOL_WEB_ANALYTICS = {
    "name": "cf_web_analytics",
    "description": (
        "Read-only Cloudflare Web Analytics (edge RUM). With no argument, lists "
        "the account's Web Analytics sites; with host_or_tag, returns one site's "
        "detail. Edge-measured traffic complements GA4 (it sees visits GA4's JS "
        "tag may miss). Create/delete are intentionally not exposed."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "host_or_tag": {
                "type": "string",
                "description": "Optional. A hostname (contains a dot) resolves to its site; otherwise treated as a site_tag. Omit to list all sites.",
            },
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def _shape_rum_site(site: dict[str, Any]) -> dict[str, Any]:
    return {
        "host": site.get("host"),
        "site_tag": site.get("site_tag"),
        "auto_install": site.get("auto_install"),
        "enabled": site.get("enabled"),
        "created": site.get("created"),
        "ruleset_id": site.get("ruleset_id"),
    }


def cf_web_analytics(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    host_or_tag = arguments.get("host_or_tag")
    try:
        if not host_or_tag:
            sites = client.web_analytics_list()
            return ok(
                {
                    "account_id": client.account_id,
                    "count": len(sites),
                    "sites": [_shape_rum_site(s) for s in sites],
                }
            )
        if "." in host_or_tag:
            sites = client.web_analytics_list()
            match = next((s for s in sites if s.get("host") == host_or_tag), None)
            if not match:
                return err(
                    ErrorCode.NOT_FOUND,
                    _SERVICE,
                    f"No Web Analytics site found for host '{host_or_tag}'.",
                    remediation="Call cf_web_analytics with no argument to list all sites.",
                )
            site_tag = match.get("site_tag")
        else:
            site_tag = host_or_tag
        site = client.web_analytics_get(site_tag)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok({"account_id": client.account_id, "site": _shape_rum_site(site)})


# --- cf_purge_cache (gated) -----------------------------------------------

TOOL_PURGE_CACHE = {
    "name": "cf_purge_cache",
    "description": (
        "Purge specific URLs from the Cloudflare cache so crawlers refetch "
        "updated content. Gated: requires SEO_MCP_ALLOW_DESTRUCTIVE=true."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "zone": {"type": "string", "description": "Zone hostname. Defaults to the configured CF_ZONE."},
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Absolute URLs to purge.",
            },
        },
        "required": ["urls"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=False, destructive=True),
}


def cf_purge_cache(arguments, config, clients) -> dict[str, Any]:
    # Gate first: a blocked purge must make zero client calls.
    if not config.allow_destructive:
        return _destructive_disabled("cf_purge_cache")
    client, error = _require(clients)
    if error:
        return error
    urls = arguments.get("urls") or []
    if not urls:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "urls must be a non-empty list.", docs_url=DOCS_BASE + "cf")
    zone = _resolve_zone_name(arguments, config)
    if not zone:
        return _missing_zone_error()
    try:
        zone_id, zone_name = client.resolve_zone_id(zone)
        client.purge_files(zone_id, urls)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok({"zone": zone_name, "purged_count": len(urls), "urls": urls})


# --- cf_purge_cache_all (gated + confirm) ---------------------------------

TOOL_PURGE_CACHE_ALL = {
    "name": "cf_purge_cache_all",
    "description": (
        "Purge the entire Cloudflare cache for a zone (affects all visitors). "
        "Gated: requires SEO_MCP_ALLOW_DESTRUCTIVE=true AND a confirm value equal "
        "to the resolved zone hostname, so a full purge cannot fire ambiguously."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "zone": {"type": "string", "description": "Zone hostname. Defaults to the configured CF_ZONE."},
            "confirm": {"type": "string", "description": "Must equal the resolved zone hostname to proceed."},
        },
        "required": ["confirm"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=False, destructive=True),
}


def cf_purge_cache_all(arguments, config, clients) -> dict[str, Any]:
    # Gate first: a blocked purge must make zero client calls.
    if not config.allow_destructive:
        return _destructive_disabled("cf_purge_cache_all")
    client, error = _require(clients)
    if error:
        return error
    zone = _resolve_zone_name(arguments, config)
    if not zone:
        return _missing_zone_error()
    confirm = arguments.get("confirm")
    try:
        zone_id, zone_name = client.resolve_zone_id(zone)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    if confirm != zone_name:
        return err(
            ErrorCode.CONFIRM_REQUIRED,
            _SERVICE,
            "Full-zone purge not confirmed.",
            remediation=f"Pass confirm='{zone_name}' (the resolved zone hostname) to purge everything.",
            docs_url=DOCS_BASE + "destructive-mode",
            details={"resolved_zone": zone_name},
        )
    try:
        client.purge_all(zone_id)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok({"zone": zone_name, "purged": True})


# --- cf_settings_audit (read-only; RULESETS section 3) --------------------
#
# CF cannot see the origin, so the security-relevant checks (SSL mode, Always
# Use HTTPS) are phrased as "verify," not "fail." HSTS is graded medium and is
# NEVER hard-failed: premature HSTS during or just after a migration is
# dangerous and hard to undo. The rules are a pure function over the settings
# map so they test without any client.

# HSTS is intentionally absent here: it can never reach critical (RULESETS section 3).
_SETTINGS_HSTS_MAX_SEVERITY = "medium"
_HSTS_MIN_MAX_AGE = 60 * 60 * 24 * 180  # 6 months, in seconds


def _cf_finding(rule_id: str, severity: str, observed: str, expected: str, why: str, benign: str, fix: dict[str, Any] | None = None) -> dict[str, Any]:
    finding = {
        "rule_id": rule_id,
        "severity": severity,
        "observed": observed,
        "expected": expected,
        "why": why,
        "benign_exception": benign,
    }
    # Machine-readable fix hint: the exact cf_settings_update `settings` key +
    # recommended value, so a host can chain audit -> cf_settings_update.
    if fix is not None:
        finding["fix"] = fix
    return finding


def _settings_map(settings: list[dict[str, Any]]) -> dict[str, Any]:
    """Index the CF settings list by ``id`` -> ``value``."""
    out: dict[str, Any] = {}
    for entry in settings:
        if isinstance(entry, dict) and entry.get("id") is not None:
            out[entry["id"]] = entry.get("value")
    return out


def _audit_settings(by_id: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Apply RULESETS section 3 to the indexed settings map. Pure: no client, no
    I/O. Every non-info finding carries why + benign_exception. CF cannot see the
    origin, so several checks are graded as 'verify' rather than a hard failure,
    and HSTS is never raised above medium."""
    findings: list[dict[str, Any]] = []

    # cf.ssl_mode: want "strict" (Full Strict). "off"/"flexible"/"full" are
    # weaker, but CF cannot see origin TLS, so this is "verify," not "fail."
    ssl_mode = by_id.get("ssl")
    if ssl_mode is not None and ssl_mode != "strict":
        findings.append(_cf_finding(
            "cf.ssl_mode", "high",
            f"ssl={ssl_mode}", "strict (Full (Strict))",
            "Verify: Flexible/Full SSL lets CF reach the origin over weak or unauthenticated TLS, "
            "risking redirect loops, mixed content, and insecure origin hops.",
            "Legacy origin that genuinely cannot do TLS; CF cannot see origin TLS, so confirm before changing.",
            fix={"setting": "ssl_mode", "recommended": "strict"},
        ))

    # cf.always_https: want "on". CF cannot see whether http->https is handled
    # at the origin or another layer, so "verify."
    always_https = by_id.get("always_use_https")
    if always_https is not None and always_https != "on":
        findings.append(_cf_finding(
            "cf.always_https", "high",
            f"always_use_https={always_https}", "on",
            "Verify: without an edge http->https 301, the http variant may be crawled and indexed, "
            "splitting canonical signals.",
            "http->https is already enforced at the origin or another layer; confirm before changing.",
            fix={"setting": "always_use_https", "recommended": "on"},
        ))

    # cf.hsts: present with max-age >= 6 months. NEVER hard-fail; premature HSTS
    # is dangerous and hard to undo. Capped at medium.
    sh = by_id.get("security_header")
    hsts = sh.get("strict_transport_security") if isinstance(sh, dict) else None
    hsts_enabled = bool(hsts.get("enabled")) if isinstance(hsts, dict) else False
    hsts_max_age = hsts.get("max_age") if isinstance(hsts, dict) else None
    if not hsts_enabled:
        findings.append(_cf_finding(
            "cf.hsts", _SETTINGS_HSTS_MAX_SEVERITY,
            "HSTS off", f"HSTS on with max-age >= {_HSTS_MIN_MAX_AGE}s (6 months)",
            "HSTS enforces HTTPS and blocks protocol downgrade. Recommended, but with a strong caveat.",
            "Premature HSTS is dangerous and hard to undo; do NOT enable during or just after an HTTPS "
            "migration until HTTPS is fully stable. Never a hard failure.",
            fix={"setting": "hsts", "recommended": {"enabled": True, "max_age": 31536000, "include_subdomains": True}},
        ))
    elif isinstance(hsts_max_age, int) and hsts_max_age < _HSTS_MIN_MAX_AGE:
        findings.append(_cf_finding(
            "cf.hsts", _SETTINGS_HSTS_MAX_SEVERITY,
            f"HSTS on, max-age={hsts_max_age}s", f"max-age >= {_HSTS_MIN_MAX_AGE}s (6 months)",
            "A short HSTS max-age weakens the downgrade protection HSTS is meant to provide.",
            "Intentionally short while ramping up HSTS confidence after a migration; raise it gradually.",
            fix={"setting": "hsts", "recommended": {"enabled": True, "max_age": 31536000, "include_subdomains": True}},
        ))

    # cf.auto_https_rewrites: want "on". Low-medium; pure mixed-content hygiene.
    auto_rewrites = by_id.get("automatic_https_rewrites")
    if auto_rewrites is not None and auto_rewrites != "on":
        findings.append(_cf_finding(
            "cf.auto_https_rewrites", "low",
            f"automatic_https_rewrites={auto_rewrites}", "on",
            "Rewriting http sub-resource links to https avoids mixed-content warnings.",
            "All content is already served over https, so there is nothing to rewrite (low value).",
            fix={"setting": "automatic_https_rewrites", "recommended": "on"},
        ))

    # cf.brotli: want "on". Pure upside; low.
    brotli = by_id.get("brotli")
    if brotli is not None and brotli != "on":
        findings.append(_cf_finding(
            "cf.brotli", "low",
            f"brotli={brotli}", "on",
            "Brotli compresses better than gzip, improving load time and Core Web Vitals.",
            "Pure upside; no real downside to leaving it off other than missed performance.",
            fix={"setting": "brotli", "recommended": "on"},
        ))

    # cf.browser_cache_ttl: informational only. "Respect existing headers"
    # (value 0, origin-controlled) is a legitimate, often preferred choice.
    bct = by_id.get("browser_cache_ttl")
    if bct == 0:
        findings.append(_cf_finding(
            "cf.browser_cache_ttl", "info",
            "browser_cache_ttl=0 (respect existing headers)", "a sane browser cache TTL",
            "Browser caching helps performance, but respecting origin Cache-Control headers is a valid strategy.",
            "Origin sends its own Cache-Control headers, so 'respect existing headers' is intentional, not a fault.",
        ))

    return findings


TOOL_SETTINGS_AUDIT = {
    "name": "cf_settings_audit",
    "description": (
        "Audit a Cloudflare zone's settings for SEO and crawl/index safety "
        "(read-only): SSL/TLS mode, Always Use HTTPS, HSTS, Automatic HTTPS "
        "Rewrites, Brotli, and browser cache TTL. Findings are severity-graded "
        "with the reason and a benign exception each. Cloudflare cannot see the "
        "origin, so the SSL and Always-HTTPS checks are phrased as 'verify,' not "
        "'fail,' and HSTS is never hard-failed (premature HSTS is dangerous and "
        "hard to undo). Answers 'are my edge settings silently sabotaging "
        "crawl/index?' It grades the edge layer only, not origin behavior."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "zone": {"type": "string", "description": "Zone hostname, e.g. 'example.com'. Defaults to the configured CF_ZONE."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def cf_settings_audit(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    zone = _resolve_zone_name(arguments, config)
    if not zone:
        return _missing_zone_error()
    try:
        zone_id, zone_name = client.resolve_zone_id(zone)
        settings = client.get_zone_settings(zone_id)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    by_id = _settings_map(settings)
    findings = _audit_settings(by_id)
    by_severity: dict[str, int] = {}
    for f in findings:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1
    if any(f["severity"] in ("critical", "high") for f in findings):
        verdict = "issues"
    elif findings:
        verdict = "review"
    else:
        verdict = "clean"

    snapshot_keys = (
        "ssl", "always_use_https", "security_header", "automatic_https_rewrites",
        "brotli", "browser_cache_ttl",
    )
    settings_snapshot = {k: by_id.get(k) for k in snapshot_keys if k in by_id}

    return ok(
        {
            "zone": zone_name,
            "settings_snapshot": settings_snapshot,
            "findings": findings,
            "summary": {"by_severity": by_severity, "verdict": verdict},
            "notes": [
                "Read-only: no settings are changed.",
                "Cloudflare cannot see the origin, so SSL and Always-HTTPS findings are 'verify,' not failures.",
                "HSTS is never hard-failed: premature HSTS is dangerous and hard to undo.",
            ],
        }
    )


# --- single / dynamic redirects (read ungated; create/delete gated) -------
#
# Single Redirects live in the zone's http_request_dynamic_redirect phase.
# cf_list_redirects is a safe read (call it before any write so nothing is
# clobbered). create/delete are gated behind SEO_MCP_ALLOW_DESTRUCTIVE exactly
# like purge. Validation reuses the v0.7.7 pre-flight (preflight_get) so we
# never point a redirect at a dead target, plus loop / duplicate / status
# checks and a dry_run preview. Bulk Redirects (account-level) come in 0.7.9.

_VALID_REDIRECT_STATUS = {301, 302, 307, 308}


def _valid_abs_url(u: Any) -> bool:
    return isinstance(u, str) and u.startswith(("http://", "https://"))


def _redirect_source_of(rule: Mapping[str, Any]) -> str | None:
    """Extract the source URL from a rule's expression for dedupe + listing."""
    m = re.search(r'full_uri eq "([^"]+)"', rule.get("expression") or "")
    return m.group(1) if m else None


def _shape_redirect_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    fv = (rule.get("action_parameters") or {}).get("from_value") or {}
    tgt = fv.get("target_url") or {}
    return {
        "id": rule.get("id"),
        "source": _redirect_source_of(rule),
        "target": tgt.get("value") or tgt.get("expression"),
        "status_code": fv.get("status_code"),
        "preserve_query_string": fv.get("preserve_query_string"),
        "description": rule.get("description"),
    }


def _build_redirect_rule(source: str, target: str, status_code: int, preserve_qs: bool, description: str | None) -> dict[str, Any]:
    return {
        "expression": f'(http.request.full_uri eq "{source}")',
        "description": description or f"SEOMonster redirect: {source} -> {target}",
        "action": "redirect",
        "action_parameters": {
            "from_value": {
                "target_url": {"value": target},
                "status_code": status_code,
                "preserve_query_string": preserve_qs,
            }
        },
    }


TOOL_LIST_REDIRECTS = {
    "name": "cf_list_redirects",
    "description": (
        "List a zone's single (dynamic) redirect rules (source, target, status "
        "code, rule id) plus the account's Bulk Redirect lists (read-only). Call "
        "this before cf_create_redirect / cf_delete_redirect / "
        "cf_bulk_redirect_upsert so writes never clobber existing rules. Pairs "
        "with redirect_chain_audit and the migration_check prompt."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "zone": {"type": "string", "description": "Zone hostname. Defaults to the configured CF_ZONE."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def cf_list_redirects(arguments, config, clients) -> dict[str, Any]:
    client, error = _require(clients)
    if error:
        return error
    zone = _resolve_zone_name(arguments, config)
    if not zone:
        return _missing_zone_error()
    try:
        zone_id, zone_name = client.resolve_zone_id(zone)
        ruleset_id, rules = client.get_dynamic_redirects(zone_id)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    shaped = [_shape_redirect_rule(r) for r in rules]
    # Bulk Redirect lists are account-level; best-effort (needs account access).
    bulk_lists: list[dict[str, Any]] | None
    try:
        account_id = client.get_account_id()
        bulk_lists = [
            {"name": item.get("name"), "id": item.get("id"), "num_items": item.get("num_items"), "description": item.get("description")}
            for item in client.list_redirect_lists(account_id)
        ]
    except ApiError:
        bulk_lists = None  # account-level access not available to this token
    return ok(
        {
            "zone": zone_name,
            "ruleset_id": ruleset_id,
            "count": len(shaped),
            "single_redirects": shaped,
            "bulk_redirect_lists": bulk_lists,
            "notes": [
                "single_redirects are zone-level (http_request_dynamic_redirect).",
                "bulk_redirect_lists are account-level; null means the token lacks account access.",
            ],
        }
    )


TOOL_CREATE_REDIRECT = {
    "name": "cf_create_redirect",
    "description": (
        "Create one single (dynamic) redirect at the Cloudflare edge (e.g. a 301 "
        "for a renamed/migrated URL). Gated: requires SEO_MCP_ALLOW_DESTRUCTIVE=true. "
        "Validates the target is reachable (no redirecting to a dead URL), refuses "
        "loops and duplicates, and supports dry_run to preview the rule first."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Absolute source URL to redirect FROM (exact full-URI match)."},
            "target": {"type": "string", "description": "Absolute target URL to redirect TO."},
            "status_code": {"type": "integer", "description": "301 (default, permanent), 302/307 (temporary), or 308.", "enum": [301, 302, 307, 308]},
            "preserve_query_string": {"type": "boolean", "description": "Carry the original query string to the target (default true)."},
            "zone": {"type": "string", "description": "Zone hostname. Defaults to the configured CF_ZONE."},
            "dry_run": {"type": "boolean", "description": "Preview the rule that would be created without writing it (default false)."},
            "skip_preflight": {"type": "boolean", "description": "Bypass the target-reachability pre-flight (default false)."},
        },
        "required": ["source", "target"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=False, destructive=True),
}


def cf_create_redirect(arguments, config, clients) -> dict[str, Any]:
    # Gate first: a blocked write must make zero client calls.
    if not config.allow_destructive:
        return _destructive_disabled("cf_create_redirect")
    client, error = _require(clients)
    if error:
        return error
    source = arguments.get("source")
    target = arguments.get("target")
    if not _valid_abs_url(source) or not _valid_abs_url(target):
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "source and target must be absolute http(s) URLs.", docs_url=DOCS_BASE + "cf")
    status_code = int(arguments.get("status_code", 301))
    if status_code not in _VALID_REDIRECT_STATUS:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, f"status_code must be one of {sorted(_VALID_REDIRECT_STATUS)}.", docs_url=DOCS_BASE + "cf")
    if source == target:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "source and target are identical (redirect loop).", docs_url=DOCS_BASE + "cf")
    zone = _resolve_zone_name(arguments, config)
    if not zone:
        return _missing_zone_error()
    advisories: list[str] = []
    if status_code == 302:
        advisories.append("302 is a temporary redirect; use 301 for a permanent move so search engines pass authority to the target.")
    try:
        zone_id, zone_name = client.resolve_zone_id(zone)
        _ruleset_id, existing = client.get_dynamic_redirects(zone_id)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    for r in existing:
        if _redirect_source_of(r) == source:
            return err(
                ErrorCode.INVALID_INPUT,
                _SERVICE,
                f"A redirect for source {source!r} already exists (rule id {r.get('id')}). "
                "Delete it first with cf_delete_redirect, or change the source.",
                remediation="Use cf_list_redirects to review existing rules.",
                details={"existing_rule_id": r.get("id")},
            )
    if not arguments.get("skip_preflight"):
        pf = preflight_get(clients, target)
        if pf is not None:
            st, _body, reason = pf
            if st is None or not (200 <= st < 300):
                detail = f"returned HTTP {st}" if st is not None else f"was unreachable ({reason})"
                return err(
                    ErrorCode.INVALID_INPUT,
                    _SERVICE,
                    f"The redirect target {detail}. Redirecting visitors and crawlers to a dead URL is "
                    "worse than no redirect, so the redirect was not created.",
                    remediation="Fix the target so it returns HTTP 200, or pass skip_preflight=true to override.",
                    docs_url=DOCS_BASE + "cf",
                    details={"target": target, "preflight_status": st},
                )
    rule = _build_redirect_rule(source, target, status_code, bool(arguments.get("preserve_query_string", True)), arguments.get("description"))
    if arguments.get("dry_run"):
        return ok({"zone": zone_name, "dry_run": True, "would_create": _shape_redirect_rule(rule), "advisories": advisories, "notes": ["dry_run: nothing was written."]})
    try:
        client.add_dynamic_redirect(zone_id, rule)
        # Re-read to surface the new rule's id so the caller can delete it
        # without a separate cf_list_redirects (FEEDBACK §22 A-FIND-3).
        _rsid, after = client.get_dynamic_redirects(zone_id)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    new_id = next((r.get("id") for r in after if _redirect_source_of(r) == source), None)
    return ok({"zone": zone_name, "created": True, "rule_id": new_id, "source": source, "target": target, "status_code": status_code, "advisories": advisories})


TOOL_DELETE_REDIRECT = {
    "name": "cf_delete_redirect",
    "description": (
        "Delete one single (dynamic) redirect rule by its id (the rollback path "
        "for cf_create_redirect). Gated: requires SEO_MCP_ALLOW_DESTRUCTIVE=true. "
        "Get the rule id from cf_list_redirects."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "rule_id": {"type": "string", "description": "The redirect rule id (from cf_list_redirects)."},
            "zone": {"type": "string", "description": "Zone hostname. Defaults to the configured CF_ZONE."},
        },
        "required": ["rule_id"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=False, destructive=True),
}


def cf_delete_redirect(arguments, config, clients) -> dict[str, Any]:
    if not config.allow_destructive:
        return _destructive_disabled("cf_delete_redirect")
    client, error = _require(clients)
    if error:
        return error
    rule_id = arguments.get("rule_id")
    if not rule_id:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "rule_id is required (from cf_list_redirects).", docs_url=DOCS_BASE + "cf")
    zone = _resolve_zone_name(arguments, config)
    if not zone:
        return _missing_zone_error()
    try:
        zone_id, zone_name = client.resolve_zone_id(zone)
        ruleset_id, existing = client.get_dynamic_redirects(zone_id)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    if ruleset_id is None or not any(r.get("id") == rule_id for r in existing):
        return err(
            ErrorCode.NOT_FOUND,
            _SERVICE,
            f"No redirect rule {rule_id!r} found in zone {zone_name}.",
            remediation="List current rules with cf_list_redirects.",
        )
    try:
        client.delete_dynamic_redirect(zone_id, ruleset_id, rule_id)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok({"zone": zone_name, "deleted_rule_id": rule_id})


# --- bulk redirects (account-level; gated + confirm) ----------------------
#
# A Redirect List (source->target items, added asynchronously) referenced by an
# account ruleset in the http_request_redirect phase. High blast radius (account
# scope, many URLs), so: gated + a confirm token equal to list_name, plus full
# local pre-validation of every item before any write (reject the batch, do not
# half-apply).

_MAX_BULK_REDIRECTS = 1000


def _build_bulk_item(source: str, target: str, status_code: int, preserve_qs: Any) -> dict[str, Any]:
    redirect: dict[str, Any] = {"source_url": source, "target_url": target, "status_code": status_code}
    if preserve_qs is not None:
        redirect["preserve_query_string"] = bool(preserve_qs)
    return {"redirect": redirect}


TOOL_BULK_REDIRECT_UPSERT = {
    "name": "cf_bulk_redirect_upsert",
    "description": (
        "Create or append many redirects at once via an account-level Bulk "
        "Redirect List (for site migrations). Gated: requires "
        "SEO_MCP_ALLOW_DESTRUCTIVE=true AND a confirm value equal to list_name. "
        "Validates every item locally first and rejects the whole batch on any "
        "bad item (never half-applies); supports dry_run. Items are added "
        "asynchronously by Cloudflare."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "maxItems": _MAX_BULK_REDIRECTS,
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Source URL/host+path to redirect FROM."},
                        "target": {"type": "string", "description": "Absolute target URL to redirect TO."},
                        "status_code": {"type": "integer", "enum": [301, 302, 307, 308]},
                        "preserve_query_string": {"type": "boolean"},
                    },
                    "required": ["source", "target"],
                    "additionalProperties": False,
                },
                "description": f"Up to {_MAX_BULK_REDIRECTS} redirect mappings.",
            },
            "list_name": {"type": "string", "description": "Name of the Bulk Redirect List to create/append (also the confirm value). Letters, numbers, and underscores only (no hyphens), e.g. site_migration_2026."},
            "confirm": {"type": "string", "description": "Must equal list_name to proceed (the high-blast-radius gate)."},
            "description": {"type": "string", "description": "Optional list description."},
            "dry_run": {"type": "boolean", "description": "Validate + report what would be written, without writing (default false)."},
        },
        "required": ["items", "list_name"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=False, destructive=True),
}


def cf_bulk_redirect_upsert(arguments, config, clients) -> dict[str, Any]:
    if not config.allow_destructive:
        return _destructive_disabled("cf_bulk_redirect_upsert")
    client, error = _require(clients)
    if error:
        return error
    items = arguments.get("items") or []
    list_name = arguments.get("list_name")
    if not list_name:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "list_name is required.", docs_url=DOCS_BASE + "cf")
    # Cloudflare list names allow only [A-Za-z0-9_]; a hyphen returns a cryptic
    # CF 10029 invalid_name. Catch it up front with a clear message (FEEDBACK
    # §22 B-FIND-1).
    if not re.fullmatch(r"[A-Za-z0-9_]+", list_name):
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            f"list_name {list_name!r} is invalid: Cloudflare list names may contain only "
            "letters, numbers, and underscores (no hyphens or spaces). Example: site_migration_2026.",
            docs_url=DOCS_BASE + "cf",
        )
    if not items:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "items must be a non-empty list.", docs_url=DOCS_BASE + "cf")
    if len(items) > _MAX_BULK_REDIRECTS:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, f"Too many items ({len(items)}); max {_MAX_BULK_REDIRECTS} per call.", docs_url=DOCS_BASE + "cf")

    # Pre-validate ALL items locally before any write (never half-apply).
    rejects: list[dict[str, Any]] = []
    seen: set[str] = set()
    built: list[dict[str, Any]] = []
    for i, it in enumerate(items):
        src = it.get("source")
        tgt = it.get("target")
        if not src:
            rejects.append({"index": i, "reason": "source is required"})
            continue
        if not _valid_abs_url(tgt):
            rejects.append({"index": i, "reason": "target must be an absolute http(s) URL"})
            continue
        sc = int(it.get("status_code", 301))
        if sc not in _VALID_REDIRECT_STATUS:
            rejects.append({"index": i, "reason": f"status_code must be one of {sorted(_VALID_REDIRECT_STATUS)}"})
            continue
        if src == tgt:
            rejects.append({"index": i, "reason": "source == target (loop)"})
            continue
        if src in seen:
            rejects.append({"index": i, "reason": "duplicate source in batch"})
            continue
        seen.add(src)
        built.append(_build_bulk_item(src, tgt, sc, it.get("preserve_query_string")))
    if rejects:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            f"{len(rejects)} of {len(items)} items failed validation; nothing was written. Fix and retry.",
            remediation="Correct the listed items (absolute target URLs, valid status codes, no loops/dupes).",
            docs_url=DOCS_BASE + "cf",
            details={"reject_count": len(rejects), "rejects": rejects[:50]},
        )

    try:
        account_id = client.get_account_id()
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    if arguments.get("dry_run"):
        return ok({"dry_run": True, "list_name": list_name, "account_id": account_id, "would_upsert_count": len(built), "notes": ["dry_run: nothing was written."]})

    if arguments.get("confirm") != list_name:
        return err(
            ErrorCode.CONFIRM_REQUIRED,
            _SERVICE,
            "Bulk redirect upsert not confirmed.",
            remediation=f"Pass confirm='{list_name}' to write {len(built)} redirect(s) to that list.",
            docs_url=DOCS_BASE + "destructive-mode",
            details={"list_name": list_name, "item_count": len(built)},
        )

    try:
        existing = client.list_redirect_lists(account_id)
        match = next((lst for lst in existing if lst.get("name") == list_name), None)
        list_id = match.get("id") if match else client.create_redirect_list(account_id, list_name, arguments.get("description", "")).get("id")
        op = client.append_redirect_items(account_id, list_id, built)
        op_id = op.get("operation_id")
        op_status = client.get_bulk_operation(account_id, op_id).get("status") if op_id else None
        # Wire the account ruleset to reference the list (idempotent).
        _rsid, rules = client.get_account_redirect_ruleset(account_id)
        already = any(
            (((r.get("action_parameters") or {}).get("from_list") or {}).get("name") == list_name)
            for r in rules
        )
        if not already:
            client.add_account_redirect_rule(
                account_id,
                {
                    "expression": f"http.request.full_uri in ${list_name}",
                    "description": f"SEOMonster bulk redirects: {list_name}",
                    "action": "redirect",
                    "action_parameters": {"from_list": {"name": list_name, "key": "http.request.full_uri"}},
                },
            )
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)
    return ok(
        {
            "account_id": account_id,
            "list_name": list_name,
            "list_id": list_id,
            "upserted_count": len(built),
            "operation_id": op_id,
            "operation_status": op_status,
            "ruleset_wired": True,
            "notes": ["Items are added asynchronously; if operation_status is not 'completed', re-check shortly with cf_list_redirects."],
        }
    )


# --- zone settings update (gated; closes the audit -> remediate loop) -----
#
# Writes only the SEO/crawl/security settings cf_settings_audit grades. HSTS is
# the footgun: any change that RAISES protection needs confirm==zone AND
# acknowledge_hsts_risk; an ssl_mode change needs confirm==zone. Validates
# locally before any CF call (reject the whole request on bad input). dry_run
# returns before/after. Needs the Zone Settings:Edit token scope (audit=Read).

_SETTING_IDS = {
    "ssl_mode": "ssl",
    "always_use_https": "always_use_https",
    "automatic_https_rewrites": "automatic_https_rewrites",
    "brotli": "brotli",
    "browser_cache_ttl": "browser_cache_ttl",
}
_RULE_FOR_SETTING = {
    "ssl_mode": "cf.ssl_mode",
    "always_use_https": "cf.always_https",
    "automatic_https_rewrites": "cf.auto_https_rewrites",
    "brotli": "cf.brotli",
    "browser_cache_ttl": "cf.browser_cache_ttl",
    "hsts": "cf.hsts",
}
_SSL_MODES = {"off", "flexible", "full", "strict"}
_ONOFF = {"on", "off"}
# Distinct names from the audit's _HSTS_MIN_MAX_AGE (6-month grading threshold) —
# these bound what cf_settings_update will WRITE.
_HSTS_VALID_MIN = 300
_HSTS_VALID_MAX = 63072000        # 2 years
_HSTS_PRELOAD_MIN = 31536000      # 1 year (HSTS preload-list requirement)
_HSTS_WEAK_MAX_AGE = 3600


def _settings_snapshot(by_id: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("ssl", "always_use_https", "automatic_https_rewrites", "brotli", "browser_cache_ttl", "security_header")
    return {k: by_id.get(k) for k in keys if k in by_id}


def _current_hsts(by_id: Mapping[str, Any]) -> dict[str, Any]:
    sh = by_id.get("security_header")
    hsts = sh.get("strict_transport_security") if isinstance(sh, dict) else None
    return hsts if isinstance(hsts, dict) else {}


TOOL_SETTINGS_UPDATE = {
    "name": "cf_settings_update",
    "description": (
        "Write the SEO/crawl/security Cloudflare zone settings that "
        "cf_settings_audit grades, to close the audit -> remediate loop. Gated: "
        "requires SEO_MCP_ALLOW_DESTRUCTIVE=true. Changing ssl_mode, or any HSTS "
        "change that raises protection, additionally requires confirm=<zone> (HSTS "
        "also requires acknowledge_hsts_risk=true). Validates locally first and "
        "supports dry_run (before/after preview). Needs the Zone Settings:Edit "
        "token scope (the audit only needs Read)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "zone": {"type": "string", "description": "Zone hostname. Defaults to the configured CF_ZONE."},
            "settings": {
                "type": "object",
                "description": "Only the provided keys are changed.",
                "properties": {
                    "ssl_mode": {"type": "string", "enum": ["off", "flexible", "full", "strict"]},
                    "always_use_https": {"type": "string", "enum": ["on", "off"]},
                    "automatic_https_rewrites": {"type": "string", "enum": ["on", "off"]},
                    "brotli": {"type": "string", "enum": ["on", "off"]},
                    "browser_cache_ttl": {"type": "integer", "description": "Seconds (0 = respect origin headers)."},
                    "hsts": {
                        "type": "object",
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "max_age": {"type": "integer", "description": "Seconds; 0 disables. >= 31536000 (1y) recommended."},
                            "include_subdomains": {"type": "boolean"},
                            "preload": {"type": "boolean"},
                            "nosniff": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
            "confirm": {"type": "string", "description": "Must equal the resolved zone for ssl_mode or HSTS-raise changes."},
            "acknowledge_hsts_risk": {"type": "boolean", "description": "Required true for any HSTS change that raises protection (HSTS is hard to undo)."},
            "dry_run": {"type": "boolean", "description": "Preview before/after without writing (default false)."},
        },
        "required": ["settings"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=False, destructive=True),
}


def cf_settings_update(arguments, config, clients) -> dict[str, Any]:
    if not config.allow_destructive:
        return _destructive_disabled("cf_settings_update")
    client, error = _require(clients)
    if error:
        return error
    settings = arguments.get("settings")
    if not isinstance(settings, dict) or not settings:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "settings must be a non-empty object.", docs_url=DOCS_BASE + "cf")
    allowed = set(_SETTING_IDS) | {"hsts"}
    unknown = [k for k in settings if k not in allowed]
    if unknown:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, f"Unknown setting key(s): {unknown}. Allowed: {sorted(allowed)}.", docs_url=DOCS_BASE + "cf")
    zone = _resolve_zone_name(arguments, config)
    if not zone:
        return _missing_zone_error()

    advisories: list[str] = []

    # Local validation (reject the whole request on any bad input).
    if "ssl_mode" in settings and settings["ssl_mode"] not in _SSL_MODES:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, f"ssl_mode must be one of {sorted(_SSL_MODES)}.", docs_url=DOCS_BASE + "cf")
    for k in ("always_use_https", "automatic_https_rewrites", "brotli"):
        if k in settings and settings[k] not in _ONOFF:
            return err(ErrorCode.INVALID_INPUT, _SERVICE, f"{k} must be 'on' or 'off'.", docs_url=DOCS_BASE + "cf")
    if "browser_cache_ttl" in settings and (not isinstance(settings["browser_cache_ttl"], int) or settings["browser_cache_ttl"] < 0):
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "browser_cache_ttl must be a non-negative integer (seconds).", docs_url=DOCS_BASE + "cf")
    hsts_req = settings.get("hsts")
    if hsts_req is not None:
        if not isinstance(hsts_req, dict):
            return err(ErrorCode.INVALID_INPUT, _SERVICE, "hsts must be an object.", docs_url=DOCS_BASE + "cf")
        ma = hsts_req.get("max_age")
        if ma is not None:
            if not isinstance(ma, int) or ma < 0:
                return err(ErrorCode.INVALID_INPUT, _SERVICE, "hsts.max_age must be a non-negative integer (seconds).", docs_url=DOCS_BASE + "cf")
            if ma != 0 and not (_HSTS_VALID_MIN <= ma <= _HSTS_VALID_MAX):
                return err(ErrorCode.INVALID_INPUT, _SERVICE, f"hsts.max_age must be 0 (disable) or between {_HSTS_VALID_MIN} and {_HSTS_VALID_MAX} seconds.", docs_url=DOCS_BASE + "cf")
            if 0 < ma < _HSTS_WEAK_MAX_AGE:
                advisories.append(f"hsts.max_age={ma}s is weak; >= {_HSTS_PRELOAD_MIN}s (1 year) is recommended.")
        if hsts_req.get("preload") is True:
            if not hsts_req.get("include_subdomains") or (isinstance(ma, int) and ma < _HSTS_PRELOAD_MIN):
                return err(
                    ErrorCode.INVALID_INPUT, _SERVICE,
                    "hsts.preload requires include_subdomains=true and max_age >= 31536000 (1 year), per the HSTS preload-list requirements.",
                    docs_url=DOCS_BASE + "cf",
                )
            advisories.append("hsts.preload only adds the directive; actual preloading still requires submitting the domain at hstspreload.org.")
        if hsts_req.get("include_subdomains") is True:
            advisories.append("hsts.include_subdomains applies HSTS to EVERY subdomain; they must all serve valid HTTPS.")

    try:
        zone_id, zone_name = client.resolve_zone_id(zone)
        before = _settings_map(client.get_zone_settings(zone_id))
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    # Risk classification: ssl_mode change, or an HSTS change that raises protection.
    requires_confirm = "ssl_mode" in settings
    hsts_raises = False
    if hsts_req is not None:
        cur = _current_hsts(before)
        cur_enabled, cur_ma = bool(cur.get("enabled")), (cur.get("max_age") or 0)
        req_enabled = hsts_req.get("enabled", cur_enabled)
        req_ma = hsts_req.get("max_age", cur_ma)
        if (
            (req_enabled and not cur_enabled)
            or (isinstance(req_ma, int) and req_ma > cur_ma)
            or (hsts_req.get("include_subdomains") and not cur.get("include_subdomains"))
            or (hsts_req.get("preload") and not cur.get("preload"))
        ):
            hsts_raises = True
            requires_confirm = True

    # Build the planned PATCH set + proposed snapshot.
    planned: dict[str, Any] = {sid: settings[k] for k, sid in _SETTING_IDS.items() if k in settings}
    if hsts_req is not None:
        merged = {**_current_hsts(before), **{kk: hsts_req[kk] for kk in ("enabled", "max_age", "include_subdomains", "preload", "nosniff") if kk in hsts_req}}
        planned["security_header"] = {"strict_transport_security": merged}
    proposed = {**before, **planned}

    if arguments.get("dry_run"):
        return ok({"zone": zone_name, "dry_run": True, "before": _settings_snapshot(before), "after": _settings_snapshot(proposed), "advisories": advisories, "notes": ["dry_run: nothing was written."]})

    if requires_confirm and arguments.get("confirm") != zone_name:
        return err(
            ErrorCode.CONFIRM_REQUIRED, _SERVICE,
            "High-risk settings change not confirmed.",
            remediation=f"Pass confirm='{zone_name}' (the resolved zone) to change ssl_mode or raise HSTS protection.",
            docs_url=DOCS_BASE + "destructive-mode",
            details={"resolved_zone": zone_name},
        )
    if hsts_raises and arguments.get("acknowledge_hsts_risk") is not True:
        return err(
            ErrorCode.CONFIRM_REQUIRED, _SERVICE,
            "Raising HSTS protection requires explicit acknowledgement.",
            remediation=(
                "Pass acknowledge_hsts_risk=true. HSTS is hard to undo: browsers cache it for max_age, so "
                "do not enable or raise it until HTTPS is fully stable on this host (and every subdomain if "
                "include_subdomains)."
            ),
            docs_url=DOCS_BASE + "destructive-mode",
            details={"resolved_zone": zone_name},
        )

    try:
        for sid, value in planned.items():
            client.patch_zone_setting(zone_id, sid, value)
        after = _settings_map(client.get_zone_settings(zone_id))
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    changed_rules = {_RULE_FOR_SETTING[k] for k in settings if k in _RULE_FOR_SETTING}
    post_findings = [f for f in _audit_settings(after) if f["rule_id"] in changed_rules]
    return ok(
        {
            "zone": zone_name,
            "updated": sorted(planned.keys()),
            "before": _settings_snapshot(before),
            "after": _settings_snapshot(after),
            "post_update_findings": post_findings,
            "advisories": advisories,
            "notes": ["A finding still in post_update_findings means CF did not apply the change as expected, or the value remains sub-optimal."],
        }
    )


# --- registry -------------------------------------------------------------

TOOLS = [
    TOOL_LIST_ZONES,
    TOOL_ZONE_INFO,
    TOOL_LIST_DNS,
    TOOL_WEB_ANALYTICS,
    TOOL_PURGE_CACHE,
    TOOL_PURGE_CACHE_ALL,
    TOOL_SETTINGS_AUDIT,
    TOOL_LIST_REDIRECTS,
    TOOL_CREATE_REDIRECT,
    TOOL_DELETE_REDIRECT,
    TOOL_BULK_REDIRECT_UPSERT,
    TOOL_SETTINGS_UPDATE,
]

HANDLERS = {
    "cf_list_zones": cf_list_zones,
    "cf_zone_info": cf_zone_info,
    "cf_list_dns": cf_list_dns,
    "cf_web_analytics": cf_web_analytics,
    "cf_purge_cache": cf_purge_cache,
    "cf_purge_cache_all": cf_purge_cache_all,
    "cf_settings_audit": cf_settings_audit,
    "cf_list_redirects": cf_list_redirects,
    "cf_create_redirect": cf_create_redirect,
    "cf_delete_redirect": cf_delete_redirect,
    "cf_bulk_redirect_upsert": cf_bulk_redirect_upsert,
    "cf_settings_update": cf_settings_update,
}
