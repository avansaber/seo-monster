"""Generic HTTP client for the technical-SEO tools.

Five v0.3.0 tools fetch arbitrary public URLs: ``inspect_meta``,
``check_canonical``, ``mixed_content_check``, ``redirect_chain_audit``,
``robots_txt_validate``, ``sitemap_validate``, ``sitemap_health``. Rather
than each tool wiring its own ``urllib`` boilerplate, they all share this
client. Tests monkeypatch the single ``_http_request_raw`` seam.

Design decisions:

- We do **not** auto-follow redirects at the urllib layer. We follow them
  ourselves so the caller (``redirect_chain_audit``) can see every hop. Other
  tools opt in via ``follow_redirects=True`` and just get the final response.
- We cap the response body at ``max_bytes`` (default 10 MiB). A typical SEO
  page is well under that; capping keeps a misconfigured server from blowing
  out memory.
- We cap the redirect chain at ``max_redirects`` (default 10) and surface a
  loop as ``UPSTREAM_ERROR`` so the tool layer can describe it.
- We send a clearly-identified User-Agent. Crawlers that hide their identity
  are widely blocked, and SEO tools should announce themselves.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from ..errors import ErrorCode
from .errors import ApiError


_USER_AGENT = "SEOMonster/0.8.1 (+https://seomonster.avansaber.com)"
_DEFAULT_TIMEOUT = 20
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_DEFAULT_MAX_REDIRECTS = 10


@dataclass
class RedirectHop:
    """One step in a redirect chain."""

    url: str
    status: int
    location: str | None
    elapsed_ms: int


@dataclass
class HttpResponse:
    """Result of a single HTTP request (or a followed redirect chain).

    ``redirect_chain`` is the ordered list of hops *before* the final
    response; the final response itself is described by ``status``,
    ``headers``, ``body_text``, and ``final_url``.
    """

    status: int
    headers: dict[str, str]
    body_bytes: bytes
    final_url: str
    redirect_chain: list[RedirectHop] = field(default_factory=list)

    @property
    def body_text(self) -> str:
        """Best-effort decode. Falls back to latin-1 so we never raise."""
        ctype = self.headers.get("content-type", "")
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        try:
            return self.body_bytes.decode(charset, errors="replace")
        except LookupError:
            return self.body_bytes.decode("utf-8", errors="replace")


class HttpClient:
    """Stdlib-only HTTP client with a thin testable seam."""

    def __init__(self, user_agent: str = _USER_AGENT, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._user_agent = user_agent
        self._timeout = timeout

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        follow_redirects: bool = True,
        max_redirects: int = _DEFAULT_MAX_REDIRECTS,
        max_bytes: int = _DEFAULT_MAX_BYTES,
        extra_headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Fetch a URL. Follow redirects manually so the caller can inspect
        every hop. Raise ``ApiError`` for unrecoverable failures."""
        chain: list[RedirectHop] = []
        current = url
        seen: set[str] = set()
        for _ in range(max_redirects + 1):
            if current in seen:
                raise ApiError(
                    ErrorCode.UPSTREAM_ERROR,
                    f"Redirect loop detected at {current!r}.",
                    details={"chain": [hop.url for hop in chain]},
                )
            seen.add(current)
            t0 = time.monotonic()
            status, headers, body = self._http_request_raw(
                method, current, max_bytes=max_bytes, extra_headers=extra_headers
            )
            elapsed = int((time.monotonic() - t0) * 1000)
            if follow_redirects and 300 <= status < 400 and "location" in headers:
                location = headers["location"]
                resolved = urllib.parse.urljoin(current, location)
                chain.append(RedirectHop(url=current, status=status, location=resolved, elapsed_ms=elapsed))
                current = resolved
                continue
            return HttpResponse(
                status=status,
                headers=headers,
                body_bytes=body,
                final_url=current,
                redirect_chain=chain,
            )
        raise ApiError(
            ErrorCode.UPSTREAM_ERROR,
            f"Exceeded max_redirects={max_redirects} starting at {url!r}.",
            details={"chain": [hop.url for hop in chain]},
        )

    def _http_request_raw(
        self,
        method: str,
        url: str,
        *,
        max_bytes: int,
        extra_headers: dict[str, str] | None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Perform one HTTP call without redirect following. Returns
        ``(status, headers_lowercase_keys, body_bytes)``. This is the single
        seam tests monkeypatch.

        Status codes >= 400 still return normally here; the caller decides
        whether a 404 is fatal (most tools) or expected (sitemap_health).
        """
        if not url.startswith(("http://", "https://")):
            raise ApiError(
                ErrorCode.INVALID_INPUT,
                f"URL must start with http:// or https://, got {url!r}.",
            )
        request = urllib.request.Request(url, method=method)
        request.add_header("User-Agent", self._user_agent)
        request.add_header("Accept", "*/*")
        if extra_headers:
            for k, v in extra_headers.items():
                request.add_header(k, v)
        # We need our own no-follow opener; the default opener handles 3xx.
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=self._timeout) as resp:
                return self._read_response(resp, max_bytes)
        except urllib.error.HTTPError as exc:
            # 3xx without a Location, or any 4xx/5xx, lands here. We still
            # want the headers + body so the caller can reason about them.
            if 300 <= exc.code < 400:
                # No location header; treat as terminal.
                return self._read_response(exc, max_bytes)
            return self._read_response(exc, max_bytes)
        except urllib.error.URLError as exc:
            raise ApiError(
                ErrorCode.UPSTREAM_ERROR,
                f"HTTP request to {url!r} failed: {exc.reason}",
            ) from exc
        except TimeoutError as exc:
            raise ApiError(
                ErrorCode.UPSTREAM_ERROR,
                f"HTTP request to {url!r} timed out after {self._timeout}s.",
            ) from exc

    @staticmethod
    def _read_response(resp: Any, max_bytes: int) -> tuple[int, dict[str, str], bytes]:
        status = getattr(resp, "status", None) or getattr(resp, "code", 0)
        # urllib gives us a list of (name, value) pairs; lowercase keys.
        headers: dict[str, str] = {}
        for k, v in (resp.headers.items() if hasattr(resp, "headers") else []):
            headers[k.lower()] = v
        body = resp.read(max_bytes + 1) if hasattr(resp, "read") else b""
        if len(body) > max_bytes:
            body = body[:max_bytes]
        return status, headers, body


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disables urllib's automatic redirect handling so we can see every hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def build_http_client() -> HttpClient:
    """Builder used by the server's ``_CLIENT_BUILDERS`` registry. No config
    needed; the HttpClient is fully self-contained."""
    return HttpClient()
