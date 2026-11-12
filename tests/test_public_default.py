"""Regression tests for default website fetch restrictions."""

import ast
import json
from pathlib import Path

import pytest

from seo_mcp.clients.errors import ApiError
from seo_mcp.clients.http import HttpClient, HttpResponse, _is_public_ip
from seo_mcp.errors import ErrorCode
from seo_mcp.tools.ai_readiness_tools import ai_citation_readiness
from seo_mcp.tools.hreflang_tools import hreflang_consistency_check
from seo_mcp.tools.linkgraph_tools import internal_link_graph
from seo_mcp.tools.onpage_tools import check_canonical, inspect_meta
from seo_mcp.tools.redirect_tools import redirect_chain_audit
from seo_mcp.tools.robots_tools import robots_txt_validate
from seo_mcp.tools.schema_tools import inspect_schema
from seo_mcp.tools.sitemap_tools import sitemap_validate


@pytest.fixture(autouse=True)
def no_system_proxy(monkeypatch):
    monkeypatch.setattr("urllib.request.getproxies", dict)


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://168.63.129.16/"])
def test_default_fetch_denies_private_and_platform_targets(monkeypatch, url):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {host})
    monkeypatch.setattr(HttpClient, "_http_request_public", lambda *a, **k: pytest.fail("connection attempted"))
    with pytest.raises(ApiError):
        HttpClient().fetch(url)


def test_default_redirect_revalidates_destination(monkeypatch):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {"1.1.1.1"} if host == "example.com" else {"127.0.0.1"})
    calls = []
    def redirect(*args, **kwargs):
        calls.append(args[2])
        return 302, {"location": "http://127.0.0.1/"}, b""
    monkeypatch.setattr(HttpClient, "_http_request_public", redirect)
    with pytest.raises(ApiError) as error:
        HttpClient().fetch("http://example.com/")
    assert len(calls) == 1
    assert "non-public" in error.value.message


def test_explicit_false_cannot_bypass_policy(monkeypatch):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {"127.0.0.1"})
    monkeypatch.setattr(HttpClient, "_http_request_public", lambda *a, **k: pytest.fail("connection attempted"))
    with pytest.raises(ApiError):
        HttpClient().fetch("http://example.com/", public_only=False)


@pytest.mark.parametrize("address", [
    "169.254.169.254", "fd00:ec2::254", "100.100.100.200", "168.63.129.16",
    "::ffff:168.63.129.16", "::ffff:169.254.169.254", "::a9fe:a9fe",
    "64:ff9b:1::a9fe:a9fe", "0.0.0.0", "::", "127.0.0.1", "10.0.0.1", "224.0.0.1",
])
def test_default_refuses_denied_resolved_addresses(monkeypatch, address):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {address})
    monkeypatch.setattr(HttpClient, "_http_request_public", lambda *a, **k: pytest.fail("connection attempted"))
    with pytest.raises(ApiError):
        HttpClient().fetch("https://example.test/")


def test_mixed_dns_answers_refused(monkeypatch):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {"8.8.8.8", "::1"})
    monkeypatch.setattr(HttpClient, "_http_request_public", lambda *a, **k: pytest.fail("connection attempted"))
    with pytest.raises(ApiError):
        HttpClient().fetch("https://example.test/")


@pytest.mark.parametrize(("handler", "key"), [
    (inspect_meta, "url"), (check_canonical, "url"), (redirect_chain_audit, "url"),
    (robots_txt_validate, "site_url"), (sitemap_validate, "sitemap_url"),
    (inspect_schema, "url"), (ai_citation_readiness, "url"),
])
def test_tool_entry_points_refuse_private_url_without_exposing_query(monkeypatch, handler, key):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {"127.0.0.1"})
    monkeypatch.setattr(HttpClient, "_http_request_public", lambda *a, **k: pytest.fail("connection attempted"))
    result = handler({key: "https://private.example/path?synthetic_secret=hidden"}, None, {"http": HttpClient()})
    assert result["ok"] is False
    assert "hidden" not in str(result)
    assert "127.0.0.1" not in str(result)


@pytest.mark.parametrize("handler", [inspect_meta, inspect_schema, ai_citation_readiness])
def test_fetch_status_error_does_not_echo_url(monkeypatch, handler):
    monkeypatch.setattr(HttpClient, "fetch", lambda self, url, **kwargs: HttpResponse(404, {}, b"", url))
    result = handler({"url": "https://example.test/canary-path?token=canary-9821"}, None, {"http": HttpClient()})
    assert result["ok"] is False
    assert "canary-path" not in str(result) and "canary-9821" not in str(result)


