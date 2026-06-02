"""Shared test fixtures and helpers.

The mocking boundary is the client layer: tests inject fake clients (objects
with a ``probe()`` method, and in later phases the data methods) rather than
patching the network. ``make_dispatcher`` wraps ``server.dispatch`` with a fixed
set of injected clients so a test can drive any tool offline.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Mapping

import pytest

from seo_mcp.config import Config, load_config


@pytest.fixture
def fake_env() -> Callable[..., dict[str, str]]:
    """Factory for an env mapping. Pass keyword overrides; only non-None values
    are included, so a test can express "this var is unset" by omitting it."""

    def _make(**overrides: str | None) -> dict[str, str]:
        return {k: v for k, v in overrides.items() if v is not None}

    return _make


@pytest.fixture
def make_config(fake_env) -> Callable[..., Config]:
    """Build a Config from an env override set, ignoring any real config file by
    pointing at a path that does not exist."""

    def _make(config_path: str = "/nonexistent/seo-mcp.toml", **env_overrides: str | None) -> Config:
        return load_config(env=fake_env(**env_overrides), config_path=config_path)

    return _make


class FakeProbeClient:
    """Minimal fake client exposing the probe() contract used by system_status.

    ``ok=True`` -> probe returns True; ``ok=False`` -> probe raises (to exercise
    the failure path). ``calls`` records how many times probe ran so tests can
    assert zero-call behavior."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls = 0

    def probe(self) -> bool:
        self.calls += 1
        if not self.ok:
            raise RuntimeError("simulated upstream failure")
        return True


@pytest.fixture
def fake_client() -> type[FakeProbeClient]:
    """The FakeProbeClient class, so tests can build instances: fake_client(ok=False)."""
    return FakeProbeClient


# --- Google API fakes -----------------------------------------------------


class FakeHttpError(Exception):
    """Mimics googleapiclient HttpError enough for the error mapper: it exposes
    ``resp.status`` and a message string. For scope/service-disabled cases the
    message carries the markers the mapper looks for."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"HTTP {status}")
        self.resp = type("Resp", (), {"status": status})()


class _Resp:
    """A canned response or a raised exception for one chained .execute()."""

    def __init__(self, spec: Any) -> None:
        self._spec = spec

    def execute(self) -> Any:
        if isinstance(self._spec, Exception):
            raise self._spec
        return self._spec


class FakeGscService:
    """Fake searchconsole/indexing discovery service.

    Built from a dict of canned responses keyed by operation:
    ``sites_list``, ``search``, ``inspect``, ``sitemaps_list``, ``submit``,
    ``publish``. A value may be a single response or a list (a queue) used for
    successive calls (e.g. the two queries in gsc_compare_periods); a single
    value is reused for repeated calls. A value that is an Exception is raised
    from ``.execute()``. All calls are recorded in ``.calls``.
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = {k: (v if isinstance(v, list) else [v]) for k, v in responses.items()}
        self._idx: dict[str, int] = {}
        self.calls: list[tuple[str, dict]] = []
        self._ctx: str | None = None

    # resource selectors
    def sites(self):
        self._ctx = "sites"
        return self

    def sitemaps(self):
        self._ctx = "sitemaps"
        return self

    def searchanalytics(self):
        self._ctx = "search"
        return self

    def urlInspection(self):
        self._ctx = "inspect"
        return self

    def index(self):
        return self

    def urlNotifications(self):
        self._ctx = "indexing"
        return self

    # leaf calls
    def list(self, **kw):
        op = "sites_list" if self._ctx == "sites" else "sitemaps_list"
        return self._mk(op, kw)

    def query(self, **kw):
        return self._mk("search", kw)

    def inspect(self, **kw):
        return self._mk("inspect", kw)

    def submit(self, **kw):
        return self._mk("submit", kw)

    def publish(self, **kw):
        return self._mk("publish", kw)

    def _mk(self, op: str, kw: dict) -> _Resp:
        self.calls.append((op, kw))
        items = self._responses.get(op)
        if items is None:
            return _Resp(KeyError(f"no canned response for {op}"))
        i = self._idx.get(op, 0)
        self._idx[op] = i + 1
        return _Resp(items[min(i, len(items) - 1)])


@pytest.fixture
def fake_http_error() -> type[FakeHttpError]:
    """The FakeHttpError class for simulating Google API failures."""
    return FakeHttpError


