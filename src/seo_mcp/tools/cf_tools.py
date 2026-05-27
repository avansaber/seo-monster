"""Cloudflare tools (6).

Reads: cf_list_zones, cf_zone_info, cf_list_dns, cf_web_analytics (read-only).
Writes (gated): cf_purge_cache and cf_purge_cache_all sit behind
SEO_MCP_ALLOW_DESTRUCTIVE; the all-purge additionally requires a confirm token
equal to the resolved zone name. The destructive gate is checked before any
client call, so a blocked purge makes zero network requests.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..clients.errors import ApiError
from ..config import Config
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import require_client


_SERVICE = "cf"
_REMEDIATION = (
    "Set CF_API_TOKEN to a Cloudflare API token (Zone:Read; add DNS:Read, "
    "Account Analytics:Read, and Cache Purge:Purge as needed). See README > Auth."
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
        remediation="Set SEO_MCP_ALLOW_DESTRUCTIVE=true to enable Cloudflare cache purge.",
        docs_url=DOCS_BASE + "destructive-mode",
    )


# --- cf_list_zones --------------------------------------------------------

TOOL_LIST_ZONES = {
    "name": "cf_list_zones",
    "description": "List the Cloudflare zones the API token can see, with status, plan, and id.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
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


# --- registry -------------------------------------------------------------

TOOLS = [
    TOOL_LIST_ZONES,
    TOOL_ZONE_INFO,
    TOOL_LIST_DNS,
    TOOL_WEB_ANALYTICS,
    TOOL_PURGE_CACHE,
    TOOL_PURGE_CACHE_ALL,
]

HANDLERS = {
    "cf_list_zones": cf_list_zones,
    "cf_zone_info": cf_zone_info,
    "cf_list_dns": cf_list_dns,
    "cf_web_analytics": cf_web_analytics,
    "cf_purge_cache": cf_purge_cache,
    "cf_purge_cache_all": cf_purge_cache_all,
}
