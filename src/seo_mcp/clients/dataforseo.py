"""DataForSEO client (roadmap F2/F3) over stdlib urllib. No HTTP dependency.

The single optional paid vendor (design doc §0.2): one Basic-auth key unlocks
SERP (organic + PAA + related + AI-overview detection), keyword volume /
difficulty / intent, and ranked-keywords (for competitor keyword gaps). The
network seam is ``_raw_request`` (does the HTTP, returns parsed JSON, or raises
ApiError); tests monkeypatch it to inject canned task envelopes without urllib.

DataForSEO wraps every response as ``{status_code, tasks:[{status_code,
result:[...]}]}``; ``_unwrap`` enforces the 20000 success codes and returns the
first task's result list. Method response shapes are parsed defensively (live
shape drift is the tester's catch, not a unit-test concern): malformed entries
are skipped, and a structurally wrong payload, non-JSON body, timeout or broken
connection is an UPSTREAM_ERROR rather than an uncaught exception.
"""

from __future__ import annotations

import base64
import http.client
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from ..config import Config
from ..errors import ErrorCode
from .errors import ApiError, map_http_status

API_BASE = "https://api.dataforseo.com"
_TIMEOUT_SECONDS = 30
_SUCCESS = 20000
# US English defaults; callers may override.
_LOCATION_US = 2840
_LANGUAGE_EN = "en"


