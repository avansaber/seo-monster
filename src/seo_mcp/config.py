"""Configuration resolution: environment-first, TOML-file fallback.

``load_config`` is a pure function over an injected env mapping and an optional
config path, so it is trivially testable. Environment variables always win over
the config file. Nothing here touches the network or the real ``os.environ``
unless the caller passes them in.

Config file location: ``SEO_MCP_CONFIG`` if set, else
``~/.config/seo-mcp/config.toml``. The file is optional; a fully env-driven
setup needs no file at all.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEFAULT_CONFIG_PATH = "~/.config/seo-mcp/config.toml"
DATA_STATE_DEFAULT = "all"
_DATA_STATES = {"all", "final"}
_TRUTHY = {"true", "1", "yes", "on"}


@dataclass(frozen=True)
class GoogleAuthConfig:
    """Google credential locations. OAuth is the primary path; service account
    is the advanced headless alternative. Whichever is present is used."""

    oauth_client: str | None = None   # client-secrets JSON path
    token: str | None = None          # writable cached-token path (OAuth)
    credentials: str | None = None    # service-account key JSON path


@dataclass(frozen=True)
class Config:
    """Fully resolved configuration for one server process."""

    google: GoogleAuthConfig
    gsc_default_site: str | None
    gsc_data_state: str
    ga4_property_id: str | None
    psi_api_key: str | None
    cf_api_token: str | None
    cf_zone: str | None
    indexnow_key: str | None
    indexnow_key_location: str | None
    allow_destructive: bool
    source_path: str | None  # the config file actually read, or None


def _truthy(value: Any) -> bool:
    """Interpret a config value as a boolean. Strings use a small allow-list;
    real booleans (from TOML) pass through."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _TRUTHY


def _clean(value: Any) -> str | None:
    """Normalize a scalar to a non-empty stripped string, else None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _expand_path(value: str | None) -> str | None:
    """Expand ``~`` and shell-style ``$VAR`` / ``${VAR}`` references in a path.

    Defensive: some hosts pass file-path defaults from the MCPB manifest without
    resolving the manifest's ``${HOME}`` substitution before injecting them as
    env vars. We re-expand here so a literal ``${HOME}/path`` or ``~/path``
    always resolves to a real filesystem location.
    """
    if value is None:
        return None
    return os.path.expanduser(os.path.expandvars(value))


def _load_file(config_path: str | None, env: Mapping[str, str]) -> tuple[dict[str, Any], str | None]:
    """Read and parse the TOML config file if present.

    Resolution order for the path: explicit ``config_path`` arg, then
    ``SEO_MCP_CONFIG`` env, then the default location. Returns the parsed dict
    (empty if no readable file) and the path actually read (or None).
    """
    candidate = config_path or env.get("SEO_MCP_CONFIG") or DEFAULT_CONFIG_PATH
    path = Path(candidate).expanduser()
    if not path.is_file():
        return {}, None
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh), str(path)
    except (OSError, tomllib.TOMLDecodeError):
        # A malformed or unreadable file must not crash the server; fall back
        # to env-only resolution.
        return {}, None


def load_config(
    env: Mapping[str, str] | None = None,
    config_path: str | None = None,
) -> Config:
    """Resolve configuration from environment and optional TOML file.

    Args:
        env: mapping to read env vars from. Defaults to ``os.environ``.
        config_path: explicit path to the TOML file. Overrides ``SEO_MCP_CONFIG``
            and the default location.

    Returns:
        A frozen ``Config``. Missing values are ``None`` (or service defaults).
    """
    env = os.environ if env is None else env
    file_data, source_path = _load_file(config_path, env)

    google_file = file_data.get("google", {})
    gsc_file = file_data.get("gsc", {})
    ga4_file = file_data.get("ga4", {})
    psi_file = file_data.get("psi", {})
    cf_file = file_data.get("cloudflare", {})
    indexnow_file = file_data.get("indexnow", {})
    server_file = file_data.get("server", {})

    def pick(env_key: str, file_value: Any) -> str | None:
        """Env wins; fall back to the file value."""
        env_value = _clean(env.get(env_key))
        if env_value is not None:
            return env_value
        return _clean(file_value)

    # Service-account key: SEO_MCP_GOOGLE_CREDENTIALS, then the standard
    # GOOGLE_APPLICATION_CREDENTIALS, then the file. Path-like fields are
    # expanded so a literal "${HOME}/..." or "~/..." resolves correctly.
    sa_credentials = _expand_path(
        _clean(env.get("SEO_MCP_GOOGLE_CREDENTIALS"))
        or _clean(env.get("GOOGLE_APPLICATION_CREDENTIALS"))
        or _clean(google_file.get("credentials"))
    )

    google = GoogleAuthConfig(
        oauth_client=_expand_path(pick("SEO_MCP_GOOGLE_OAUTH_CLIENT", google_file.get("oauth_client"))),
        token=_expand_path(pick("SEO_MCP_GOOGLE_TOKEN", google_file.get("token"))),
        credentials=sa_credentials,
    )

    data_state = (pick("SEO_MCP_DATA_STATE", gsc_file.get("data_state")) or DATA_STATE_DEFAULT).lower()
    if data_state not in _DATA_STATES:
        data_state = DATA_STATE_DEFAULT

    # allow_destructive: env truthiness wins if the env var is present at all;
    # otherwise the file's boolean; otherwise False.
    if env.get("SEO_MCP_ALLOW_DESTRUCTIVE") is not None:
        allow_destructive = _truthy(env.get("SEO_MCP_ALLOW_DESTRUCTIVE"))
    else:
        allow_destructive = _truthy(server_file.get("allow_destructive"))

    return Config(
        google=google,
        gsc_default_site=pick("SEO_MCP_GSC_DEFAULT_SITE", gsc_file.get("default_site")),
        gsc_data_state=data_state,
        ga4_property_id=pick("SEO_MCP_GA4_PROPERTY_ID", ga4_file.get("property_id")),
        psi_api_key=pick("PSI_API_KEY", psi_file.get("api_key")),
        cf_api_token=pick("CF_API_TOKEN", cf_file.get("api_token")),
        cf_zone=pick("CF_ZONE", cf_file.get("zone")),
        indexnow_key=pick("SEO_MCP_INDEXNOW_KEY", indexnow_file.get("key")),
        indexnow_key_location=pick("SEO_MCP_INDEXNOW_KEY_LOCATION", indexnow_file.get("key_location")),
        allow_destructive=allow_destructive,
        source_path=source_path,
    )
