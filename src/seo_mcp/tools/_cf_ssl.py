"""Conservative observation of SSL configuration rules, without expression execution."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any


def _hosts(expression: Any) -> set[str] | None:
    if not isinstance(expression, str) or len(expression) > 4096:
        return None
    if expression.strip() == "true":
        return {"*"}
    hosts: set[str] = set()
    for part in re.split(r"\s+or\s+", expression.strip()):
        part = part.strip()
        if part.startswith("(") and part.endswith(")"):
            part = part[1:-1].strip()
        match = re.fullmatch(r'http\.host\s+eq\s+"([a-zA-Z0-9.-]+)"', part)
        if match:
            hosts.add(match[1].lower())
            continue
        match = re.fullmatch(r'http\.host\s+in\s*\{\s*((?:"[a-zA-Z0-9.-]+"\s*)+)\}', part)
        if not match:
            return None
        hosts.update(h.lower() for h in re.findall(r'"([a-zA-Z0-9.-]+)"', match[1]))
    if any(len(h) > 253 or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                               for label in h.split(".")) for h in hosts):
        return None
    return hosts or None


def ssl_coverage(zone: str, default: Any, ruleset: dict[str, Any] | None, error_code: str | None) -> dict[str, Any]:
    """Report candidate-host phase observations, not complete effective TLS policy."""
    rules = ruleset.get("rules", []) if ruleset else []
    names = {zone, f"www.{zone}"}
    parsed: list[tuple[int, set[str] | None, Any]] = []
    unsupported: list[int] = []
    for position, rule in enumerate(rules):
        if not isinstance(rule, dict):
            parsed.append((position, None, None))
            unsupported.append(position)
            continue
        if rule.get("enabled") is False:
            continue
        params = rule.get("action_parameters")
        if isinstance(params, dict) and "ssl" not in params and rule.get("action") == "set_config":
            continue
        hosts = _hosts(rule.get("expression"))
        ssl = params.get("ssl") if isinstance(params, dict) else None
        if rule.get("action") != "set_config" or rule.get("enabled", True) is not True or ssl not in (
            "off", "flexible", "full", "strict", "origin_pull",
        ):
            hosts = None
        if hosts is None:
            unsupported.append(position)
        else:
            names.update(h for h in hosts if h != "*" and (h == zone or h.endswith(f".{zone}")))
        parsed.append((position, hosts, ssl))

    observed: dict[str, Any] = {}
    for host in sorted(names):
        value = default if not error_code else None
        positions: list[int] = []
        for position, hosts, ssl in parsed:
            if hosts is None:
                value = None
                positions.append(position)
            elif host in hosts or "*" in hosts:
                value = ssl
                positions.append(position)
        observed[host] = {"ssl": value, "rule_positions": positions,
                          "availability": "observed" if value is not None else "unknown"}
    return {
        "status": "unavailable" if error_code else "partial" if unsupported else "observed",
        "default_ssl": default,
        "hosts": observed,
        "other_hostnames": "not_enumerated",
        "origin_readiness": "unknown",
        "effective_policy_complete": False,
        "unsupported_rule_positions": unsupported,
        "error_code": error_code,
        "source": {"phase": "http_config_settings", "ruleset_id": ruleset.get("id") if ruleset else None,
                   "observed_at": datetime.now(UTC).isoformat(), "rules": rules,
                   "rule_version": "cf.ssl.coverage/v1", "calculation": "last supported matching SSL setting in provider order"},
        "caveats": [
            "Only exact hostname equality, hostname sets, their simple OR combinations and true are interpreted.",
            "Later supported rules override earlier SSL observations; unsupported rules make affected coverage unknown.",
            "Apex and www are candidate hostnames, not proof that those hosts exist or are proxied.",
            "Other hostnames, Page Rules and origin certificate readiness are not verified; no zone-wide SSL fix is supplied.",
        ],
    }
