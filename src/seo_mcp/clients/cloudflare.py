"""Cloudflare client (API v4) over stdlib urllib. No external HTTP dependency.

``CfClient`` wraps the Bearer-token REST API. The network seam is
``_raw_request`` (does the HTTP, returns the parsed ``{success, errors, result}``
envelope, or raises ``ApiError`` on transport/HTTP failure). ``_http_request``
layers Cloudflare's ``success`` flag handling on top. Tests monkeypatch
``_raw_request`` to inject canned envelopes (or raise) without touching urllib.

Scope is SEO-relevant reads plus cache purge (gated in the tools): zones, zone
info, DNS read, read-only Web Analytics, and purge.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..config import Config
from ..errors import ErrorCode
from .errors import ApiError, map_http_status


API_BASE = "https://api.cloudflare.com/client/v4"
_TIMEOUT_SECONDS = 20
_DYNAMIC_REDIRECT_PHASE = "http_request_dynamic_redirect"
_REDIRECT_PHASE_ACCOUNT = "http_request_redirect"


class CfClient:
    def __init__(self, token: str) -> None:
        self._token = token
        self._account_id: str | None = None

    # --- network seam -----------------------------------------------------

    def _raw_request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Perform the HTTP call and return the parsed JSON envelope. Raises
        ApiError on HTTP or transport failure. This is the single seam tests
        monkeypatch."""
        url = f"{API_BASE}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._token}")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise self._error_from_http(exc.code, body_text) from exc
        except urllib.error.URLError as exc:
            raise ApiError(
                ErrorCode.UPSTREAM_ERROR,
                f"Cloudflare request failed: {exc.reason}",
            ) from exc

    def _http_request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self._raw_request(method, path, body)
        if not payload.get("success", False):
            raise self._error_from_payload(payload)
        return payload

    @staticmethod
    def _error_from_http(status: int, body_text: str) -> ApiError:
        cf_errors = []
        try:
            cf_errors = json.loads(body_text).get("errors", [])
        except (ValueError, AttributeError):
            pass
        error = map_http_status(status, body_text, service="Cloudflare")
        if cf_errors:
            error.details = {**(error.details or {}), "cf_errors": cf_errors}
        # CF 9109 "Unauthorized to access requested resource" = a VALID token that
        # simply can't reach this zone/account resource (distinct from a bad
        # token). Give the scope-specific remediation (FEEDBACK R19-FIND-2).
        if status == 403 and any(e.get("code") == 9109 for e in cf_errors if isinstance(e, dict)):
            error.remediation = (
                "Cloudflare 9109: the token cannot access this resource. Most often the "
                "token is valid but its Zone Resources do not include this zone - set them to "
                "'All zones from an account', or add this zone (for an account tool, grant the "
                "matching account-level permission). If the zone scope is already correct, the "
                "token may be malformed or expired; regenerate it."
            )
        return error

    @staticmethod
    def _error_from_payload(payload: dict[str, Any]) -> ApiError:
        cf_errors = payload.get("errors", [])
        message = cf_errors[0].get("message") if cf_errors else "Cloudflare returned success=false."
        return ApiError(
            ErrorCode.UPSTREAM_ERROR,
            f"Cloudflare: {message}",
            details={"cf_errors": cf_errors},
        )

    # --- zones ------------------------------------------------------------

    def list_zones(self) -> list[dict[str, Any]]:
        return self._http_request("GET", "/zones?per_page=50").get("result", [])

    def resolve_zone(self, hostname: str) -> dict[str, Any]:
        payload = self._http_request("GET", f"/zones?name={urllib.parse.quote(hostname)}")
        results = payload.get("result", [])
        if not results:
            raise ApiError(
                ErrorCode.NOT_FOUND,
                f"Cloudflare zone '{hostname}' not found, or the token has no access to it.",
                remediation="Check the hostname and that the API token can read this zone.",
            )
        return results[0]

    def resolve_zone_id(self, hostname: str) -> tuple[str, str]:
        zone = self.resolve_zone(hostname)
        return zone["id"], zone["name"]

    def zone_info(self, hostname: str) -> dict[str, Any]:
        # The name-filtered lookup already returns the full zone object.
        return self.resolve_zone(hostname)

    def get_zone_settings(self, zone_id: str) -> list[dict[str, Any]]:
        """Read all zone settings (read-only). CF returns a list of
        ``{id, value, editable, modified_on, ...}`` entries; the caller indexes
        by ``id`` (e.g. 'ssl', 'always_use_https', 'security_header')."""
        return self._http_request("GET", f"/zones/{zone_id}/settings").get("result", [])

    def patch_zone_setting(self, zone_id: str, setting_id: str, value: Any) -> dict[str, Any]:
        """Write one zone setting (gated in tools). PATCH the single-setting
        endpoint with ``{"value": ...}``; returns the updated setting. Needs the
        Zone Settings:Edit token scope (the audit only needs Read)."""
        return self._http_request(
            "PATCH", f"/zones/{zone_id}/settings/{setting_id}", {"value": value}
        ).get("result", {})

    # --- bot management / managed robots.txt (writes gated in tools) ------

    def get_bot_management(self, zone_id: str) -> dict[str, Any]:
        """Read the zone's Bot Management config (read-only). Returns the full
        config object, which includes the managed-robots / Content-Signals
        fields (``is_robots_txt_managed``, ``cf_robots_variant``,
        ``ai_bots_protection``, ``content_bots_protection``,
        ``crawler_protection``). Needs the Bot Management Read scope."""
        return self._http_request("GET", f"/zones/{zone_id}/bot_management").get("result", {})

    def update_bot_management(self, zone_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Write Bot Management fields (gated in tools). GET the current config,
        overlay only the changed keys, drop the read-only/derived fields the PUT
        rejects, and PUT the merged object back so we never clobber the zone's
        plan-specific configuration. Returns the updated config. Needs the Bot
        Management Edit scope (the read path only needs Read)."""
        merged = {**self.get_bot_management(zone_id), **changes}
        # Server-computed fields the schema marks read-only; PUT rejects them.
        for ro in ("stale_zone_configuration", "using_latest_model"):
            merged.pop(ro, None)
        return self._http_request("PUT", f"/zones/{zone_id}/bot_management", merged).get("result", {})

    # --- dns --------------------------------------------------------------

    def list_dns(self, zone_id: str, record_type: str | None = None) -> list[dict[str, Any]]:
        path = f"/zones/{zone_id}/dns_records?per_page=500"
        if record_type:
            path += f"&type={urllib.parse.quote(record_type)}"
        return self._http_request("GET", path).get("result", [])

    # --- account + web analytics -----------------------------------------

    def get_account_id(self) -> str:
        """Resolve the account id from any visible zone (reference cf.py). Cached."""
        if self._account_id:
            return self._account_id
        zones = self._http_request("GET", "/zones?per_page=1").get("result", [])
        if not zones:
            raise ApiError(
                ErrorCode.NOT_FOUND,
                "No zones visible to the token; cannot derive the account id for Web Analytics.",
            )
        account = zones[0].get("account", {}).get("id")
        if not account:
            raise ApiError(
                ErrorCode.UPSTREAM_ERROR,
                "Zone response did not include an account id.",
            )
        self._account_id = account
        return account

    @property
    def account_id(self) -> str | None:
        """The cached account id, or None if not resolved yet (no extra call)."""
        return self._account_id

    def web_analytics_list(self) -> list[dict[str, Any]]:
        account = self.get_account_id()
        return self._http_request(
            "GET", f"/accounts/{account}/rum/site_info/list?per_page=50"
        ).get("result", [])

    def web_analytics_get(self, site_tag: str) -> dict[str, Any]:
        account = self.get_account_id()
        return self._http_request(
            "GET", f"/accounts/{account}/rum/site_info/{urllib.parse.quote(site_tag)}"
        ).get("result", {})

    # --- cache purge (gated in tools) ------------------------------------

    def purge_files(self, zone_id: str, urls: list[str]) -> None:
        self._http_request("POST", f"/zones/{zone_id}/purge_cache", {"files": urls})

    def purge_all(self, zone_id: str) -> None:
        self._http_request("POST", f"/zones/{zone_id}/purge_cache", {"purge_everything": True})

    # --- single / dynamic redirects (gated in tools) ---------------------
    #
    # Single Redirects live in the zone's http_request_dynamic_redirect phase
    # ruleset. We never blind-PUT the phase entrypoint (that would replace ALL
    # rules); we read it, then append/delete individual rules by id.

    def get_dynamic_redirects(self, zone_id: str) -> tuple[str | None, list[dict[str, Any]]]:
        """Return ``(ruleset_id, rules)`` for the zone's dynamic-redirect phase.
        Returns ``(None, [])`` when no ruleset exists yet (CF 404s the
        entrypoint until the first rule is created)."""
        try:
            payload = self._http_request(
                "GET", f"/zones/{zone_id}/rulesets/phases/{_DYNAMIC_REDIRECT_PHASE}/entrypoint"
            )
        except ApiError as exc:
            if exc.code == ErrorCode.NOT_FOUND:
                return None, []
            raise
        result = payload.get("result", {}) or {}
        return result.get("id"), result.get("rules", []) or []

    def add_dynamic_redirect(self, zone_id: str, rule: dict[str, Any]) -> dict[str, Any]:
        """Append one redirect rule (never replaces existing rules). Creates the
        phase ruleset on first use. Returns the CF ``result``."""
        ruleset_id, _ = self.get_dynamic_redirects(zone_id)
        if ruleset_id is None:
            body = {
                "name": "default",
                "kind": "zone",
                "phase": _DYNAMIC_REDIRECT_PHASE,
                "rules": [rule],
            }
            return self._http_request("POST", f"/zones/{zone_id}/rulesets", body).get("result", {})
        return self._http_request(
            "POST", f"/zones/{zone_id}/rulesets/{ruleset_id}/rules", rule
        ).get("result", {})

    def delete_dynamic_redirect(self, zone_id: str, ruleset_id: str, rule_id: str) -> None:
        self._http_request("DELETE", f"/zones/{zone_id}/rulesets/{ruleset_id}/rules/{rule_id}")

    # --- bulk redirects (account-level; gated in tools) ------------------
    #
    # Bulk Redirects = an account Redirect List (source->target items, added
    # asynchronously) referenced by an account ruleset in the
    # http_request_redirect phase. Reuses get_account_id().

    def list_redirect_lists(self, account_id: str) -> list[dict[str, Any]]:
        lists = self._http_request("GET", f"/accounts/{account_id}/rules/lists").get("result", []) or []
        return [item for item in lists if item.get("kind") == "redirect"]

    def create_redirect_list(self, account_id: str, name: str, description: str = "") -> dict[str, Any]:
        body = {"name": name, "kind": "redirect", "description": description or "Managed by SEOMonster"}
        return self._http_request("POST", f"/accounts/{account_id}/rules/lists", body).get("result", {})

    def get_redirect_list_items(self, account_id: str, list_id: str) -> list[dict[str, Any]]:
        return self._http_request(
            "GET", f"/accounts/{account_id}/rules/lists/{list_id}/items?per_page=500"
        ).get("result", []) or []

    def append_redirect_items(self, account_id: str, list_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Add items (async). Returns the result envelope (carries operation_id)."""
        return self._http_request(
            "POST", f"/accounts/{account_id}/rules/lists/{list_id}/items", items
        ).get("result", {})

    def get_bulk_operation(self, account_id: str, operation_id: str) -> dict[str, Any]:
        return self._http_request(
            "GET", f"/accounts/{account_id}/rules/lists/bulk_operations/{operation_id}"
        ).get("result", {})

    def get_account_redirect_ruleset(self, account_id: str) -> tuple[str | None, list[dict[str, Any]]]:
        """Return ``(ruleset_id, rules)`` for the account http_request_redirect
        phase. ``(None, [])`` when no ruleset exists yet."""
        try:
            payload = self._http_request(
                "GET", f"/accounts/{account_id}/rulesets/phases/{_REDIRECT_PHASE_ACCOUNT}/entrypoint"
            )
        except ApiError as exc:
            if exc.code == ErrorCode.NOT_FOUND:
                return None, []
            raise
        result = payload.get("result", {}) or {}
        return result.get("id"), result.get("rules", []) or []

    def add_account_redirect_rule(self, account_id: str, rule: dict[str, Any]) -> dict[str, Any]:
        """Append a Bulk Redirect enable-rule. Creates the account phase ruleset
        on first use. Never blind-replaces existing rules."""
        ruleset_id, _ = self.get_account_redirect_ruleset(account_id)
        if ruleset_id is None:
            body = {"name": "default", "kind": "root", "phase": _REDIRECT_PHASE_ACCOUNT, "rules": [rule]}
            return self._http_request("POST", f"/accounts/{account_id}/rulesets", body).get("result", {})
        return self._http_request(
            "POST", f"/accounts/{account_id}/rulesets/{ruleset_id}/rules", rule
        ).get("result", {})

    # --- health -----------------------------------------------------------

    def probe(self) -> bool:
        """Cheap reachability check used by system_status: list one zone."""
        self._http_request("GET", "/zones?per_page=1")
        return True


def build_cf_client(config: Config) -> CfClient | None:
    """Construct a CfClient when a token is configured, else None (the tools
    then return AUTH_MISSING)."""
    if not config.cf_api_token:
        return None
    return CfClient(config.cf_api_token)
