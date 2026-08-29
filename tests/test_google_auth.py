"""Tests for the credential resolver and the interactive consent helper.

The key invariant: ``build_google_credentials`` is silent. It must never open
a browser, never call ``run_local_server``. The consent flow is in
``run_oauth_consent`` and is only invoked from the ``seo-monster auth`` CLI.
"""

from __future__ import annotations

import json
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


# --- GH issue #2: the scope guard must actually fire --------------------------
#
# `Credentials.from_authorized_user_file(path, scopes)` passes the requested
# scopes through to `from_authorized_user_info`, which only consults the file's
# own "scopes" key when the caller passes None. So `creds.scopes` echoed back
# whatever we asked for and `scopes_ok` was always True -- the MissingGoogleAuth
# branch below it was unreachable. These tests pin the fixed behaviour: compare
# against the granted set recorded in the token file.


def _write_granted_token(tmp_path, granted, *, as_string=False, include=True):
    payload = {
        "token": "x",
        "refresh_token": "y",
        "client_id": "c",
        "client_secret": "s",
    }
    if include:
        payload["scopes"] = " ".join(granted) if as_string else list(granted)
    p = tmp_path / "token.json"
    p.write_text(json.dumps(payload))
    return p


def _oauth_config(make_config, token_path):
    return make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/x/client.json",
        SEO_MCP_GOOGLE_TOKEN=str(token_path),
    )


def test_scope_guard_fires_when_token_is_missing_a_scope(make_config, tmp_path):
    """A token granting 2 scopes must be rejected when 3 are required."""
    granted = [
        "https://www.googleapis.com/auth/webmasters",
        "https://www.googleapis.com/auth/indexing",
    ]
    asked = granted + ["https://www.googleapis.com/auth/analytics.readonly"]
    cfg = _oauth_config(make_config, _write_granted_token(tmp_path, granted))
    with pytest.raises(MissingGoogleAuth, match="does not cover the scopes"):
        build_google_credentials(cfg, asked)


def test_scope_guard_does_not_fire_when_token_covers_scopes(make_config, tmp_path):
    """The inverse: a superset token must get past the scope guard."""
    asked = ["https://www.googleapis.com/auth/webmasters"]
    granted = asked + ["https://www.googleapis.com/auth/indexing"]
    cfg = _oauth_config(make_config, _write_granted_token(tmp_path, granted))
    try:
        build_google_credentials(cfg, asked)
    except Exception as exc:  # noqa: BLE001 - may still fail later on refresh
        assert "does not cover the scopes" not in str(exc)


def test_scope_guard_accepts_space_delimited_string_scopes(make_config, tmp_path):
    """google-auth allows "scopes" to be a space-delimited string.

    Treating that string as a sequence yields a set of single characters, which
    would fail the subset test and lock out every user holding such a token.
    """
    asked = ["https://www.googleapis.com/auth/webmasters"]
    granted = asked + ["https://www.googleapis.com/auth/indexing"]
    token = _write_granted_token(tmp_path, granted, as_string=True)
    cfg = _oauth_config(make_config, token)
    try:
        build_google_credentials(cfg, asked)
    except Exception as exc:  # noqa: BLE001 - may still fail later on refresh
        assert "does not cover the scopes" not in str(exc)


def test_scope_guard_treats_absent_scopes_key_as_empty(make_config, tmp_path):
    """A token file with no "scopes" key must fail toward re-consent."""
    token = _write_granted_token(tmp_path, [], include=False)
    cfg = _oauth_config(make_config, token)
    with pytest.raises(MissingGoogleAuth, match="does not cover the scopes"):
        build_google_credentials(cfg, ["https://www.googleapis.com/auth/webmasters"])


def test_scope_guard_handles_malformed_token_file(make_config, tmp_path):
    """A token file holding a JSON list/scalar must not leak AttributeError."""
    p = tmp_path / "token.json"
    p.write_text("[]")
    cfg = _oauth_config(make_config, p)
    with pytest.raises(MissingGoogleAuth, match="does not cover the scopes"):
        build_google_credentials(cfg, ["https://www.googleapis.com/auth/webmasters"])


def test_scope_guard_runs_before_credential_construction(make_config, tmp_path):
    """Unparseable JSON must surface our remediation, not a json/AttributeError."""
    p = tmp_path / "token.json"
    p.write_text("{not json")
    cfg = _oauth_config(make_config, p)
    with pytest.raises(MissingGoogleAuth, match="seo-monster auth"):
        build_google_credentials(cfg, ["https://www.googleapis.com/auth/webmasters"])
