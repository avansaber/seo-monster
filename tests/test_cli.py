"""Tests for the ``seo-monster auth`` CLI subcommand and main()-dispatch."""

from __future__ import annotations

import sys

import pytest

from seo_mcp import cli


def test_auth_main_no_google_returns_2(make_config, monkeypatch, capsys):
    cfg = make_config()
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    rc = cli.auth_main()
    err = capsys.readouterr().err
    assert rc == 2
    assert "no Google auth configured" in err
    assert "seo-monster auth" in err


def test_auth_main_service_account_short_circuits(make_config, monkeypatch, capsys):
    cfg = make_config(SEO_MCP_GOOGLE_CREDENTIALS="/x/sa.json")
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    rc = cli.auth_main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Service-account" in out
    assert "no interactive consent" in out.lower()


def test_auth_main_oauth_runs_consent(make_config, monkeypatch, capsys, tmp_path):
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/x/client.json",
        SEO_MCP_GOOGLE_TOKEN=str(tmp_path / "token.json"),
    )
    monkeypatch.setattr(cli, "load_config", lambda: cfg)
    called: dict[str, object] = {}

    def fake_consent(config, scopes):
        called["config"] = config
        called["scopes"] = scopes
        return tmp_path / "token.json"

    monkeypatch.setattr(cli, "run_oauth_consent", fake_consent)
    rc = cli.auth_main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "Token cached" in out
    assert called["config"] is cfg
    assert called["scopes"]  # non-empty


def test_auth_main_propagates_missing_google_auth(make_config, monkeypatch, capsys, tmp_path):
    from seo_mcp.auth import MissingGoogleAuth

    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/x/client.json",
        SEO_MCP_GOOGLE_TOKEN=str(tmp_path / "token.json"),
    )
    monkeypatch.setattr(cli, "load_config", lambda: cfg)

    def fake_consent(config, scopes):
        raise MissingGoogleAuth("simulated failure")

    monkeypatch.setattr(cli, "run_oauth_consent", fake_consent)
    rc = cli.auth_main()
    err = capsys.readouterr().err
    assert rc == 2
    assert "simulated failure" in err


def test_server_main_dispatches_auth_to_cli(monkeypatch):
    # server.main() must peel off the "auth" subcommand and never enter the
    # stdio server loop.
    pytest.importorskip("mcp")
    from seo_mcp import server

    sys_exit_called: dict[str, int] = {}

    def fake_exit(code):
        sys_exit_called["code"] = code
        raise SystemExit(code)

    def fake_auth_main(argv):
        return 7  # arbitrary, non-zero

    async def fake_async_main():
        raise AssertionError("stdio server must not start when 'auth' is the subcommand")

    monkeypatch.setattr(sys, "argv", ["seo-monster", "auth"])
    monkeypatch.setattr(sys, "exit", fake_exit)
    monkeypatch.setattr("seo_mcp.cli.auth_main", fake_auth_main)
    monkeypatch.setattr(server, "_async_main", fake_async_main)

    with pytest.raises(SystemExit):
        server.main()
    assert sys_exit_called["code"] == 7