@pytest.mark.parametrize(("handler", "arguments"), [
    (hreflang_consistency_check, {"urls": ["https://private.example/a", "https://private.example/b"]}),
    (internal_link_graph, {"start_url": "https://private.example/"}),
])
def test_crawling_tool_entry_points_do_not_connect_to_private_hosts(monkeypatch, handler, arguments):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {"127.0.0.1"})
    monkeypatch.setattr(HttpClient, "_http_request_public", lambda *a, **k: pytest.fail("connection attempted"))
    result = handler(arguments, None, {"http": HttpClient()})
    assert result["ok"] is True
    if handler is hreflang_consistency_check:
        assert not result["data"]["fetched"]
        assert len(result["data"]["fetch_errors"]) == 2
    else:
        assert result["data"]["pages"][0]["status"] is None


@pytest.mark.parametrize("status", [300, 304, 305, 306])
def test_non_redirect_response_is_not_followed(monkeypatch, status):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {"8.8.8.8"})
    calls = []
    def respond(*args, **kwargs):
        calls.append(args[2])
        return status, {"location": "http://127.0.0.1/"}, b""
    monkeypatch.setattr(HttpClient, "_http_request_public", respond)
    assert HttpClient().fetch("https://example.test/").status == status
    assert len(calls) == 1


@pytest.mark.parametrize("location", ["//127.0.0.1/admin", "http://168.63.129.16/", "file:///etc/passwd", "ftp://example.test/file"])
def test_redirect_to_disallowed_target_never_connects(monkeypatch, location):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {"8.8.8.8"} if host == "example.test" else {host})
    calls = []
    def respond(*args, **kwargs):
        calls.append(args[2])
        return 302, {"location": location}, b""
    monkeypatch.setattr(HttpClient, "_http_request_public", respond)
    with pytest.raises(ApiError):
        HttpClient().fetch("https://example.test/start")
    assert calls == ["https://example.test/start"]


@pytest.mark.parametrize("url", [
    "http://user:password@example.test/", "http://[fe80::1%25en0]/",
    "http://example.test:99999/", "http://example.test:0/", "https://example.test../",
    "https://example.test/space here", "https://example.test/\x00", "https://example.test/\r\n",
    "https://example.test\u3002\u3002/", "https://example.test/\ud800",
])
def test_invalid_authority_never_reaches_dns_or_transport(monkeypatch, url):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda *a: pytest.fail("unexpected DNS"))
    monkeypatch.setattr(HttpClient, "_http_request_public", lambda *a, **k: pytest.fail("unexpected connection"))
    with pytest.raises(ApiError):
        HttpClient().fetch(url)


@pytest.mark.parametrize("host", ["2130706433", "0x7f.1", "0177.0.0.1", "127.1", "localhost."])
def test_numeric_and_trailing_dot_hosts_are_checked_after_resolution(monkeypatch, host):
    resolutions = []
    def resolve(name, port):
        resolutions.append((name, port))
        return {"127.0.0.1"}
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", resolve)
    monkeypatch.setattr(HttpClient, "_http_request_public", lambda *a, **k: pytest.fail("connection attempted"))
    with pytest.raises(ApiError):
        HttpClient().fetch(f"http://{host}/")
    assert resolutions == [(host, 80)]


@pytest.mark.parametrize("address", [
    "fec0::1", "::ffff:0:a9fe:a9fe", "2002:7f00:1::", "2001::ffff:ffff:7f00:1",
    "64:ff9b::a9fe:a9fe", "192.0.0.192", "100.64.0.1", "198.18.0.1", "240.0.0.1",
])
def test_special_address_classification_is_not_public(address):
    assert _is_public_ip(address) is False


@pytest.mark.parametrize("address", ["8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_address_positive_controls(address):
    assert _is_public_ip(address) is True


def test_fetch_pins_public_trailing_dot_hostname(monkeypatch):
    calls = []
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda host, port: {"8.8.8.8"})
    monkeypatch.setattr(HttpClient, "_http_request_public", lambda self, method, url, **kw: (calls.append(kw["target"]) or (200, {}, b"")))
    HttpClient().fetch("https://example.test./")
    assert calls[0].host == "example.test"


def test_fetch_rejects_authorization_header_before_connection(monkeypatch):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda *args: {"8.8.8.8"})
    monkeypatch.setattr("seo_mcp.clients.http._PublicConnection", lambda *a, **k: pytest.fail("connection attempted"))
    with pytest.raises(ApiError) as error:
        HttpClient().fetch("https://example.test/", extra_headers={"Authorization": "synthetic-secret"})
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert "synthetic-secret" not in str(error.value.to_envelope("technical"))


