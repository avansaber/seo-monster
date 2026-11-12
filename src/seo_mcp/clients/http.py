"""Generic HTTP client for the technical-SEO tools.

Five v0.3.0 tools fetch arbitrary public URLs: ``inspect_meta``,
``check_canonical``, ``mixed_content_check``, ``redirect_chain_audit``,
``robots_txt_validate``, ``sitemap_validate``, ``sitemap_health``. Rather
than each tool wiring its own ``urllib`` boilerplate, they all share this
client. Tests can replace the pinned transport and DNS resolution seams.

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
- Every URL and redirect hop must resolve only to public addresses, so a
  website, provider result or redirect cannot reach loopback, private, link-local or cloud
  metadata hosts. Connections use validated numeric addresses without a second
  lookup, preserving the original Host header, TLS hostname checks and SNI.
  Fetches refuse proxies, credential headers and methods other than GET/HEAD.
  Private and local audits are unsupported. NAT64/DNS64-only access using
  the well-known 64:ff9b::/96 prefix is also unsupported.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
import string
import time
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


def safe_public_fetch_error(exc: ApiError) -> str:
    """Return only known, fixed public-fetch messages to another interface."""
    allowed = {
        "Public fetches require a direct connection without proxies.",
        "Public fetches support only GET and HEAD.",
        "Public fetches do not accept credentials or connection-header overrides.",
        "Refusing an invalid public HTTP URL.",
        "Could not resolve the public HTTP host.",
        "Refusing a host with non-public addresses.",
        "Public HTTP connection or response failed.",
        "Public fetch contains invalid URL or header characters.",
        "HTTP response contains an invalid redirect URL.",
        "Redirect loop detected.",
    }
    return exc.message if exc.message in allowed else "Public website fetch failed."


def _require_direct_connection() -> None:
    """Refuse configured proxies before resolving a website target."""
    proxies = urllib.request.getproxies()
    if any(proxies.get(key) for key in ("http", "https", "all", "socks")):
        raise ApiError(ErrorCode.INVALID_INPUT, "Public fetches require a direct connection without proxies.")


def _validate_request(method: str, extra_headers: dict[str, str] | None) -> None:
    if method not in ("GET", "HEAD"):
        raise ApiError(ErrorCode.INVALID_INPUT, "Public fetches support only GET and HEAD.")
    forbidden = {
        "host", "authorization", "proxy-authorization", "cookie", "content-length", "transfer-encoding",
        "connection", "upgrade", "expect", "te", "keep-alive", "proxy-connection", "user-agent", "accept",
        "accept-encoding",
    }
    if extra_headers and any(isinstance(key, str) and key.lower() in forbidden for key in extra_headers):
        raise ApiError(ErrorCode.INVALID_INPUT, "Public fetches do not accept credentials or connection-header overrides.")
    if extra_headers and any(
        not isinstance(key, str) or re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key) is None
        or not isinstance(value, str)
        or any((ord(ch) < 32 and ch != "\t") or ord(ch) == 127 or ord(ch) > 255 for ch in value)
        for key, value in extra_headers.items()
    ):
        raise ApiError(ErrorCode.INVALID_INPUT, "Public fetch contains invalid URL or header characters.")


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


def _safe_chain_details(chain: list[RedirectHop]) -> dict[str, Any]:
    """Keep hop order and status without exposing URL parts from a page."""
    return {
        "chain": [f"{urllib.parse.urlsplit(hop.url).scheme}://[redacted]" for hop in chain],
        "statuses": [hop.status for hop in chain],
    }


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
        public_only: bool = True,
    ) -> HttpResponse:
        """Fetch a URL. Follow redirects manually so the caller can inspect
        every hop. Raise ``ApiError`` for unrecoverable failures.

        The retained ``public_only`` argument cannot disable the public policy.
        """
        _validate_request(method, extra_headers)
        _require_direct_connection()
        chain: list[RedirectHop] = []
        current = url
        seen: set[str] = set()
        for _ in range(max_redirects + 1):
            if current in seen:
                raise ApiError(
                    ErrorCode.UPSTREAM_ERROR,
                    "Redirect loop detected.",
                    details=_safe_chain_details(chain),
                )
            seen.add(current)
            try:
                target = _require_public_url(current)
                t0 = time.monotonic()
                status, headers, body = self._http_request_public(
                    method, current, max_bytes=max_bytes, extra_headers=extra_headers, target=target
                )
            except ApiError as exc:
                if chain:
                    raise ApiError(exc.code, safe_public_fetch_error(exc), details=_safe_chain_details(chain)) from exc
                raise
            elapsed = int((time.monotonic() - t0) * 1000)
            if follow_redirects and status in (301, 302, 303, 307, 308) and "location" in headers:
                location = headers["location"]
                try:
                    resolved = urllib.parse.urljoin(current, location)
                except (ValueError, UnicodeError) as exc:
                    raise ApiError(
                        ErrorCode.UPSTREAM_ERROR,
                        "HTTP response contains an invalid redirect URL.",
                        details=_safe_chain_details(chain),
                    ) from exc
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
            f"Exceeded max_redirects={max_redirects}.",
            details=_safe_chain_details(chain),
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
        _validate_request(method, extra_headers)
        _require_direct_connection()
        headers = {"User-Agent": self._user_agent, "Accept": "*/*"}
        headers.update(extra_headers or {})
        connection = _PublicConnection(target, self._timeout)
        try:
            parts = urllib.parse.urlsplit(url)
            path = urllib.parse.quote(parts.path or "/", safe=string.punctuation)
            query = urllib.parse.quote(parts.query, safe=string.punctuation)
            if query:
                path += "?" + query
            connection.request(method, path, headers=headers)
            with connection.getresponse() as response:
                return self._read_response(response, max_bytes)
        except (OSError, http.client.HTTPException) as exc:
            raise ApiError(ErrorCode.UPSTREAM_ERROR, "Public HTTP connection or response failed.") from exc
        except (ValueError, UnicodeError) as exc:
            raise ApiError(ErrorCode.INVALID_INPUT, "Public fetch contains invalid URL or header characters.") from exc
        finally:
            connection.close()

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
    if isinstance(ip, ipaddress.IPv4Address) and (
        ip in ipaddress.ip_network("192.0.0.0/24") or ip == ipaddress.ip_address("168.63.129.16")
    ):
        return False
    # Transition routes can embed private IPv4 destinations even when an
    # older Python address table labels the IPv6 value as global.
    if isinstance(ip, ipaddress.IPv6Address) and (
        ip.sixtofour is not None
        or ip.teredo is not None
        or ip in ipaddress.ip_network("64:ff9b::/96")
        or ip in ipaddress.ip_network("64:ff9b:1::/48")
        or ip in ipaddress.ip_network("::/96")
        or ip.is_site_local
    ):
        return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def _require_public_url(url: str) -> _PublicTarget:
    """Parse and resolve one hop, retaining validated addresses for connection."""
    try:
        if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
            raise ValueError("Invalid URL characters")
        url.encode("utf-8")
        parts = urllib.parse.urlsplit(url)
        port = parts.port
        host = parts.hostname
        if parts.scheme not in ("http", "https") or not host or parts.username is not None or port == 0 or "%" in host or host.endswith(".."):
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
    # DNS retains the absolute name; HTTP Host and TLS use the certificate name.
    connection_host = host.removesuffix(".")
    if not connection_host:
        raise ApiError(ErrorCode.INVALID_INPUT, "Refusing an invalid public HTTP URL.")
    return _PublicTarget(connection_host, port, secure, tuple(sorted(addresses)))


def build_http_client() -> HttpClient:
    """Builder used by the server's ``_CLIENT_BUILDERS`` registry. No config
    needed; the HttpClient is fully self-contained."""
    return HttpClient()