class DataForSEOClient:
    def __init__(self, login: str, password: str) -> None:
        self._auth = base64.b64encode(f"{login}:{password}".encode()).decode()
        # Redacted from upstream error bodies before they reach envelope details.
        self._secrets = (login, password, self._auth)

    # --- network seam -----------------------------------------------------
    def _raw_request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_unredirected_header("Authorization", f"Basic {self._auth}")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            try:
                body_text = exc.read(65536).decode("utf-8", errors="replace")
            except (OSError, http.client.HTTPException):
                body_text = ""
            error = map_http_status(exc.code, body_text, service="DataForSEO", secrets=self._secrets)
            # A remote body may contain secrets beyond our configured values.
            # Keep its status mapping, never the free-form diagnostic content.
            error.details = {"status": exc.code, "body": "[redacted]" if body_text else ""}
            raise error from exc
        except urllib.error.URLError as exc:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, "DataForSEO request failed.") from exc
        except TimeoutError as exc:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, f"DataForSEO request timed out after {_TIMEOUT_SECONDS}s.") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, f"DataForSEO connection failed ({type(exc).__name__}).") from exc
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, "DataForSEO returned a response that is not valid JSON.") from exc

    def _unwrap(self, payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise _shape_error("response")
        if payload.get("status_code") != _SUCCESS:
            raise ApiError(
                ErrorCode.UPSTREAM_ERROR,
                "DataForSEO returned an unsuccessful response.",
                details={"status_code": _status_code(payload.get("status_code"))},
            )
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            raise _shape_error("tasks")
        if not tasks:
            return []
        t0 = tasks[0]
        if not isinstance(t0, dict):
            raise _shape_error("task")
        if t0.get("status_code") != _SUCCESS:
            raise ApiError(
                ErrorCode.UPSTREAM_ERROR,
                "DataForSEO returned an unsuccessful task.",
                details={"status_code": _status_code(t0.get("status_code"))},
            )
        result = t0.get("result")
        if not isinstance(result, list):
            raise _shape_error("result")
        return result

    def _post(self, path: str, body: Any) -> list[dict[str, Any]]:
        return self._unwrap(self._raw_request("POST", path, body))

    # --- SERP -------------------------------------------------------------
    def serp(self, keyword: str, *, location_code: int = _LOCATION_US, language_code: str = _LANGUAGE_EN, depth: int = 20) -> dict[str, Any]:
        result = self._post(
            "/v3/serp/google/organic/live/advanced",
            [{"keyword": keyword, "location_code": location_code, "language_code": language_code, "depth": depth}],
        )
        items = _result_items(result)
        organic: list[dict[str, Any]] = []
        paa: list[str] = []
        related: list[str] = []
        # Invalid entries cannot establish that the overview was absent.
        ai_overview: bool | None = False if all(isinstance(it, dict) and isinstance(it.get("type"), str) for it in items) else None
        ai_overview_citations: list[dict[str, Any]] = []
        ai_overview_text_parts: list[str] = []
        result_types: set[str] = set()
        for it in _dicts(items):
            t = _str(it.get("type"))
            if t:
                result_types.add(t)
            if t == "organic":
                organic.append({
                    "rank": it.get("rank_absolute"),
                    "url": _str(it.get("url")),
                    "title": _str(it.get("title")),
                    "domain": _str(it.get("domain")),
                })
            elif t == "people_also_ask":
                for sub in _dicts(it.get("items")):
                    q = _str(sub.get("title"))
                    if q:
                        paa.append(q)
            elif t == "related_searches":
                for q in _list(it.get("items")):
                    if isinstance(q, str):
                        related.append(q)
            elif t == "ai_overview":
                ai_overview = True
                ai_overview_citations.extend(_extract_references(it.get("references")))
                if it.get("text"):
                    ai_overview_text_parts.append(str(it["text"]))
                for sub in _dicts(it.get("items")):
                    ai_overview_citations.extend(_extract_references(sub.get("references")))
                    for field in ("text", "snippet", "title"):
                        if sub.get(field):
                            ai_overview_text_parts.append(str(sub[field]))
        return {
            "organic": organic,
            "paa": _dedup(paa),
            "related": _dedup(related),
            "ai_overview_present": ai_overview,
            # F7: the AIO answer body (not just citation titles) so brand/competitor
            # name detection works in ai_citation_track.
            "ai_overview_text": " ".join(ai_overview_text_parts),
            "ai_overview_citations": ai_overview_citations,
            "result_types": sorted(result_types),
        }

    # --- keyword data -----------------------------------------------------
    def keyword_overview(self, keywords: list[str], *, location_code: int = _LOCATION_US, language_code: str = _LANGUAGE_EN) -> list[dict[str, Any]]:
        result = self._post(
            "/v3/dataforseo_labs/google/keyword_overview/live",
            [{"keywords": keywords, "location_code": location_code, "language_code": language_code}],
        )
        out = []
        for it in _dicts(_result_items(result)):
            ki = _dict(it.get("keyword_info"))
            kp = _dict(it.get("keyword_properties"))
            si = _dict(it.get("search_intent_info"))
            out.append({
                "keyword": it.get("keyword"),
                "search_volume": ki.get("search_volume"),
                "keyword_difficulty": kp.get("keyword_difficulty"),
                "intent": si.get("main_intent"),
            })
        return out

    def ranked_keywords(self, domain: str, *, limit: int = 100, location_code: int = _LOCATION_US, language_code: str = _LANGUAGE_EN) -> list[dict[str, Any]]:
        result = self._post(
            "/v3/dataforseo_labs/google/ranked_keywords/live",
            [{"target": domain, "location_code": location_code, "language_code": language_code, "limit": limit}],
        )
        out = []
        for it in _dicts(_result_items(result)):
            kd = _dict(it.get("keyword_data"))
            serp_el = _dict(_dict(it.get("ranked_serp_element")).get("serp_item"))
            out.append({
                "keyword": kd.get("keyword"),
                "search_volume": _dict(kd.get("keyword_info")).get("search_volume"),
                "position": serp_el.get("rank_absolute"),
            })
        return out

    def probe(self) -> bool:
        """Cheap reachability check: the account user_data endpoint."""
        self._unwrap(self._raw_request("GET", "/v3/appendix/user_data"))
        return True


def _shape_error(part: str) -> ApiError:
    return ApiError(ErrorCode.UPSTREAM_ERROR, f"DataForSEO returned an unexpected response shape ({part}).")


def _status_code(value: Any) -> int | None:
    """Never copy untrusted status text or composite values into an error."""
    return value if type(value) is int else None


def _result_items(result: list[Any]) -> list[Any]:
    """Require observed items, or an explicit zero item count for a null list."""
    if not result:
        return []
    first = result[0]
    if not isinstance(first, dict):
        raise _shape_error("result item")
    items = first.get("items")
    if items is None and type(first.get("items_count")) is int and first["items_count"] == 0:
        return []
    if not isinstance(items, list):
        raise _shape_error("items")
    if items and not any(isinstance(item, dict) for item in items):
        raise _shape_error("items entries")
    return items


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in _list(value) if isinstance(v, dict)]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _dedup(items: list[str]) -> list[str]:
    """Order-preserving de-dup (tester F5: raw per-seed related/paa listed each
    term twice when the SERP returned duplicate blocks)."""
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _extract_references(refs: Any) -> list[dict[str, Any]]:
    """Normalize an AI-overview references list to {url, title, domain}. Defensive
    against DataForSEO shape drift."""
    out: list[dict[str, Any]] = []
    for ref in _dicts(refs):
        url = _str(ref.get("url"))
        if not url:
            continue
        try:
            parsed = urlparse(url)
            port = parsed.port
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None or port == 0:
                continue
        except (ValueError, UnicodeError):
            continue
        out.append({"url": url, "title": _str(ref.get("title")), "domain": _str(ref.get("domain")) or parsed.netloc.lower()})
    return out


def build_dataforseo_client(config: Config) -> DataForSEOClient | None:
    """Construct a client when login+password are configured, else None."""
    login = getattr(config, "dataforseo_login", None)
    password = getattr(config, "dataforseo_password", None)
    if not login or not password:
        return None
    return DataForSEOClient(login, password)