@pytest.fixture
def gsc_payloads() -> dict[str, Any]:
    """Canned Search Console / Indexing API response bodies."""
    return {
        "sites_list": {
            "siteEntry": [
                {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteOwner"},
                {"siteUrl": "https://www.example.com/", "permissionLevel": "siteFullUser"},
            ]
        },
        "search": {
            "rows": [
                {"keys": ["seo tools"], "clicks": 120, "impressions": 3400, "ctr": 0.035, "position": 4.2},
                {"keys": ["mcp server"], "clicks": 45, "impressions": 900, "ctr": 0.05, "position": 7.1},
            ]
        },
        "inspect": {
            "inspectionResult": {
                "indexStatusResult": {
                    "verdict": "PASS",
                    "coverageState": "Submitted and indexed",
                    "crawledAs": "MOBILE",
                    "lastCrawlTime": "2026-05-20T10:00:00Z",
                    "indexingState": "INDEXING_ALLOWED",
                    "pageFetchState": "SUCCESSFUL",
                    "googleCanonical": "https://www.example.com/page",
                    "userCanonical": "https://www.example.com/page",
                },
                "mobileUsabilityResult": {"verdict": "PASS"},
            }
        },
        "sitemaps_list": {
            "sitemap": [
                {
                    "path": "https://www.example.com/sitemap.xml",
                    "lastSubmitted": "2026-05-01T00:00:00Z",
                    "lastDownloaded": "2026-05-19T00:00:00Z",
                    "isPending": False,
                    "isSitemapsIndex": True,
                    "contents": [{"type": "web", "submitted": "120", "indexed": "118"}],
                }
            ]
        },
        "submit": {},
        "publish": {
            "urlNotificationMetadata": {
                "latestUpdate": {"type": "URL_UPDATED", "notifyTime": "2026-05-27T12:00:00Z"}
            }
        },
    }


@pytest.fixture
def make_gsc_client():
    """Build a real GscClient backed by a FakeGscService (one service used for
    both search and indexing). Pass a responses dict; defaults to gsc_payloads
    when called with no override."""
    from seo_mcp.clients.gsc import GscClient

    def _make(responses: dict[str, Any]) -> Any:
        service = FakeGscService(responses)
        client = GscClient(service, service)
        client._service = service  # expose for call assertions
        return client

    return _make


# --- GA4 fakes ------------------------------------------------------------


class FakeGoogleApiError(Exception):
    """Mimics a google.api_core exception: carries an HTTP-style ``code`` int
    (the path Ga4Client's mapper uses in production) and a message."""

    def __init__(self, code: int, message: str = "") -> None:
        super().__init__(message or f"status {code}")
        self.code = code


def make_ga4_response(
    dimension_headers: list[str],
    metric_headers: list[str],
    rows: list[tuple[list, list]],
    row_count: int | None = None,
) -> SimpleNamespace:
    """Build a RunReportResponse-shaped duck for the Ga4Client normalizer. Each
    row is (dimension_values, metric_values); values are stringified like GA4."""
    return SimpleNamespace(
        dimension_headers=[SimpleNamespace(name=n) for n in dimension_headers],
        metric_headers=[SimpleNamespace(name=n, type_=SimpleNamespace(name="TYPE_INTEGER")) for n in metric_headers],
        rows=[
            SimpleNamespace(
                dimension_values=[SimpleNamespace(value=str(d)) for d in dims],
                metric_values=[SimpleNamespace(value=str(m)) for m in mets],
            )
            for dims, mets in rows
        ],
        row_count=row_count if row_count is not None else len(rows),
    )


class FakeGa4Analytics:
    """Stands in for BetaAnalyticsDataClient. Returns canned responses (a single
    response reused, or a list consumed in order) and records each request. An
    Exception response is raised from run_report."""

    def __init__(self, responses: Any) -> None:
        self._responses = responses if isinstance(responses, list) else [responses]
        self._idx = 0
        self.requests: list[Any] = []

    def run_report(self, request: Any) -> Any:
        self.requests.append(request)
        spec = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        if isinstance(spec, Exception):
            raise spec
        return spec


@pytest.fixture
def fake_ga4_error() -> type[FakeGoogleApiError]:
    return FakeGoogleApiError


@pytest.fixture
def ga4_response():
    """Factory for RunReportResponse-shaped fakes."""
    return make_ga4_response


@pytest.fixture
def make_ga4_client():
    """Build a real Ga4Client backed by a FakeGa4Analytics. Pass the canned
    response(s); the fake analytics client is exposed at ``client._analytics``
    for request assertions."""
    from seo_mcp.clients.ga4 import Ga4Client

    def _make(responses: Any, default_property: str | None = None) -> Any:
        analytics = FakeGa4Analytics(responses)
        return Ga4Client(analytics, default_property=default_property)

    return _make


# --- Cloudflare fakes -----------------------------------------------------


class FakeCfTransport:
    """Stands in for CfClient._raw_request. Returns a canned CF envelope chosen
    by a label derived from the request path, and records every call. A response
    that is an Exception is raised. Used to drive the real CfClient end to end.

    Labels: purge, rum_list, rum_get, dns, resolve, account, zones.
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, method: str, path: str, body: Any = None) -> Any:
        self.calls.append((method, path, body))
        label = self._label(path)
        # Method-prefixed key wins (e.g. "POST bulk_lists" for create vs the
        # bare "bulk_lists" GET that lists), falling back to the bare label.
        if f"{method} {label}" in self.responses:
            spec = self.responses[f"{method} {label}"]
        elif label in self.responses:
            spec = self.responses[label]
        else:
            raise AssertionError(f"no canned CF response for label {label!r} (path {path})")
        if isinstance(spec, Exception):
            raise spec
        return spec

    @staticmethod
    def _label(path: str) -> str:
        if "purge_cache" in path:
            return "purge"
        if "rum/site_info/list" in path:
            return "rum_list"
        if "rum/site_info/" in path:
            return "rum_get"
        if "dns_records" in path:
            return "dns"
        if "bulk_operations" in path:
            return "bulk_operation"
        if "/rules/lists" in path and "/items" in path:
            return "bulk_items"
        if "/rules/lists" in path:
            return "bulk_lists"
        if "/entrypoint" in path:
            return "redirect_entrypoint"
        if "/rules/" in path:
            return "redirect_delete_rule"
        if path.endswith("/rules"):
            return "redirect_add_rule"
        if path.endswith("/rulesets"):
            return "redirect_create_ruleset"
        if "name=" in path:
            return "resolve"
        if "per_page=1" in path:
            return "account"
        return "zones"


def _ok(result: Any) -> dict[str, Any]:
    """Wrap a result in a successful CF envelope."""
    return {"success": True, "errors": [], "result": result}


@pytest.fixture
def cf_envelope():
    """Helper to wrap a result value in a successful CF envelope."""
    return _ok


@pytest.fixture
def cf_payloads() -> dict[str, Any]:
    """Canned CF envelopes keyed by FakeCfTransport label."""
    zone = {
        "id": "zone123",
        "name": "example.com",
        "status": "active",
        "plan": {"name": "Pro"},
        "paused": False,
        "name_servers": ["ns1.cloudflare.com", "ns2.cloudflare.com"],
        "created_on": "2024-01-01T00:00:00Z",
        "modified_on": "2026-05-01T00:00:00Z",
        "account": {"id": "acct123"},
    }
    return {
        "zones": _ok([zone, {"id": "z2", "name": "other.com", "status": "active", "plan": {"name": "Free"}}]),
        "resolve": _ok([zone]),
        "account": _ok([{"account": {"id": "acct123"}}]),
        "dns": _ok(
            [
                {"type": "A", "name": "example.com", "content": "192.0.2.1", "ttl": 1, "proxied": True, "id": "r1"},
                {"type": "TXT", "name": "example.com", "content": "google-site-verification=abc", "ttl": 1, "proxied": False, "id": "r2"},
            ]
        ),
        "rum_list": _ok(
            [
                {"host": "example.com", "site_tag": "tag-abc", "auto_install": True, "enabled": True, "created": "2025-01-01", "ruleset_id": "rs1"}
            ]
        ),
        "rum_get": _ok(
            {"host": "example.com", "site_tag": "tag-abc", "auto_install": True, "enabled": True, "created": "2025-01-01", "ruleset_id": "rs1"}
        ),
        "purge": _ok({"id": "zone123"}),
        # Redirect (dynamic) phase: default entrypoint exists with no rules.
        "redirect_entrypoint": _ok({"id": "rs-redir-1", "rules": []}),
        "redirect_add_rule": _ok({"id": "rule-new-1"}),
        "redirect_create_ruleset": _ok({"id": "rs-redir-1", "rules": [{"id": "rule-new-1"}]}),
        "redirect_delete_rule": _ok({"id": "rs-redir-1"}),
        # Bulk redirects (account-level). GET lists -> empty; POST create -> id.
        "bulk_lists": _ok([]),
        "POST bulk_lists": _ok({"id": "list-1", "name": "seomonster-redirects", "kind": "redirect"}),
        "bulk_items": _ok({"operation_id": "op-1"}),
        "bulk_operation": _ok({"status": "completed"}),
    }


@pytest.fixture
def make_cf_client():
    """Build a real CfClient whose network seam (_raw_request) is a
    FakeCfTransport. The transport is exposed at ``client._transport`` for call
    assertions (e.g. asserting a blocked purge made zero calls)."""
    from seo_mcp.clients.cloudflare import CfClient

    def _make(responses: dict[str, Any]) -> Any:
        client = CfClient(token="testtoken")
        transport = FakeCfTransport(responses)
        client._raw_request = transport
        client._transport = transport  # expose for assertions
        return client

    return _make


@pytest.fixture
def make_dispatcher() -> Callable[..., Callable[..., dict[str, Any]]]:
    """Return a dispatcher bound to a fixed clients mapping.

    Usage:
        dispatch = make_dispatcher(clients={"gsc": FakeProbeClient()})
        result = dispatch("system_status", {"probe": True}, config)
    """
    from seo_mcp import server

    def _factory(clients: Mapping[str, Any] | None = None):
        clients = clients or {}

        def _dispatch(name: str, arguments: Mapping[str, Any], config: Config) -> dict[str, Any]:
            return server.dispatch(name, arguments, config, clients)

        return _dispatch

    return _factory
