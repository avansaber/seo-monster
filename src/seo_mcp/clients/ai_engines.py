"""AI answer-engine client (roadmap F4) for ai_citation_track.

This is the one place the server must call OTHER LLMs (the host is a single
model; it cannot query Perplexity/Gemini/etc.). Each engine is queried with web
search enabled and its answer + cited sources are normalized to
``{answer_text, citations: [{url, title, domain}]}``. Google AI Overviews has no
API and is handled separately by the tool via the DataForSEO SERP client.

IMPORTANT (dev/tester split): the per-engine request/response shapes below are
best-effort against the documented 2026 APIs. They are exercised in unit tests
via the per-engine ``_raw_*`` seam with canned payloads (normalization is what
we verify); the LIVE request shapes, model ids, and auth must be confirmed by
the tester with real keys -- these are paid, non-deterministic endpoints that
mocks cannot validate. Model ids are module constants so they are easy to bump.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from ..config import Config
from ..errors import ErrorCode
from .errors import ApiError, map_http_status

_TIMEOUT_SECONDS = 60

# Model ids per engine (bump as providers release; tester confirms live).
_MODEL_PERPLEXITY = "sonar"
_MODEL_OPENAI = "gpt-4o-search-preview"
_MODEL_ANTHROPIC = "claude-sonnet-4-6"
_MODEL_GEMINI = "gemini-2.5-flash"

ENGINES = ("perplexity", "openai", "anthropic", "gemini")


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except (ValueError, AttributeError):
        return ""


class AiEnginesClient:
    def __init__(self, keys: dict[str, str]) -> None:
        # only retain engines with a non-empty key
        self._keys = {k: v for k, v in keys.items() if v}

    def available_engines(self) -> list[str]:
        return sorted(self._keys)

    def probe(self) -> bool:
        # Configured == probe ok. We deliberately do NOT make a live (paid) call
        # here; live reachability is the tester's job.
        return bool(self._keys)

    def query(self, engine: str, prompt: str) -> dict[str, Any]:
        if engine not in self._keys:
            raise ApiError(ErrorCode.AUTH_MISSING, f"No API key configured for engine {engine!r}.")
        raw = getattr(self, f"_raw_{engine}")(prompt)
        return getattr(self, f"_normalize_{engine}")(raw)

    # --- shared HTTP seam -------------------------------------------------
    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any], *, service: str) -> dict[str, Any]:
        request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
        for k, v in headers.items():
            request.add_header(k, v)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise map_http_status(exc.code, exc.read().decode("utf-8", errors="replace"), service=service) from exc
        except urllib.error.URLError as exc:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, f"{service} request failed: {exc.reason}") from exc

    # --- per-engine raw calls (the live seam; monkeypatched in tests) -----
    def _raw_perplexity(self, prompt: str) -> dict[str, Any]:
        return self._post(
            "https://api.perplexity.ai/chat/completions",
            {"Authorization": f"Bearer {self._keys['perplexity']}"},
            {"model": _MODEL_PERPLEXITY, "messages": [{"role": "user", "content": prompt}]},
            service="Perplexity",
        )

    def _raw_openai(self, prompt: str) -> dict[str, Any]:
        return self._post(
            "https://api.openai.com/v1/responses",
            {"Authorization": f"Bearer {self._keys['openai']}"},
            {"model": _MODEL_OPENAI, "input": prompt, "tools": [{"type": "web_search"}]},
            service="OpenAI",
        )

    def _raw_anthropic(self, prompt: str) -> dict[str, Any]:
        return self._post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": self._keys["anthropic"], "anthropic-version": "2023-06-01"},
            {"model": _MODEL_ANTHROPIC, "max_tokens": 1024,
             "messages": [{"role": "user", "content": prompt}],
             "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]},
            service="Anthropic",
        )

    def _raw_gemini(self, prompt: str) -> dict[str, Any]:
        return self._post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{_MODEL_GEMINI}:generateContent?key={self._keys['gemini']}",
            {},
            {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]},
            service="Gemini",
        )

    # --- per-engine normalizers ------------------------------------------
    @staticmethod
    def _cite(url: str | None, title: str | None = None) -> dict[str, Any] | None:
        if not url:
            return None
        return {"url": url, "title": title, "domain": _domain(url)}

    def _normalize_perplexity(self, raw: dict[str, Any]) -> dict[str, Any]:
        choices = raw.get("choices") or []
        text = (choices[0].get("message", {}).get("content") if choices else "") or ""
        cites = [c for sr in (raw.get("search_results") or []) if (c := self._cite(sr.get("url"), sr.get("title")))]
        return {"answer_text": text, "citations": cites}

    def _normalize_openai(self, raw: dict[str, Any]) -> dict[str, Any]:
        text_parts: list[str] = []
        cites: list[dict[str, Any]] = []
        for item in raw.get("output") or []:
            for block in item.get("content") or []:
                if block.get("type") in ("output_text", "text"):
                    text_parts.append(block.get("text", ""))
                for ann in block.get("annotations") or []:
                    if ann.get("type") == "url_citation":
                        c = self._cite(ann.get("url"), ann.get("title"))
                        if c:
                            cites.append(c)
        return {"answer_text": " ".join(text_parts), "citations": cites}

    def _normalize_anthropic(self, raw: dict[str, Any]) -> dict[str, Any]:
        text_parts: list[str] = []
        cites: list[dict[str, Any]] = []
        for block in raw.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
                for cit in block.get("citations") or []:
                    c = self._cite(cit.get("url"), cit.get("title"))
                    if c:
                        cites.append(c)
        return {"answer_text": " ".join(text_parts), "citations": cites}

    def _normalize_gemini(self, raw: dict[str, Any]) -> dict[str, Any]:
        candidates = raw.get("candidates") or []
        cand = candidates[0] if candidates else {}
        text_parts = [p.get("text", "") for p in (cand.get("content", {}).get("parts") or [])]
        cites: list[dict[str, Any]] = []
        for chunk in (cand.get("groundingMetadata", {}).get("groundingChunks") or []):
            web = chunk.get("web") or {}
            c = self._cite(web.get("uri"), web.get("title"))
            if c:
                cites.append(c)
        return {"answer_text": " ".join(text_parts), "citations": cites}


def build_ai_engines_client(config: Config) -> AiEnginesClient | None:
    keys = {
        "perplexity": getattr(config, "perplexity_api_key", None),
        "openai": getattr(config, "openai_api_key", None),
        "anthropic": getattr(config, "anthropic_api_key", None),
        "gemini": getattr(config, "gemini_api_key", None),
    }
    if not any(keys.values()):
        return None
    return AiEnginesClient(keys)
