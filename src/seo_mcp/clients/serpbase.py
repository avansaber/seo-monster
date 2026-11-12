"""Optional SerpBase search client with a bounded, explicit paid-call policy."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import threading
import unicodedata
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlsplit

from .. import __version__
from ..errors import ErrorCode
from .errors import ApiError

_HOST = "api.serpbase.dev"
_PATH = "/google/search"
_MAX_BODY = 1024 * 1024
_TIMEOUT = 60
_MAX_DECODE_PASSES = 8
_lock = threading.Lock()
_remaining = 1


def _fail(message: str, *, code: ErrorCode = ErrorCode.UPSTREAM_ERROR, details: dict | None = None) -> ApiError:
    return ApiError(code, message, details=details)


def _text(value: Any, limit: int = 500) -> str | None:
    if not isinstance(value, str):
        return None
    return "".join(c for c in value if ord(c) >= 32 and ord(c) != 127)[:limit]


def _contains_key(value: str, key: str) -> bool:
    """Check encoded forms without changing content returned to the caller."""
    current = value
    for _ in range(_MAX_DECODE_PASSES):
        if key in current:
            return True
        decoded = unquote(current)
        if decoded == current:
            return False
        current = decoded
    return key in current or unquote(current) != current


def _safe_text(value: Any, key: str, limit: int = 500) -> str | None:
    text = _text(value, limit)
    if text is None or not key:
        return text
    text = text.replace(key, "[redacted]")
    return "[redacted]" if _contains_key(text, key) else text


def _url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if parsed.scheme not in ("http", "https") or not host or parsed.username is not None or port == 0:
            return None
        if "%" in host or host.lower().rstrip(".") in ("localhost", "localhost.localdomain", "168.63.129.16"):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global or address.is_multicast or address.is_reserved:
                return None
        return value
    except (ValueError, UnicodeError):
        return None


class SerpBaseClient:
    """One paid attempt per process; the ceiling resets on restart."""

    def __init__(self, key: str) -> None:
        if not isinstance(key, str) or not key or len(key) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) > 126 for c in key):
            raise _fail("SerpBase API key format is invalid.", code=ErrorCode.AUTH_INVALID)
        self._key = key

    def _request(self, data: bytes) -> tuple[int, bytes]:
        connection = http.client.HTTPSConnection(_HOST, timeout=_TIMEOUT)
        try:
            connection.request("POST", _PATH, body=data, headers={
                "Content-Type": "application/json", "X-API-Key": self._key,
                "User-Agent": f"SEOMonster/{__version__}",
            })
            with connection.getresponse() as response:
                return response.status, response.read(_MAX_BODY + 1)
        finally:
            connection.close()

    def serp(self, keyword: str) -> dict[str, Any]:
        global _remaining
        if not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in keyword):
            raise _fail("SerpBase query is invalid.", code=ErrorCode.INVALID_INPUT)
        body = json.dumps({"q": keyword.strip(), "gl": "us", "hl": "en", "device": "mobile", "page": 1}).encode("utf-8")
        with _lock:
            if _remaining <= 0:
                raise _fail("SerpBase local process limit reached; restart the server to reset it. No request was sent.", code=ErrorCode.RATE_LIMITED,
                            details={"dispatch": "not_dispatched", "charge": "not_applicable"})
            _remaining -= 1
        try:
            http_status, raw = self._request(body)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise _fail("SerpBase request did not return a usable response; charge is unknown. Do not retry automatically.",
                        details={"dispatch": "unknown", "charge": "unknown"}) from exc
        if http_status != 200 or len(raw) > _MAX_BODY:
            mapping = {400: ErrorCode.INVALID_INPUT, 401: ErrorCode.AUTH_INVALID,
                       403: ErrorCode.AUTH_INVALID, 404: ErrorCode.NOT_FOUND, 429: ErrorCode.RATE_LIMITED}
            raise _fail("SerpBase returned an unusable HTTP response; charge is unknown.",
                        code=mapping.get(http_status, ErrorCode.UPSTREAM_ERROR) if http_status != 200 else ErrorCode.UPSTREAM_ERROR,
                        details={"dispatch": "dispatched", "charge": "unknown", "http_status": http_status})
        try:
            payload = json.loads(raw)
        except (ValueError, RecursionError, UnicodeError) as exc:
            raise _fail("SerpBase returned invalid JSON; charge is unknown.", details={"dispatch": "dispatched", "charge": "unknown"}) from exc
        if not isinstance(payload, dict) or type(payload.get("status")) is not int:
            raise _fail("SerpBase response status is unavailable; charge is unknown.", details={"dispatch": "dispatched", "charge": "unknown"})
        status = payload["status"]
        charge = payload.get("credits_charged")
        charge_valid = type(charge) in (int, float) and 0 <= charge <= 1000000
        consistent = charge_valid and not (status == 0 and charge == 0) and not (status != 0 and charge > 0)
        observed = charge if consistent and payload.get("charge_status") != "unknown" else None
        meta = {"dispatch": "dispatched", "charge": "reported" if observed is not None else "unknown",
                "credits_charged": observed, "credits_reported": charge if charge_valid else None,
                "charge_inconsistent": bool(charge_valid and not consistent)}
        request_id = payload.get("request_id")
        # Accept documented identifier formats only, with no credential or query echo.
        if (isinstance(request_id, str)
                and re.fullmatch(r"(?:r-[0-9a-fA-F]{24}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})", request_id)
                and self._key not in request_id
                and keyword.casefold() not in request_id.casefold()):
            meta["request_id"] = request_id
        if status != 0:
            mapping = {1000: ErrorCode.INVALID_INPUT, 1001: ErrorCode.AUTH_INVALID, 1029: ErrorCode.RATE_LIMITED}
            meta["provider_status"] = status
            message = "SerpBase reports insufficient credits." if status == 1020 else "SerpBase reported an unsuccessful request."
            raise _fail(message, code=mapping.get(status, ErrorCode.UPSTREAM_ERROR), details=meta)
        if not isinstance(payload.get("query"), str) or not payload["query"].strip() or type(payload.get("page")) is not int or payload["page"] != 1:
            raise _fail("SerpBase returned incomplete search metadata.", details=meta)
        echo = unicodedata.normalize("NFKC", " ".join(payload["query"].split())).casefold()
        requested = unicodedata.normalize("NFKC", " ".join(keyword.split())).casefold()
        if echo != requested:
            raise _fail("SerpBase returned a response for a different query.", details=meta)
        organic = payload.get("organic")
        paa = payload.get("people_also_ask")
        related = payload.get("related_searches")
        overview = payload.get("ai_overview")
        for value in (organic, paa, related):
            if value is not None and not isinstance(value, list):
                raise _fail("SerpBase returned an invalid result shape.", details=meta)
        if overview is not None and not isinstance(overview, dict):
            raise _fail("SerpBase returned an invalid overview shape.", details=meta)
        rows, skipped, missing = [], 0, 0
        for item in (organic or [])[:50]:
            if not isinstance(item, dict):
                raise _fail("SerpBase returned an invalid organic result.", details=meta)
            candidate = item.get("url") or item.get("link")
            if candidate is None:
                missing += 1
                continue
            url = _url(candidate)
            if url and self._key and _contains_key(url, self._key):
                url = None
            if not url:
                skipped += 1
                continue
            rank = item.get("rank")
            rows.append({"url": url, "rank": rank if type(rank) is int and rank > 0 else None,
                         "title": _safe_text(item.get("title"), self._key), "domain": urlsplit(url).hostname})
        questions = []
        for item in (paa or [])[:50]:
            if not isinstance(item, dict) or not isinstance(item.get("question"), str):
                raise _fail("SerpBase returned invalid question data.", details=meta)
            questions.append(_safe_text(item["question"], self._key))
        if related is not None and any(not isinstance(item, str) for item in related[:50]):
            raise _fail("SerpBase returned invalid related-search data.", details=meta)
        organic_skipped = skipped
        citations = []
        citation_missing = 0
        citation_unsafe = 0
        if overview is not None:
            if (not isinstance(overview.get("summary"), str) or not isinstance(overview.get("sources"), list)
                    or ("truncated" in overview and type(overview["truncated"]) is not bool)):
                raise _fail("SerpBase returned invalid overview data.", details=meta)
            for item in overview.get("sources", [])[:50]:
                if not isinstance(item, dict):
                    raise _fail("SerpBase returned invalid citation data.", details=meta)
                candidate = item.get("link")
                if candidate is None:
                    citation_missing += 1
                    continue
                url = _url(candidate)
                if url and self._key and _contains_key(url, self._key):
                    url = None
                if url:
                    citations.append({"url": url, "domain": urlsplit(url).hostname, "title": _safe_text(item.get("title"), self._key)})
                else:
                    skipped += 1
                    citation_unsafe += 1
        overview_truncated = None
        if overview is not None:
            if (overview.get("truncated") is True or len(overview["summary"]) > 10000
                    or len(overview["sources"]) > 50 or citation_missing or citation_unsafe):
                overview_truncated = True
            elif overview.get("truncated") is False:
                overview_truncated = False
        return {
            "organic": rows if organic is not None else None,
            "paa": questions if paa is not None else None,
            "related": [_safe_text(item, self._key) for item in related[:50]] if related is not None else None,
            "ai_overview_present": True if overview is not None else None,
            "ai_overview_text": _safe_text(overview.get("summary"), self._key, 10000) if overview else None,
            "ai_overview_citations": citations if overview is not None else None,
            "result_types": None,
            "provider_meta": {**meta, "provider": "serpbase", "device_requested": "mobile", "device_observed": None,
                              "locale_requested": "us-en", "page": 1, "retrieved_at": datetime.now(UTC).isoformat(),
                              "unsafe_urls_skipped": skipped, "missing_organic_urls": missing,
                              "organic_incomplete": None if organic is None else bool(organic_skipped or missing or len(organic) > 50),
                              "missing_citation_urls": citation_missing, "unsafe_citation_urls": citation_unsafe,
                              "truncated": any(isinstance(v, list) and len(v) > 50 for v in (organic, paa, related)),
                              "overview_truncated": overview_truncated},
        }


def build_serpbase_client(config: Any) -> SerpBaseClient | None:
    key = getattr(config, "serpbase_api_key", None)
    return SerpBaseClient(key) if key else None


def dataforseo_selected(config: Any) -> bool:
    selection = getattr(config, "serp_provider", None)
    return selection is None or selection.lower() == "dataforseo"


def selected_serp_client(config: Any, clients: Any) -> tuple[str, Any]:
    selection = getattr(config, "serp_provider", None)
    if dataforseo_selected(config):
        return "dataforseo", clients.get("dataforseo")
    if selection.lower() != "serpbase":
        raise _fail("SERP provider selection is invalid.", code=ErrorCode.INVALID_INPUT)
    client = clients.get("serpbase")
    if client is None:
        raise _fail("SerpBase is selected but its API key is not configured.", code=ErrorCode.AUTH_MISSING)
    return "serpbase", client
