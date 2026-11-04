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
- ``public_only=True`` is for URLs a third party chose (SERP results): every
  hop must resolve only to public addresses, so a provider result or a
  competitor redirect cannot reach loopback, private, link-local or cloud
  metadata hosts. Connections use validated numeric addresses without a second
  lookup, preserving the original Host header, TLS hostname checks and SNI.
  This path refuses proxies, credential headers and methods other than GET/HEAD.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .. import __version__
from ..errors import ErrorCode
from .errors import ApiError

# Derived from the package version rather than hardcoded: this string drifted
# behind the real version for several releases because it was a literal, and a
# release-gate check does not cover it (see PLAN §11.4.2).
_USER_AGENT = f"SEOMonster/{__version__} (+https://www.avansaber.com/seomonster/)"
_DEFAULT_TIMEOUT = 20
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_DEFAULT_MAX_REDIRECTS = 10


@dataclass(frozen=True)
class _PublicTarget:
    host: str
    port: int
    secure: bool
    addresses: tuple[str, ...]


class _PublicConnection(http.client.HTTPConnection):
    """Connect to numeric public addresses while retaining the HTTP hostname."""

    def __init__(self, target: _PublicTarget, timeout: int) -> None:
        self.default_port = 443 if target.secure else 80
        super().__init__(target.host, target.port, timeout=timeout)
        self._target = target

    def connect(self) -> None:
        deadline = time.monotonic() + self.timeout
        last_error: OSError | None = None
        for address in self._target.addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Public connection timed out.")
            ip = ipaddress.ip_address(address)
            family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
            sockaddr = (str(ip), self.port, 0, 0) if ip.version == 6 else (str(ip), self.port)
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                sock.settimeout(remaining)
                sock.connect(sockaddr)
            except OSError as exc:
                sock.close()
                last_error = exc
                continue
            try:
                if self._target.secure:
                    context = ssl.create_default_context()
                    context.set_alpn_protocols(["http/1.1"])
                    sock = context.wrap_socket(sock, server_hostname=self.host)
            except BaseException:
                sock.close()
                raise
            self.sock = sock
            return
        raise OSError("All validated public addresses failed.") from last_error


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
        public_only: bool = False,
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
            target = _require_public_url(current) if public_only else None
            t0 = time.monotonic()
            if target is not None:
                status, headers, body = self._http_request_public(
                    method, current, max_bytes=max_bytes, extra_headers=extra_headers, target=target
                )
            else:
                status, headers, body = self._http_request_raw(
                    method, current, max_bytes=max_bytes, extra_headers=extra_headers
                )
            elapsed = int((time.monotonic() - t0) * 1000)
            if follow_redirects and 300 <= status < 400 and "location" in headers:
                location = headers["location"]
                try:
                    resolved = urllib.parse.urljoin(current, location)
                except (ValueError, UnicodeError) as exc:
                    raise ApiError(ErrorCode.UPSTREAM_ERROR, "HTTP response contains an invalid redirect URL.") from exc
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

    def _http_request_public(
        self,
        method: str,
        url: str,
        *,
        max_bytes: int,
        extra_headers: dict[str, str] | None,
        target: _PublicTarget,
    ) -> tuple[int, dict[str, str], bytes]:
        """Read one provider-selected URL with a pinned direct connection."""
        if method not in ("GET", "HEAD"):
            raise ApiError(ErrorCode.INVALID_INPUT, "Public fetches support only GET and HEAD.")
        if any(urllib.request.getproxies().get(key) for key in ("http", "https", "all")):
            raise ApiError(ErrorCode.INVALID_INPUT, "Public fetches require a direct connection without HTTP proxies.")
        forbidden = {"host", "authorization", "proxy-authorization", "cookie", "content-length", "transfer-encoding", "connection"}
        if extra_headers and any(key.lower() in forbidden for key in extra_headers):
            raise ApiError(ErrorCode.INVALID_INPUT, "Public fetches do not accept credentials or connection-header overrides.")
        headers = {"User-Agent": self._user_agent, "Accept": "*/*"}
        headers.update(extra_headers or {})
        connection = _PublicConnection(target, self._timeout)
        try:
            parts = urllib.parse.urlsplit(url)
            path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
            connection.request(method, path, headers=headers)
            with connection.getresponse() as response:
                return self._read_response(response, max_bytes)
        except (OSError, http.client.HTTPException) as exc:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, "Public HTTP connection or response failed.") from exc
        except (ValueError, UnicodeError) as exc:
            raise ApiError(ErrorCode.INVALID_INPUT, "Public fetch contains invalid URL or header characters.") from exc
        finally:
            connection.close()

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
        try:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.port == 0:
                raise ValueError("Invalid HTTP URL")
            request = urllib.request.Request(url, method=method)
        except (ValueError, UnicodeError) as exc:
            raise ApiError(ErrorCode.INVALID_INPUT, "URL must be a valid HTTP or HTTPS URL.") from exc
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


def _resolve_host(host: str, port: int) -> set[str]:
    """Every address ``host`` resolves to. Tests monkeypatch this seam."""
    return {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}


def _is_public_ip(address: str) -> bool:
    if "%" in address:
        return False
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("192.0.0.0/24"):
        return False
    # Transition routes can embed private IPv4 destinations even when an
    # older Python address table labels the IPv6 value as global.
    if isinstance(ip, ipaddress.IPv6Address) and (
        ip.sixtofour is not None or ip.teredo is not None or ip in ipaddress.ip_network("64:ff9b::/96")
    ):
        return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def _require_public_url(url: str) -> _PublicTarget:
    """Parse and resolve one hop, retaining validated addresses for connection."""
    try:
        if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
            raise ValueError("Invalid URL characters")
        parts = urllib.parse.urlsplit(url)
        port = parts.port
        host = parts.hostname
        if parts.scheme not in ("http", "https") or not host or parts.username is not None or port == 0 or "%" in host:
            raise ValueError("Invalid URL authority")
        host = host.encode("idna").decode("ascii")
    except (ValueError, UnicodeError) as exc:
        raise ApiError(ErrorCode.INVALID_INPUT, "Refusing an invalid public HTTP URL.") from exc
    secure = parts.scheme == "https"
    port = port or (443 if secure else 80)
    try:
        addresses = _resolve_host(host, port)
    except (OSError, UnicodeError) as exc:
        raise ApiError(ErrorCode.UPSTREAM_ERROR, "Could not resolve the public HTTP host.") from exc
    if not addresses or not all(_is_public_ip(a) for a in addresses):
        raise ApiError(ErrorCode.INVALID_INPUT, "Refusing a host with non-public addresses.")
    return _PublicTarget(host, port, secure, tuple(sorted(addresses)))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disables urllib's automatic redirect handling so we can see every hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def build_http_client() -> HttpClient:
    """Builder used by the server's ``_CLIENT_BUILDERS`` registry. No config
    needed; the HttpClient is fully self-contained."""
    return HttpClient()
