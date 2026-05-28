"""system_status: the "call this first" discovery tool.

Reports which services are configured, the Google auth method, requested
scopes, whether destructive mode is on, and the catalog of currently-registered
tools grouped by service. With ``probe: true`` it makes one cheap live call per
configured service that has a client wired (clients are injected, so tests
exercise the probe path with fakes). Never raises.

This module is import-light on purpose: it does not import ``mcp`` or any Google
library, so its logic is unit-testable without the SDK installed. The tool
schema is exposed as a plain dict; ``server.py`` wraps it into an mcp ``Tool``.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import __version__
from ..auth import google_auth_method, google_configured, required_scopes
from ..config import Config
from ..errors import ok
from ._helpers import annotations


TOOL: dict[str, Any] = {
    "name": "system_status",
    "description": (
        "Report which SEO services are configured and reachable, the Google "
        "auth method and scopes, whether destructive mode is enabled, and the "
        "full catalog of available tools grouped by service. Call this first if "
        "unsure what is set up. Pass probe=true to confirm credentials work via "
        "one cheap live call per configured service (default false: config-only)."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "probe": {
                "type": "boolean",
                "description": (
                    "If true, make one cheap live call per configured service to "
                    "confirm the credentials actually work. Default false."
                ),
            }
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True, open_world=True),
}


_SERVICE_PREFIXES = ("gsc", "ga4", "psi", "cf", "indexnow")


def group_tools(tool_names: list[str]) -> dict[str, list[str]]:
    """Group registered tool names by service prefix.

    ``gsc_*`` -> "gsc", ``ga4_*`` -> "ga4", ``psi_*`` -> "psi", ``cf_*`` -> "cf";
    anything else (e.g. ``system_status``) -> "general". Every service key is
    present even when empty, so the catalog shape is stable across phases.
    """
    catalog: dict[str, list[str]] = {p: [] for p in _SERVICE_PREFIXES}
    catalog["general"] = []
    for name in tool_names:
        prefix = name.split("_", 1)[0]
        if prefix in _SERVICE_PREFIXES:
            catalog[prefix].append(name)
        else:
            catalog["general"].append(name)
    return catalog


def _probe(clients: Mapping[str, Any], key: str, probe: bool) -> bool | None:
    """Run a configured client's cheap reachability probe.

    Returns None when probing is off or no client is wired for the service,
    True/False when a client exists and its ``probe()`` succeeds/fails.
    """
    if not probe:
        return None
    try:
        client = clients.get(key)
    except Exception:
        # Building the client failed (e.g. missing/!invalid Google auth): the
        # service is configured but not reachable with these credentials.
        return False
    if client is None:
        return None
    try:
        return bool(client.probe())
    except Exception:
        return False


def handle(
    arguments: Mapping[str, Any],
    config: Config,
    clients: Mapping[str, Any],
    tool_names: list[str],
) -> dict[str, Any]:
    """Build the system_status envelope. See module docstring for semantics."""
    probe = bool(arguments.get("probe", False))

    method = google_auth_method(config)
    google_ready = google_configured(config)

    # GA4 probe explanation: when google is configured but no GA4 property is,
    # reachable stays null and we explain why. Saves a debugging round-trip.
    ga4_reason: str | None = None
    if google_ready and not config.ga4_property_id:
        ga4_reason = "no default property configured; set SEO_MCP_GA4_PROPERTY_ID to enable the GA4 probe"
    elif not google_ready:
        ga4_reason = "no Google auth configured"

    services: dict[str, Any] = {
        "gsc": {
            "configured": google_ready,
            "auth_method": method,
            "scopes": required_scopes(config) if google_ready else None,
            "reachable": _probe(clients, "gsc", probe) if google_ready else None,
            "default_site": config.gsc_default_site,
        },
        "ga4": {
            "configured": google_ready,
            "auth_method": method,
            # A GA4 probe needs a property to report against (no cheap account
            # ping without the Admin API), so only probe when one is configured.
            "reachable": (
                _probe(clients, "ga4", probe)
                if (google_ready and config.ga4_property_id)
                else None
            ),
            "default_property": config.ga4_property_id,
            "reason": ga4_reason,
        },
        "psi": {
            # PSI works against the anonymous endpoint even without a key, so it
            # is always "configured" (its tools never return AUTH_MISSING). The
            # key only relaxes rate limits.
            "configured": True,
            "auth_method": "api_key" if config.psi_api_key else "anonymous",
            "reachable": _probe(clients, "psi", probe),
        },
        "cf": {
            "configured": config.cf_api_token is not None,
            "auth_method": "api_token" if config.cf_api_token else None,
            "reachable": _probe(clients, "cf", probe) if config.cf_api_token else None,
            "default_zone": config.cf_zone,
        },
        "indexnow": {
            "configured": config.indexnow_key is not None,
            "auth_method": "shared_key" if config.indexnow_key else None,
            "reachable": _probe(clients, "indexnow", probe) if config.indexnow_key else None,
            "key_location": config.indexnow_key_location,
        },
    }

    return ok(
        {
            "version": __version__,
            "destructive_enabled": config.allow_destructive,
            # Where config values came from: the TOML path if a file was read,
            # else "env" (env vars + defaults, no file). Useful for debugging.
            "config_source": config.source_path or "env",
            "services": services,
            "tools": group_tools(tool_names),
        }
    )
