"""Chrome UX Report (CrUX) History API client.

CrUX exposes 25 weeks of trailing field data for Core Web Vitals at p75. The
History endpoint differs from the standard query endpoint by returning a time
series rather than a single window. Same Google API key works for both this
and PSI; we reuse ``PSI_API_KEY``.

Endpoint: ``POST https://chromeuxreport.googleapis.com/v1/records:queryHistoryRecord?key=API_KEY``
Request body (per Google docs):

    {"origin": "https://example.com", "formFactor": "PHONE",
     "metrics": ["largest_contentful_paint", "interaction_to_next_paint", ...]}

OR ``url`` instead of ``origin`` for page-level data.

Failure mapping:
- 404 with "chrome ux report data" -> not an error from the user's POV; we
  surface it as an empty result. The page or origin simply lacks field data.
- 400 with "insufficient data" -> same: empty result.
- Other 4xx -> ApiError per ``map_http_status``.
- Network errors -> UPSTREAM_ERROR.
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


_ENDPOINT = "https://chromeuxreport.googleapis.com/v1/records:queryHistoryRecord"
_TIMEOUT_SECONDS = 30


class CruxHistoryClient:
    def __init__(self, api_key: str | None) -> None:
        self._key = api_key

    def query(
        self,
        *,
        url: str | None = None,
        origin: str | None = None,
        form_factor: str | None = None,
        metrics: list[str] | None = None,
    ) -> dict[str, Any]:
        if not (url or origin):
            raise ApiError(ErrorCode.INVALID_INPUT, "crux_history needs either url or origin.")
        if url and origin:
            raise ApiError(ErrorCode.INVALID_INPUT, "crux_history accepts url OR origin, not both.")
        body: dict[str, Any] = {}
        if url:
            body["url"] = url
        else:
            body["origin"] = origin
        if form_factor:
            body["formFactor"] = form_factor.upper()
        if metrics:
            body["metrics"] = list(metrics)
        return self._http_post(body)

    def _http_post(self, body: dict[str, Any]) -> dict[str, Any]:
        """Perform the POST and return parsed JSON. Raises ApiError except
        when the failure means "no data" (404 / 400 insufficient data), which
        is mapped to an empty success body. Single seam tests monkeypatch."""
        endpoint = _ENDPOINT + (f"?key={urllib.parse.quote(self._key)}" if self._key else "")
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(endpoint, data=data, method="POST")
        request.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            if self._is_no_data(exc.code, body_text):
                return {"record": None, "no_data": True}
            raise map_http_status(exc.code, body_text, service="CrUX History") from exc
        except urllib.error.URLError as exc:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, f"CrUX History request failed: {exc.reason}") from exc

    @staticmethod
    def _is_no_data(status: int, body: str) -> bool:
        lower = body.lower()
        if status == 404:
            return True
        if status == 400 and ("insufficient data" in lower or "chrome ux report" in lower):
            return True
        return False

    def probe(self) -> bool:
        """Cheap reachability check: post an obviously-invalid request and
        accept any structured HTTP response as proof the endpoint is up."""
        try:
            self._http_post({"origin": "https://example.invalid"})
        except ApiError as exc:
            return exc.code != ErrorCode.UPSTREAM_ERROR
        return True


def build_crux_client(config: Config) -> CruxHistoryClient:
    """Construct a CruxHistoryClient. Reuses ``PSI_API_KEY``. Always succeeds
    (the key is optional; CrUX accepts unauthenticated requests at a tighter
    rate limit)."""
    return CruxHistoryClient(api_key=config.psi_api_key)
