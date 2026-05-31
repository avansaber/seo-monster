"""Tests for `seo-monster setup`: the 0600 config writer, path resolution, and
the interactive setup_main (driven with injected prompt functions, validators
monkeypatched so nothing touches the network)."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from seo_mcp import cli
from seo_mcp.config import (
    config_file_mode,
    load_config,
    read_config_toml,
    resolve_config_path,
    write_config_toml,
)


# --- config writer / path helpers ----------------------------------------


def test_write_config_toml_roundtrips_and_drops_empties(tmp_path):
    path = tmp_path / "cfgdir" / "config.toml"
    write_config_toml(
        path,
        {
            "cloudflare": {"api_token": "cfat_abc", "zone": "example.com"},
            "psi": {"api_key": ""},          # empty value dropped
            "indexnow": {},                  # empty table omitted
            "gsc": {"default_site": "sc-domain:example.com"},
        },
    )
    # Reads back through the real loader (env empty so the file wins).
    cfg = load_config(env={}, config_path=str(path))
    assert cfg.cf_api_token == "cfat_abc"
    assert cfg.cf_zone == "example.com"
    assert cfg.gsc_default_site == "sc-domain:example.com"
    assert cfg.psi_api_key is None  # empty was dropped, not written as ""

    text = path.read_text()
    assert "[cloudflare]" in text
    assert "[psi]" not in text      # empty value -> no stanza
    assert "[indexnow]" not in text  # empty table -> no stanza


@pytest.mark.skipif(os.name == "nt", reason="POSIX perms")
def test_write_config_toml_sets_0600_and_0700(tmp_path):
    path = tmp_path / "cfgdir" / "config.toml"
    write_config_toml(path, {"cloudflare": {"api_token": "x"}})
    assert stat.S_IMODE(os.stat(path).st_mode) == config_file_mode() == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_write_config_toml_escapes_special_chars(tmp_path):
    path = tmp_path / "config.toml"
    tricky = 'tok"en\\with\\specials'
    write_config_toml(path, {"cloudflare": {"api_token": tricky}})
    cfg = load_config(env={}, config_path=str(path))
    assert cfg.cf_api_token == tricky  # survived escaping + tomllib round-trip


def test_resolve_config_path_precedence(tmp_path):
    explicit = str(tmp_path / "explicit.toml")
    assert str(resolve_config_path(env={}, config_path=explicit)) == explicit
    assert str(resolve_config_path(env={"SEO_MCP_CONFIG": "/x/y.toml"})) == "/x/y.toml"
    assert str(resolve_config_path(env={})).endswith("/.config/seo-mcp/config.toml")


def test_read_config_toml_missing_and_malformed(tmp_path):
    assert read_config_toml(tmp_path / "nope.toml") == {}
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not valid toml [[[")
    assert read_config_toml(bad) == {}


# --- setup_main -----------------------------------------------------------


def _prompts(text: dict[str, str], secret: dict[str, str]):
    """Build (prompt, secret_prompt) fakes that answer by label substring.
    A label with no matching key returns '' (i.e. skip / keep existing)."""

    def _match(answers: dict[str, str]):
        def _fn(label: str) -> str:
            for key, val in answers.items():
                if key in label:
                    return val
            return ""
        return _fn

    return _match(text), _match(secret)


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    """Point setup at a tmp config path and stub the network validators."""
    cfg_path = tmp_path / "cfgdir" / "config.toml"
    monkeypatch.setattr(cli, "resolve_config_path", lambda *a, **k: cfg_path)
    monkeypatch.setattr(cli, "validate_cloudflare", lambda token: ("ok", "3 zone(s) visible"))
    monkeypatch.setattr(cli, "validate_indexnow", lambda key, loc: ("skipped", "stub"))
    return cfg_path


def test_setup_main_happy_path_writes_config(setup_env, capsys):
    prompt, secret = _prompts(
        text={"zone": "example.com", "GSC property": "sc-domain:example.com", "GA4 property": "properties/123"},
        secret={"Cloudflare API token": "cfat_new", "PSI API key": "psikey", "IndexNow key": "idxkey"},
    )
    rc = cli.setup_main(prompt=prompt, secret_prompt=secret)
    assert rc == 0
    assert "Wrote" in capsys.readouterr().out

    cfg = load_config(env={}, config_path=str(setup_env))
    assert cfg.cf_api_token == "cfat_new"
    assert cfg.cf_zone == "example.com"
    assert cfg.psi_api_key == "psikey"
    assert cfg.indexnow_key == "idxkey"
    assert cfg.gsc_default_site == "sc-domain:example.com"
    assert cfg.ga4_property_id == "properties/123"


def test_setup_main_rejected_cf_token_not_saved(tmp_path, monkeypatch, capsys):
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr(cli, "resolve_config_path", lambda *a, **k: cfg_path)
    monkeypatch.setattr(cli, "validate_cloudflare", lambda token: ("rejected", "invalid token"))
    monkeypatch.setattr(cli, "validate_indexnow", lambda key, loc: ("skipped", "stub"))

    prompt, secret = _prompts(text={"zone": "example.com"}, secret={"Cloudflare API token": "cfat_bad"})
    rc = cli.setup_main(prompt=prompt, secret_prompt=secret)
    assert rc == 0

    cfg = load_config(env={}, config_path=str(cfg_path))
    assert cfg.cf_api_token is None     # known-bad token never persisted
    assert cfg.cf_zone == "example.com"  # the rest still saved
    assert "not saved" in capsys.readouterr().out.lower()


def test_setup_main_rerun_keeps_existing_when_blank(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    write_config_toml(cfg_path, {"cloudflare": {"api_token": "cfat_keep", "zone": "old.com"}})
    monkeypatch.setattr(cli, "resolve_config_path", lambda *a, **k: cfg_path)

    validated: dict[str, bool] = {}
    monkeypatch.setattr(cli, "validate_cloudflare", lambda token: validated.update(called=True) or ("ok", ""))
    monkeypatch.setattr(cli, "validate_indexnow", lambda key, loc: ("skipped", "stub"))

    # All prompts blank -> keep existing everywhere.
    prompt, secret = _prompts(text={}, secret={})
    rc = cli.setup_main(prompt=prompt, secret_prompt=secret)
    assert rc == 0

    cfg = load_config(env={}, config_path=str(cfg_path))
    assert cfg.cf_api_token == "cfat_keep"  # not wiped
    assert cfg.cf_zone == "old.com"
    assert "called" not in validated  # unchanged token -> no re-validation


def test_setup_written_file_is_still_overridden_by_env(setup_env, monkeypatch):
    prompt, secret = _prompts(text={}, secret={"Cloudflare API token": "cfat_file"})
    cli.setup_main(prompt=prompt, secret_prompt=secret)
    # Env var must still win over the file the setup wrote (precedence preserved).
    cfg = load_config(env={"CF_API_TOKEN": "cfat_env_wins"}, config_path=str(setup_env))
    assert cfg.cf_api_token == "cfat_env_wins"


def test_setup_main_eof_exits_cleanly(tmp_path, monkeypatch, capsys):
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr(cli, "resolve_config_path", lambda *a, **k: cfg_path)

    def boom(_label):
        raise EOFError

    rc = cli.setup_main(prompt=boom, secret_prompt=boom)
    assert rc == 1
    assert "cancelled" in capsys.readouterr().err.lower()
    assert not cfg_path.exists()  # nothing written on abort


def test_validate_cloudflare_classifies_400_as_rejected(monkeypatch):
    # Regression for FEEDBACK §12c.i: a typo'd short token returns HTTP 400 ->
    # INVALID_INPUT, which must reject (not fall through to 'unreachable' and
    # get persisted).
    from seo_mcp.clients.cloudflare import CfClient
    from seo_mcp.clients.errors import ApiError
    from seo_mcp.errors import ErrorCode

    def boom(self):
        raise ApiError(ErrorCode.INVALID_INPUT, "Cloudflare rejected the request as invalid (HTTP 400).")

    monkeypatch.setattr(CfClient, "list_zones", boom)
    status, _ = cli.validate_cloudflare("cfat_typo")
    assert status == "rejected"


def test_validate_cloudflare_upstream_error_stays_unreachable(monkeypatch):
    # Guard: extending the reject set must NOT misclassify a genuine offline
    # error as rejected (that would drop a valid token).
    from seo_mcp.clients.cloudflare import CfClient
    from seo_mcp.clients.errors import ApiError
    from seo_mcp.errors import ErrorCode

    def boom(self):
        raise ApiError(ErrorCode.UPSTREAM_ERROR, "network down")

    monkeypatch.setattr(CfClient, "list_zones", boom)
    status, _ = cli.validate_cloudflare("cfat_real")
    assert status == "unreachable"


def test_validate_indexnow_sends_branded_user_agent(monkeypatch):
    # Regression for FEEDBACK §12c.ii: the default Python-urllib UA is 403'd by
    # Cloudflare's Browser Integrity Check, so the key-file fetch must send the
    # project's branded UA.
    import urllib.request

    captured: dict[str, str] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"mykey"

    def fake_urlopen(req, timeout=None):
        captured["ua"] = req.get_header("User-agent")
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    status, _ = cli.validate_indexnow("mykey", "https://example.com/mykey.txt")
    assert status == "ok"
    assert captured["ua"] and captured["ua"].startswith("SEOMonster/")


def test_server_main_dispatches_setup_to_cli(monkeypatch):
    pytest.importorskip("mcp")
    from seo_mcp import server

    exit_code: dict[str, int] = {}

    def fake_exit(code):
        exit_code["code"] = code
        raise SystemExit(code)

    async def fake_async_main():
        raise AssertionError("stdio server must not start for the 'setup' subcommand")

    monkeypatch.setattr(sys, "argv", ["seo-monster", "setup"])
    monkeypatch.setattr(sys, "exit", fake_exit)
    monkeypatch.setattr("seo_mcp.cli.setup_main", lambda argv: 5)
    monkeypatch.setattr(server, "_async_main", fake_async_main)

    with pytest.raises(SystemExit):
        server.main()
    assert exit_code["code"] == 5
