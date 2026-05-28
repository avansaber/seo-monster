"""Tests for the credential resolver and the interactive consent helper.

The key invariant: ``build_google_credentials`` is silent. It must never open
a browser, never call ``run_local_server``. The consent flow is in
``run_oauth_consent`` and is only invoked from the ``seo-monster auth`` CLI.
"""

from __future__ import annotations

import os
import stat
from types import SimpleNamespace

import pytest

from seo_mcp.auth import MissingGoogleAuth
from seo_mcp.clients.google_auth import (
    _write_token,
    build_google_credentials,
    required_token_mode,
    run_oauth_consent,
)


def test_build_creds_no_auth_at_all_raises(make_config):
    cfg = make_config()
    with pytest.raises(MissingGoogleAuth, match="No Google credentials configured"):
        build_google_credentials(cfg, ["scope"])


def test_build_creds_oauth_without_token_path_raises(make_config):
    cfg = make_config(SEO_MCP_GOOGLE_OAUTH_CLIENT="/x/client.json")
    with pytest.raises(MissingGoogleAuth, match="SEO_MCP_GOOGLE_TOKEN"):
        build_google_credentials(cfg, ["scope"])


def test_build_creds_missing_token_file_directs_to_cli(make_config, tmp_path):
    # Critical guard for §7a.viii: when no token cache exists, the server
    # must NOT open a browser. It must surface a clear remediation pointing
    # the user at the `seo-monster auth` CLI.
    token = tmp_path / "no_such_token.json"
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/x/client.json",
        SEO_MCP_GOOGLE_TOKEN=str(token),
    )
    with pytest.raises(MissingGoogleAuth, match="seo-monster auth"):
        build_google_credentials(cfg, ["scope"])


def test_run_oauth_consent_requires_oauth_client(make_config):
    cfg = make_config()
    with pytest.raises(MissingGoogleAuth, match="SEO_MCP_GOOGLE_OAUTH_CLIENT"):
        run_oauth_consent(cfg, ["scope"])


def test_run_oauth_consent_requires_token_path(make_config):
    cfg = make_config(SEO_MCP_GOOGLE_OAUTH_CLIENT="/x/client.json")
    with pytest.raises(MissingGoogleAuth, match="SEO_MCP_GOOGLE_TOKEN"):
        run_oauth_consent(cfg, ["scope"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits only")
def test_write_token_applies_secure_perms(tmp_path):
    target = tmp_path / "newdir" / "token.json"
    fake_creds = SimpleNamespace(to_json=lambda: '{"refresh_token": "x"}')
    path = _write_token(str(target), fake_creds)
    assert path.read_text() == '{"refresh_token": "x"}'
    assert stat.S_IMODE(path.stat().st_mode) == required_token_mode()  # 0600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    # Sanity: the file mode is read-only for group/other, not world-readable.
    assert required_token_mode() & 0o077 == 0
