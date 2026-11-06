"""Stable adapter for saved SEOmonster result envelopes.

This module deliberately does not import ``seo_mcp``. The boundary is a
versioned JSON document containing the public result envelope plus capture
metadata. A future live adapter may speak MCP over stdio, but it must produce
the same document before the workbench can ingest it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import canonical_json, digest, stable_id, timestamp, finite_number


ADAPTER_VERSION = "seomonster-result/v1"
REQUIRED_ENVELOPE_KEYS = frozenset({"ok", "data", "error"})
UNTRUSTED_MARKERS = ("<script", "ignore previous", "system prompt", "developer message", "tool call")
MAX_CAPTURE_BYTES = 2_000_000


class AdapterError(ValueError):
    """Saved input does not satisfy the public adapter contract."""


@dataclass(frozen=True)
class AdaptedResult:
    snapshot_id: str
    project_id: str
    tool: str
    captured_at: str
    provider: dict[str, Any]
    arguments: dict[str, Any]
    envelope: dict[str, Any]
    raw: dict[str, Any]
    content_hash: str
    source_locator: str
    freshness: str
    caveats: tuple[str, ...]


def _validate(bundle: dict[str, Any]) -> None:
    required = {"adapter_version", "tool", "captured_at", "provider", "arguments", "result"}
    missing = required - set(bundle)
    if missing:
        raise AdapterError(f"saved result missing fields: {sorted(missing)}")
    if bundle["adapter_version"] != ADAPTER_VERSION:
        raise AdapterError(f"unsupported adapter_version: {bundle['adapter_version']!r}")
    envelope = bundle["result"]
    if not isinstance(envelope, dict) or not REQUIRED_ENVELOPE_KEYS <= set(envelope):
        raise AdapterError("result must preserve the SEOmonster {ok,data,error} envelope")
    if not isinstance(envelope["ok"], bool):
        raise AdapterError("result.ok must be a boolean")
    if envelope["ok"] and envelope["error"] is not None:
        raise AdapterError("successful result must have error=null")
    if not envelope["ok"] and envelope["data"] is not None:
        raise AdapterError("failed result must have data=null")
    if not envelope["ok"] and not isinstance(envelope["error"], dict):
        raise AdapterError("failed result must have an error object")
    if not isinstance(bundle["tool"], str) or not bundle["tool"]:
        raise AdapterError("tool must be a nonempty string")
    if not isinstance(bundle["arguments"], dict) or not isinstance(bundle["provider"], dict):
        raise AdapterError("arguments and provider must be JSON objects")
    provider = bundle["provider"]
    if not {"name", "status", "cost"} <= set(provider) or not isinstance(provider["cost"], dict):
        raise AdapterError("provider must include name, status, and cost metadata")
    if not {"model", "currency", "amount"} <= set(provider["cost"]):
        raise AdapterError("provider.cost must include model, currency, and amount")
    if not all(isinstance(provider[key], str) for key in ("name", "status")):
        raise AdapterError("provider name and status must be strings")
    cost = provider["cost"]
    if not all(isinstance(cost[key], str) for key in ("model", "currency")) or (
        cost["amount"] is not None and (not finite_number(cost["amount"]) or cost["amount"] < 0)
    ):
        raise AdapterError("invalid provider cost metadata; unknown cost must be null")
    try:
        timestamp(bundle["captured_at"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise AdapterError("captured_at must be an ISO-8601 timestamp with timezone") from exc
    if bundle.get("freshness", "unknown") not in {"current", "stale", "unknown"}:
        raise AdapterError("freshness must be current, stale, or unknown")


def _untrusted_caveats(bundle: dict[str, Any]) -> tuple[str, ...]:
    text = json.dumps(bundle.get("result"), ensure_ascii=False).lower()
    if any(marker in text for marker in UNTRUSTED_MARKERS):
        return ("Untrusted fetched content was retained as inert data; embedded instructions were not executed.",)
    return ()


def load_saved_result(path: str | Path, project_id: str) -> AdaptedResult:
    source = Path(path)
    try:
        with source.open("rb") as stream:
            data = stream.read(MAX_CAPTURE_BYTES + 1)
        if len(data) > MAX_CAPTURE_BYTES:
            raise AdapterError("saved result exceeds size limit")
        bundle = json.loads(data)
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"could not read saved result: {exc}") from exc
    return adapt_saved_result(bundle, project_id, source.name)


def adapt_saved_result(bundle: dict[str, Any], project_id: str, source_locator: str = "mcp-capture.json") -> AdaptedResult:
    """Validate an in-memory capture without accepting a host filesystem path."""
    if not isinstance(bundle, dict):
        raise AdapterError("saved result must be a JSON object")
    try:
        serialized = canonical_json(bundle)
        if len(serialized.encode()) > MAX_CAPTURE_BYTES:
            raise AdapterError("saved result exceeds size limit")
        bundle = json.loads(serialized)  # detach caller-owned data
    except (TypeError, ValueError, RecursionError) as exc:
        raise AdapterError("saved result must be bounded, finite JSON") from exc
    _validate(bundle)
    content_hash = digest(bundle)
    return AdaptedResult(
        snapshot_id=stable_id("snap", project_id, content_hash),
        project_id=project_id,
        tool=str(bundle["tool"]),
        captured_at=str(bundle["captured_at"]),
        provider=bundle["provider"],
        arguments=bundle["arguments"],
        envelope=bundle["result"],
        raw=bundle,
        content_hash=content_hash,
        source_locator=Path(source_locator).name,
        freshness=bundle.get("freshness", "unknown"),
        caveats=_untrusted_caveats(bundle),
    )
