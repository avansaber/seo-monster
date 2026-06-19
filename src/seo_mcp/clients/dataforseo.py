"""DataForSEO client (roadmap F2/F3) over stdlib urllib. No HTTP dependency.

The single optional paid vendor (design doc §0.2): one Basic-auth key unlocks
SERP (organic + PAA + related + AI-overview detection), keyword volume /
difficulty / intent, and ranked-keywords (for competitor keyword gaps). The
network seam is ``_raw_request`` (does the HTTP, returns parsed JSON, or raises
ApiError); tests monkeypatch it to inject canned task envelopes without urllib.

DataForSEO wraps every response as ``{status_code, tasks:[{status_code,
result:[...]}]}``; ``_unwrap`` enforces the 20000 success codes and returns the
first task's result list. Method response shapes are parsed defensively (live
shape drift is the tester's catch, not a unit-test concern).
"""

from __future__ import annotations

import base64
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

    # --- network seam -----------------------------------------------------
    def _raw_request(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        url = f"{API_BASE}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Basic {self._auth}")
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise map_http_status(exc.code, body_text, service="DataForSEO") from exc
        except urllib.error.URLError as exc:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, f"DataForSEO request failed: {exc.reason}") from exc

    def _unwrap(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if payload.get("status_code") != _SUCCESS:
            raise ApiError(
                ErrorCode.UPSTREAM_ERROR,
                f"DataForSEO: {payload.get('status_message', 'error')} ({payload.get('status_code')}).",
                details={"status_code": payload.get("status_code")},
            )
        tasks = payload.get("tasks") or []
        if not tasks:
            return []
        t0 = tasks[0]
        if t0.get("status_code") != _SUCCESS:
            raise ApiError(
                ErrorCode.UPSTREAM_ERROR,
                f"DataForSEO task: {t0.get('status_message', 'error')} ({t0.get('status_code')}).",
                details={"status_code": t0.get("status_code")},
            )
        return t0.get("result") or []

    def _post(self, path: str, body: Any) -> list[dict[str, Any]]:
        return self._unwrap(self._raw_request("POST", path, body))

    # --- SERP -------------------------------------------------------------
    def serp(self, keyword: str, *, location_code: int = _LOCATION_US, language_code: str = _LANGUAGE_EN, depth: int = 20) -> dict[str, Any]:
        result = self._post(
            "/v3/serp/google/organic/live/advanced",
            [{"keyword": keyword, "location_code": location_code, "language_code": language_code, "depth": depth}],
        )
        items = (result[0].get("items") if result else None) or []
        organic: list[dict[str, Any]] = []
        paa: list[str] = []
        related: list[str] = []
        ai_overview = False
        ai_overview_citations: list[dict[str, Any]] = []
        ai_overview_text_parts: list[str] = []
        result_types: set[str] = set()
        for it in items:
            t = it.get("type")
            if t:
                result_types.add(t)
            if t == "organic":
                organic.append({
                    "rank": it.get("rank_absolute"),
                    "url": it.get("url"),
                    "title": it.get("title"),
                    "domain": it.get("domain"),
                })
            elif t == "people_also_ask":
                for sub in it.get("items") or []:
                    q = sub.get("title")
                    if q:
                        paa.append(q)
            elif t == "related_searches":
                for q in it.get("items") or []:
                    if isinstance(q, str):
                        related.append(q)
            elif t == "ai_overview":
                ai_overview = True
                ai_overview_citations.extend(_extract_references(it.get("references")))
                if it.get("text"):
                    ai_overview_text_parts.append(str(it["text"]))
                for sub in it.get("items") or []:
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
        items = (result[0].get("items") if result else None) or []
        out = []
        for it in items:
            ki = it.get("keyword_info") or {}
            kp = it.get("keyword_properties") or {}
            si = it.get("search_intent_info") or {}
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
        items = (result[0].get("items") if result else None) or []
        out = []
        for it in items:
            kd = it.get("keyword_data") or {}
            serp_el = (it.get("ranked_serp_element") or {}).get("serp_item") or {}
            out.append({
                "keyword": kd.get("keyword"),
                "search_volume": (kd.get("keyword_info") or {}).get("search_volume"),
                "position": serp_el.get("rank_absolute"),
            })
        return out

    def probe(self) -> bool:
        """Cheap reachability check: the account user_data endpoint."""
        self._unwrap(self._raw_request("GET", "/v3/appendix/user_data"))
        return True


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
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        url = ref.get("url")
        if not url:
            continue
        out.append({"url": url, "title": ref.get("title"), "domain": ref.get("domain") or urlparse(url).netloc.lower()})
    return out


def build_dataforseo_client(config: Config) -> DataForSEOClient | None:
    """Construct a client when login+password are configured, else None."""
    login = getattr(config, "dataforseo_login", None)
    password = getattr(config, "dataforseo_password", None)
    if not login or not password:
        return None
    return DataForSEOClient(login, password)