def test_public_fetch_does_not_invoke_legacy_raw_transport():
    import seo_mcp.clients.http as http_module

    tree = ast.parse(Path(http_module.__file__).read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_http_request_raw" for node in ast.walk(tree)
    )


def test_safe_fetch_messages_track_literal_errors():
    import seo_mcp.clients.http as http_module

    tree = ast.parse(Path(http_module.__file__).read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "safe_public_fetch_error")
    assignment = next(n for n in function.body if isinstance(n, ast.Assign))
    allowed = ast.literal_eval(assignment.value)
    literals = {
        n.args[1].value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "ApiError"
        and len(n.args) > 1 and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str)
    }
    assert allowed == literals


def test_denial_envelope_does_not_reveal_private_query_or_ip(monkeypatch):
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda *a: {"168.63.129.16"})
    monkeypatch.setattr(HttpClient, "_http_request_public", lambda *a, **k: pytest.fail("connection attempted"))
    with pytest.raises(ApiError) as error:
        HttpClient().fetch("https://private.example/path?token=synthetic-value")
    encoded = json.dumps(error.value.to_envelope("technical"))
    assert "synthetic-value" not in encoded
    assert "private.example" not in encoded
    assert "168.63.129.16" not in encoded


def test_unsanctioned_modules_do_not_import_common_http_transports():
    """Catch common direct transport imports and calls outside listed clients."""
    import seo_mcp

    root = Path(seo_mcp.__file__).parent
    allowlist = {f"clients/{name}.py" for name in (
        "http", "crux", "openpagerank", "ai_engines", "cloudflare", "dataforseo", "indexnow", "psi", "serpbase"
    )}
    forbidden_imports = ("urllib.request", "urllib.robotparser", "requests", "httpx", "http.client", "socket", "urllib3", "aiohttp")
    for path in root.rglob("*.py"):
        if path.relative_to(root).as_posix() in allowlist:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not any(alias.name == name or alias.name.startswith(name + ".")
                                   for name in forbidden_imports) for alias in node.names), path.name
            if isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
                assert not any(module == name or module.startswith(name + ".")
                               for module in imported for name in forbidden_imports), path.name
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"_http_request_raw", "urlopen", "build_opener"}
            for node in ast.walk(tree)
        ), path.name


@pytest.mark.parametrize("proxy_key", ["http", "https", "all", "socks"])
def test_configured_proxy_is_rejected_without_disclosing_it(monkeypatch, proxy_key):
    import urllib.request

    from seo_mcp.clients.http import _PublicTarget

    monkeypatch.setattr(urllib.request, "getproxies", lambda: {proxy_key: "http://user:synthetic-secret@proxy.test"})
    monkeypatch.setattr("seo_mcp.clients.http._PublicConnection", lambda *a, **k: pytest.fail("connection attempted"))
    target = _PublicTarget("example.test", 443, True, ("8.8.8.8",))
    with pytest.raises(ApiError) as error:
        HttpClient()._http_request_public("GET", "https://example.test/", max_bytes=10, extra_headers=None, target=target)
    assert "synthetic-secret" not in json.dumps(error.value.to_envelope("technical"))


def test_fetch_refuses_proxy_before_dns(monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://user:synthetic-secret@proxy.test"})
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda *a: pytest.fail("unexpected DNS"))
    with pytest.raises(ApiError) as error:
        HttpClient().fetch("https://example.test/")
    envelope = json.dumps(error.value.to_envelope("technical"))
    assert error.value.code == ErrorCode.INVALID_INPUT
    assert all(secret not in envelope for secret in ("synthetic-secret", "proxy.test", "user:"))


def test_proxy_bypass_list_does_not_override_refusal(monkeypatch):
    monkeypatch.setattr("urllib.request.getproxies", lambda: {"https": "http://proxy.test", "no": "example.test"})
    monkeypatch.setattr("seo_mcp.clients.http._resolve_host", lambda *a: pytest.fail("unexpected DNS"))
    with pytest.raises(ApiError) as error:
        HttpClient().fetch("https://example.test/")
    assert error.value.code == ErrorCode.INVALID_INPUT


def test_no_proxy_only_does_not_trigger_proxy_refusal(monkeypatch):
    import urllib.request

    from seo_mcp.clients.http import _PublicTarget

    class ReachedTransport(Exception):
        pass

    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"no": "example.test"})
    monkeypatch.setattr("seo_mcp.clients.http._PublicConnection", lambda *a, **k: (_ for _ in ()).throw(ReachedTransport()))
    target = _PublicTarget("example.test", 443, True, ("8.8.8.8",))
    with pytest.raises(ReachedTransport):
        HttpClient()._http_request_public("GET", "https://example.test/", max_bytes=10, extra_headers=None, target=target)
