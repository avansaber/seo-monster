"""IndexNow client (api.indexnow.org) over stdlib urllib.

IndexNow is a multi-engine URL-submission protocol launched by Microsoft Bing
and Yandex in 2021. As of 2026 it is supported by Bing, Yandex, Naver,
Seznam.cz, and Yep. **Google does not support IndexNow**, so this client
complements (does not replace) ``gsc_request_indexing`` for the Google side.

The protocol has two shapes:

- Single URL: ``GET https://api.indexnow.org/indexnow?url=URL&key=KEY``.
- Batch: ``POST https://api.indexnow.org/indexnow`` with JSON body
  ``{"host": ..., "key": ..., "keyLocation": ..., "urlList": [...]}``.

Search engines verify ownership the first time they see a key by fetching
``https://<host>/<key>.txt`` and expecting the body to equal the key. The
user is responsible for hosting that file; we cannot create it.

Response semantics (per IndexNow spec):
- 200: accepted.
- 202: received, key verification pending.
- 400: malformed request.
- 403: key invalid or key file not at the expected location.
- 422: URL list contains URLs not matching the host.
- 429: rate limited.

The seam for tests is ``_http_request``, mirroring our PSI and Cloudflare
clients. Tests monkeypatch it to inject canned responses without touching
urllib.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..config import Config
from ..errors import ErrorCode
from .errors import ApiError


_ENDPOINT = "https://api.indexnow.org/indexnow"
_TIMEOUT_SECONDS = 30


def _host_of(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    return parsed.hostname


class IndexNowClient:
    def __init__(self, key: str, key_location: str | None = None) -> None:
        self._key = key
        self._key_location = key_location

    def _key_location_for_host(self, host: str) -> str:
        """IndexNow requires keyLocation to live on the SAME host as the
        submitted URLs (else HTTP 422). Honor an explicitly-configured
        key_location only when it is on this host (supports a non-root key file
        for the configured site); otherwise derive the conventional host-root
        location so any host works (FEEDBACK §25)."""
        if self._key_location and _host_of(self._key_location) == host:
            return self._key_location
        return f"https://{host}/{self._key}.txt"

    def submit(self, url: str) -> dict[str, Any]:
        """Submit a single URL via the GET form. Returns the parsed response
        envelope (most engines reply with an empty body and a status code; we
        normalize to ``{"accepted": True}`` so the tool layer has a shape to
        return)."""
        host = _host_of(url)
        params = {"url": url, "key": self._key}
        if host:
            params["keyLocation"] = self._key_location_for_host(host)
        qs = urllib.parse.urlencode(params)
        return self._http_request("GET", f"{_ENDPOINT}?{qs}", None)

    def bulk_submit(self, urls: list[str]) -> dict[str, Any]:
        """Submit multiple URLs via the POST form. All URLs must share the
        same host (IndexNow rejects mixed-host batches with HTTP 422)."""
        if not urls:
            raise ApiError(ErrorCode.INVALID_INPUT, "IndexNow bulk submit needs at least one URL.")
        host = _host_of(urls[0])
        if host is None:
            raise ApiError(
                ErrorCode.INVALID_INPUT,
                f"Could not derive host from URL: {urls[0]!r}",
            )
        for u in urls[1:]:
            if _host_of(u) != host:
                raise ApiError(
                    ErrorCode.INVALID_INPUT,
                    "IndexNow requires all URLs in a batch to share the same host. "
                    f"Mixed hosts found: {host} vs {_host_of(u)}.",
                )
        body: dict[str, Any] = {
            "host": host,
            "key": self._key,
            "keyLocation": self._key_location_for_host(host),
            "urlList": list(urls),
        }
        return self._http_request("POST", _ENDPOINT, body)

    def _http_request(self, method: str, url: str, body: dict[str, Any] | None) -> dict[str, Any]:
        """Perform the HTTP call. Raises ApiError on failure. This is the
        single seam tests monkeypatch."""
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json; charset=utf-8")
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
                status = resp.status
                # Most engines reply with an empty body. Treat 200 and 202 as
                # success; everything else falls through to the error path
                # (though urlopen would raise HTTPError for >=400 anyway).
                return {"accepted": True, "status": status}
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise self._map_error(exc.code, body_text) from exc
        except urllib.error.URLError as exc:
            raise ApiError(
                ErrorCode.UPSTREAM_ERROR,
                f"IndexNow request failed: {exc.reason}",
            ) from exc

    @staticmethod
    def _map_error(status: int, body: str) -> ApiError:
        if status == 400:
            return ApiError(
                ErrorCode.INVALID_INPUT,
                "IndexNow rejected the request as malformed (HTTP 400).",
                details={"status": 400, "body": body[:500]},
            )
        if status == 403:
            return ApiError(
                ErrorCode.AUTH_INVALID,
                "IndexNow could not verify the key.",
                remediation=(
                    "Host the key file at https://<your-host>/<key>.txt with "
                    "the key string as the file body, then retry. Or pass an "
                    "explicit keyLocation when submitting."
                ),
                details={"status": 403, "body": body[:500]},
            )
        if status == 422:
            return ApiError(
                ErrorCode.INVALID_INPUT,
                "IndexNow says the URL list contains URLs that do not match the host.",
                details={"status": 422, "body": body[:500]},
            )
        if status == 429:
            return ApiError(
                ErrorCode.RATE_LIMITED,
                "IndexNow rate limit hit (HTTP 429).",
                remediation="Wait before submitting again; the spec rate-limits per host.",
                details={"status": 429, "body": body[:500]},
            )
        return ApiError(
            ErrorCode.UPSTREAM_ERROR,
            f"IndexNow returned HTTP {status}.",
            details={"status": status, "body": body[:500]},
        )

    def probe(self) -> bool:
        """Cheap reachability check. Submit a clearly-malformed request (no
        URL parameter) and expect a 400. Any HTTP response proves the endpoint
        is reachable; only transport failures count as unreachable."""
        try:
            self._http_request("GET", f"{_ENDPOINT}?key={urllib.parse.quote(self._key)}", None)
        except ApiError as exc:
            return exc.code != ErrorCode.UPSTREAM_ERROR
        return True


def build_indexnow_client(config: Config) -> IndexNowClient | None:
    """Construct an IndexNowClient when a key is configured, else None
    (the tools then return AUTH_MISSING)."""
    if not config.indexnow_key:
        return None
    return IndexNowClient(config.indexnow_key, key_location=config.indexnow_key_location)
