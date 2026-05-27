"""PageSpeed Insights client (PSI v5 runPagespeed) over stdlib urllib.

``PsiClient.analyze`` builds the request URL and returns the raw PSI JSON; the
tool shapes it. All HTTP goes through the single ``_http_get`` helper so tests
monkeypatch one method to inject canned JSON or raise an ``ApiError``.

The API key is optional: PSI serves anonymous requests (with tighter rate
limits), so this client never reports AUTH_MISSING.
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


_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
_DEFAULT_CATEGORIES = ("performance", "accessibility", "best-practices", "seo")
_TIMEOUT_SECONDS = 90


class PsiClient:
    def __init__(self, api_key: str | None = None) -> None:
        self._key = api_key

    def _build_url(self, url: str, strategy: str, categories: list[str]) -> str:
        # PSI expects the ``category`` parameter repeated, not comma-joined.
        parts = [
            f"url={urllib.parse.quote(url, safe='')}",
            f"strategy={urllib.parse.quote(strategy)}",
        ]
        for cat in categories:
            parts.append(f"category={urllib.parse.quote(cat)}")
        if self._key:
            parts.append(f"key={urllib.parse.quote(self._key)}")
        return f"{_ENDPOINT}?{'&'.join(parts)}"

    def _http_get(self, url: str) -> dict[str, Any]:
        """Perform the GET and return parsed JSON. Raises ApiError on failure.
        This is the single network seam tests monkeypatch."""
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise map_http_status(exc.code, body, service="PageSpeed Insights") from exc
        except urllib.error.URLError as exc:
            raise ApiError(
                ErrorCode.UPSTREAM_ERROR,
                f"PageSpeed Insights request failed: {exc.reason}",
            ) from exc

    def analyze(
        self,
        url: str,
        strategy: str = "mobile",
        categories: list[str] | None = None,
    ) -> dict[str, Any]:
        cats = list(categories) if categories else list(_DEFAULT_CATEGORIES)
        return self._http_get(self._build_url(url, strategy, cats))

    def probe(self) -> bool:
        """Cheap reachability check: hit the endpoint with the required ``url``
        param missing. Any HTTP response (PSI replies 400) proves the service is
        reachable; only a transport-level failure counts as unreachable. This
        does not run a Lighthouse analysis, so it stays cheap."""
        try:
            self._http_get(_ENDPOINT)
        except ApiError as exc:
            # A structured HTTP error means PSI answered -> reachable. A
            # transport failure is surfaced as UPSTREAM_ERROR -> unreachable.
            return exc.code != ErrorCode.UPSTREAM_ERROR
        return True


def build_psi_client(config: Config) -> PsiClient:
    """Construct a PsiClient. Always succeeds (key optional)."""
    return PsiClient(api_key=config.psi_api_key)
