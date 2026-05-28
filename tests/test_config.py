"""Tests for config resolution: env-first, file fallback, defaults, flags."""

from __future__ import annotations

import textwrap

from seo_mcp.config import DATA_STATE_DEFAULT, load_config


def _write_toml(tmp_path, body: str) -> str:
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body))
    return str(path)


def test_nothing_configured_yields_all_none():
    cfg = load_config(env={}, config_path="/nonexistent.toml")
    assert cfg.google.oauth_client is None
    assert cfg.google.credentials is None
    assert cfg.gsc_default_site is None
    assert cfg.ga4_property_id is None
    assert cfg.psi_api_key is None
    assert cfg.cf_api_token is None
    assert cfg.allow_destructive is False
    assert cfg.gsc_data_state == DATA_STATE_DEFAULT
    assert cfg.source_path is None


def test_env_only_resolution():
    env = {
        "SEO_MCP_GOOGLE_OAUTH_CLIENT": "/c/client.json",
        "SEO_MCP_GOOGLE_TOKEN": "/c/token.json",
        "SEO_MCP_GSC_DEFAULT_SITE": "sc-domain:example.com",
        "SEO_MCP_GA4_PROPERTY_ID": "properties/123",
        "PSI_API_KEY": "AIzaKEY",
        "CF_API_TOKEN": "cftoken",
        "CF_ZONE": "example.com",
    }
    cfg = load_config(env=env, config_path="/nonexistent.toml")
    assert cfg.google.oauth_client == "/c/client.json"
    assert cfg.google.token == "/c/token.json"
    assert cfg.gsc_default_site == "sc-domain:example.com"
    assert cfg.ga4_property_id == "properties/123"
    assert cfg.psi_api_key == "AIzaKEY"
    assert cfg.cf_api_token == "cftoken"
    assert cfg.cf_zone == "example.com"


def test_file_fallback_when_env_absent(tmp_path):
    path = _write_toml(
        tmp_path,
        """
        [google]
        oauth_client = "/file/client.json"
        token = "/file/token.json"

        [gsc]
        default_site = "sc-domain:fromfile.com"
        data_state = "final"

        [ga4]
        property_id = "properties/999"

        [psi]
        api_key = "filekey"

        [cloudflare]
        api_token = "filetoken"
        zone = "fromfile.com"

        [server]
        allow_destructive = true
        """,
    )
    cfg = load_config(env={}, config_path=path)
    assert cfg.google.oauth_client == "/file/client.json"
    assert cfg.gsc_default_site == "sc-domain:fromfile.com"
    assert cfg.gsc_data_state == "final"
    assert cfg.ga4_property_id == "properties/999"
    assert cfg.psi_api_key == "filekey"
    assert cfg.cf_api_token == "filetoken"
    assert cfg.cf_zone == "fromfile.com"
    assert cfg.allow_destructive is True
    assert cfg.source_path == path


def test_env_wins_over_file(tmp_path):
    path = _write_toml(
        tmp_path,
        """
        [gsc]
        default_site = "sc-domain:fromfile.com"

        [cloudflare]
        api_token = "filetoken"
        """,
    )
    env = {
        "SEO_MCP_GSC_DEFAULT_SITE": "sc-domain:fromenv.com",
        "CF_API_TOKEN": "envtoken",
    }
    cfg = load_config(env=env, config_path=path)
    assert cfg.gsc_default_site == "sc-domain:fromenv.com"
    assert cfg.cf_api_token == "envtoken"


def test_service_account_standard_env_fallback():
    # SEO_MCP_GOOGLE_CREDENTIALS preferred, then GOOGLE_APPLICATION_CREDENTIALS.
    cfg = load_config(
        env={"GOOGLE_APPLICATION_CREDENTIALS": "/std/sa.json"},
        config_path="/nonexistent.toml",
    )
    assert cfg.google.credentials == "/std/sa.json"

    cfg2 = load_config(
        env={
            "SEO_MCP_GOOGLE_CREDENTIALS": "/preferred/sa.json",
            "GOOGLE_APPLICATION_CREDENTIALS": "/std/sa.json",
        },
        config_path="/nonexistent.toml",
    )
    assert cfg2.google.credentials == "/preferred/sa.json"


def test_data_state_invalid_falls_back_to_default():
    cfg = load_config(env={"SEO_MCP_DATA_STATE": "garbage"}, config_path="/nonexistent.toml")
    assert cfg.gsc_data_state == DATA_STATE_DEFAULT
    cfg2 = load_config(env={"SEO_MCP_DATA_STATE": "FINAL"}, config_path="/nonexistent.toml")
    assert cfg2.gsc_data_state == "final"


def test_destructive_flag_truthiness():
    truthy = ["true", "True", "1", "yes", "ON"]
    for v in truthy:
        cfg = load_config(env={"SEO_MCP_ALLOW_DESTRUCTIVE": v}, config_path="/nonexistent.toml")
        assert cfg.allow_destructive is True, f"{v!r} should be truthy"
    for v in ["false", "0", "no", "", "maybe"]:
        cfg = load_config(env={"SEO_MCP_ALLOW_DESTRUCTIVE": v}, config_path="/nonexistent.toml")
        assert cfg.allow_destructive is False, f"{v!r} should be falsey"


def test_env_blank_value_does_not_override_file(tmp_path):
    # A blank env var is treated as unset and falls through to the file.
    path = _write_toml(
        tmp_path,
        """
        [gsc]
        default_site = "sc-domain:fromfile.com"
        """,
    )
    cfg = load_config(env={"SEO_MCP_GSC_DEFAULT_SITE": "   "}, config_path=path)
    assert cfg.gsc_default_site == "sc-domain:fromfile.com"


def test_path_fields_expand_tilde_and_env_vars(monkeypatch):
    # Defense in depth: even if a host passes a literal "${HOME}/..." or "~/..."
    # to SEO_MCP_GOOGLE_*, the server resolves it to an absolute path.
    monkeypatch.setenv("HOME", "/home/test-user")
    env = {
        "SEO_MCP_GOOGLE_OAUTH_CLIENT": "~/.config/seo-monster/client_secret.json",
        "SEO_MCP_GOOGLE_TOKEN": "${HOME}/.config/seo-monster/token.json",
        "SEO_MCP_GOOGLE_CREDENTIALS": "${HOME}/sa.json",
    }
    cfg = load_config(env=env, config_path="/nonexistent.toml")
    assert cfg.google.oauth_client == "/home/test-user/.config/seo-monster/client_secret.json"
    assert cfg.google.token == "/home/test-user/.config/seo-monster/token.json"
    assert cfg.google.credentials == "/home/test-user/sa.json"


def test_path_fields_pass_through_when_already_absolute():
    cfg = load_config(
        env={
            "SEO_MCP_GOOGLE_OAUTH_CLIENT": "/abs/client.json",
            "SEO_MCP_GOOGLE_TOKEN": "/abs/token.json",
        },
        config_path="/nonexistent.toml",
    )
    assert cfg.google.oauth_client == "/abs/client.json"
    assert cfg.google.token == "/abs/token.json"


def test_malformed_toml_falls_back_to_env(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("this is = = not valid toml [[[")
    cfg = load_config(env={"CF_API_TOKEN": "envtoken"}, config_path=str(path))
    assert cfg.cf_api_token == "envtoken"
    assert cfg.source_path is None
