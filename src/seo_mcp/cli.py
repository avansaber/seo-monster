"""CLI subcommands for SEOMonster.

The default invocation (``seo-monster`` with no arguments) starts the MCP
server over stdio. The dispatch in ``server.main()`` peels off subcommands
before that. Currently one subcommand is supported:

    seo-monster auth

Run the one-time interactive Google OAuth consent flow from a terminal,
write the cached token to ``SEO_MCP_GOOGLE_TOKEN``, and exit. This must be
run *before* using SEOMonster from a GUI host like Claude Desktop, because
those hosts launch the server as an MCP subprocess that has no way to wait
for a real human to complete browser consent.
"""

from __future__ import annotations

import getpass
import sys
from typing import Callable, Sequence

from .auth import MissingGoogleAuth, required_scopes
from .clients.google_auth import run_oauth_consent
from .config import (
    load_config,
    read_config_toml,
    resolve_config_path,
    write_config_toml,
)


def auth_main(argv: Sequence[str] | None = None) -> int:
    """Run the OAuth consent flow. Returns a Unix exit code.

    Usage:
        seo-monster auth

    The flow reuses the same scope set the server requests at runtime, so a
    single consent unlocks every Google-backed tool.
    """
    # argv kept for future flags (e.g. --read-only). Currently unused.
    del argv

    config = load_config()
    method = _which_google_auth(config)
    if method == "service_account":
        print(
            "Service-account auth is configured (SEO_MCP_GOOGLE_CREDENTIALS). "
            "No interactive consent step is needed; the key file IS the credential."
        )
        return 0
    if method is None:
        print(
            "ERROR: no Google auth configured. Set SEO_MCP_GOOGLE_OAUTH_CLIENT "
            "and SEO_MCP_GOOGLE_TOKEN, then re-run `seo-monster auth`.",
            file=sys.stderr,
        )
        return 2

    scopes = required_scopes(config)
    print(
        "Opening a browser for one-time Google OAuth consent. Approve the "
        "requested scopes, then return here."
    )
    try:
        path = run_oauth_consent(config, scopes)
    except MissingGoogleAuth as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Token cached at {path} (0600). MCP server is now ready to use.")
    return 0


def _which_google_auth(config) -> str | None:
    """Mirror of auth.google_auth_method but kept locally so we don't depend
    on import order at CLI startup. OAuth wins when both are configured."""
    if config.google.oauth_client:
        return "oauth"
    if config.google.credentials:
        return "service_account"
    return None


# --- `seo-monster setup` --------------------------------------------------


def _ask_text(prompt_fn: Callable[[str], str], label: str, current: str | None) -> str | None:
    """Prompt for a plain (non-secret) value. Enter keeps the current value (if
    any), or skips. Returns the resolved value or None."""
    if current:
        raw = prompt_fn(f"{label} [{current}]: ").strip()
        return raw or current
    raw = prompt_fn(f"{label} (Enter to skip): ").strip()
    return raw or None


def _ask_secret(secret_fn: Callable[[str], str], label: str, current: str | None) -> str | None:
    """Prompt for a secret without echoing. Enter keeps the existing value (if
    any), or skips. The current value is never displayed."""
    if current:
        raw = secret_fn(f"{label} [keep existing, Enter to keep]: ").strip()
        return raw or current
    raw = secret_fn(f"{label} (Enter to skip): ").strip()
    return raw or None


def validate_cloudflare(token: str) -> tuple[str, str]:
    """Validate a CF token by listing zones. Returns (status, message) where
    status is 'ok' | 'rejected' | 'unreachable'. Module-level so tests can
    monkeypatch it without touching the network."""
    from .clients.cloudflare import CfClient
    from .clients.errors import ApiError
    from .errors import ErrorCode

    try:
        zones = CfClient(token).list_zones()
        return "ok", f"{len(zones)} zone(s) visible"
    except ApiError as exc:
        # /zones is a fixed endpoint with no user-supplied params, so a 400 ->
        # INVALID_INPUT here can only mean the token / auth header is
        # unparseable (CF code 6003). A typo'd short token returns 400, not
        # 401/403, so INVALID_INPUT must reject too (tester FEEDBACK §12c.i).
        rejected = {
            ErrorCode.AUTH_INVALID,
            ErrorCode.AUTH_MISSING,
            ErrorCode.SCOPE_INSUFFICIENT,
            ErrorCode.INVALID_INPUT,
        }
        return ("rejected" if exc.code in rejected else "unreachable"), exc.message
    except Exception as exc:  # network boundary
        return "unreachable", str(exc)


def validate_indexnow(key: str, key_location: str | None) -> tuple[str, str]:
    """Validate IndexNow by fetching the key-file URL and comparing its body to
    the key. Skipped (status 'skipped') when no location is given; IndexNow's
    key file is per-host and the host may not be known at setup time."""
    if not key_location:
        return "skipped", "no key-file URL given; validated on first submit"
    import urllib.error
    import urllib.request

    from . import __version__

    # Send the project's branded User-Agent. Cloudflare's Browser Integrity
    # Check 403s the default Python-urllib UA (CF error 1010), which would make
    # every IndexNow key file hosted behind CF look unreachable on first setup
    # (tester FEEDBACK §12c.ii). Branded UAs are not blocked.
    req = urllib.request.Request(
        key_location,
        headers={"User-Agent": f"SEOMonster/{__version__} (+https://seomonster.avansaber.com)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (trusted user URL)
            body = resp.read().decode("utf-8", "replace").strip()
    except Exception as exc:  # network boundary
        return "unreachable", f"could not fetch {key_location}: {exc}"
    if body == key.strip():
        return "ok", "key file matches the key"
    return "rejected", "key-file body does not equal the key"


def _report(label: str, status: str, message: str) -> None:
    glyph = {"ok": "OK", "rejected": "FAILED", "warn": "WARN", "unreachable": "SKIPPED", "skipped": "SKIPPED"}.get(status, "?")
    print(f"  [{glyph}] {label}: {message}")


def setup_main(
    argv: Sequence[str] | None = None,
    *,
    prompt: Callable[[str], str] | None = None,
    secret_prompt: Callable[[str], str] | None = None,
) -> int:
    """Run the interactive setup, converting EOF / Ctrl-C into a clean exit.

    A wrapper around ``_setup_run`` so that invoking ``seo-monster setup`` in a
    non-interactive context (piped stdin, no TTY) or aborting with Ctrl-C exits
    with a one-line message and code 1 instead of dumping a traceback. No config
    is written on abort (the write is the last step of ``_setup_run``).
    """
    try:
        return _setup_run(argv, prompt=prompt, secret_prompt=secret_prompt)
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled; no changes were written.", file=sys.stderr)
        return 1


def _setup_run(
    argv: Sequence[str] | None = None,
    *,
    prompt: Callable[[str], str] | None = None,
    secret_prompt: Callable[[str], str] | None = None,
) -> int:
    """Interactive credential setup. Writes a 0600 config file the server reads.

    Usage:
        seo-monster setup

    Collects the non-OAuth credentials (Cloudflare, PageSpeed Insights, IndexNow)
    and the convenience defaults (GSC property, GA4 property), validates what it
    can against the upstream APIs, and writes them to the config file resolved by
    ``config.resolve_config_path`` (default ``~/.config/seo-mcp/config.toml``,
    0600). Google OAuth stays on the separate ``seo-monster auth`` flow.

    Re-runnable: existing values are shown as defaults and kept when a field is
    left blank, so a re-run to add one credential never wipes the others.
    Environment variables still override the file at runtime.

    A credential whose validation is *rejected* is NOT written (so a known-bad
    token never lands on disk); one that is merely *unreachable* (offline) is
    written with a note. ``prompt`` / ``secret_prompt`` are injectable for tests.
    """
    del argv  # reserved for future flags (e.g. --non-interactive)
    ask = prompt or input
    ask_secret = secret_prompt or getpass.getpass

    config_path = resolve_config_path()
    existing = read_config_toml(config_path)

    def table(name: str) -> dict:
        value = existing.get(name, {})
        return value if isinstance(value, dict) else {}

    cf_e, psi_e, idx_e = table("cloudflare"), table("psi"), table("indexnow")
    gsc_e, ga4_e = table("gsc"), table("ga4")

    print(
        "SEOMonster setup. This writes your credentials to\n"
        f"  {config_path}\n"
        "with 0600 permissions, so your MCP host config needs no secrets.\n"
        "Press Enter to skip a service you do not use (or to keep an existing "
        "value).\n"
    )

    sections: dict[str, dict] = {
        "google": dict(table("google")),  # preserve any Google paths already set
        "gsc": dict(gsc_e),
        "ga4": dict(ga4_e),
        "psi": dict(psi_e),
        "cloudflare": dict(cf_e),
        "indexnow": dict(idx_e),
    }

    # --- Cloudflare ---
    print("Cloudflare (read tools + cache purge):")
    cf_token = _ask_secret(ask_secret, "  Cloudflare API token", cf_e.get("api_token"))
    if cf_token and cf_token != cf_e.get("api_token"):
        status, msg = validate_cloudflare(cf_token)
        _report("Cloudflare token", status, msg)
        if status == "rejected":
            cf_token = None  # never persist a known-bad token
            print("    -> not saved; fix the token and re-run `seo-monster setup`.")
    sections["cloudflare"]["api_token"] = cf_token
    sections["cloudflare"]["zone"] = _ask_text(ask, "  Default Cloudflare zone (hostname)", cf_e.get("zone"))

    # --- PageSpeed Insights ---
    print("PageSpeed Insights (a key relaxes the shared anonymous rate limit):")
    psi_key = _ask_secret(ask_secret, "  PSI API key", psi_e.get("api_key"))
    if psi_key and psi_key != psi_e.get("api_key"):
        # R5: do NOT burn quota with a full analyze; the key is exercised on
        # first real call. Store as-is with a note.
        _report("PSI key", "skipped", "stored; validated on first analyze (avoids burning quota)")
    sections["psi"]["api_key"] = psi_key

    # --- IndexNow ---
    print("IndexNow (notify Bing/Yandex/etc.):")
    idx_key = _ask_secret(ask_secret, "  IndexNow key", idx_e.get("key"))
    idx_loc = _ask_text(ask, "  IndexNow key-file URL (optional override)", idx_e.get("key_location"))
    if idx_key:
        status, msg = validate_indexnow(idx_key, idx_loc)
        _report("IndexNow", status, msg)
        if status == "rejected":
            print("    -> key/file mismatch; saving anyway, but fix the hosted file.")
    sections["indexnow"]["key"] = idx_key
    sections["indexnow"]["key_location"] = idx_loc

    # --- convenience defaults (format-checked only; full check needs OAuth) ---
    print("Defaults (optional):")
    gsc_site = _ask_text(ask, "  Default GSC property (sc-domain:example.com or https URL)", gsc_e.get("default_site"))
    if gsc_site and not (gsc_site.startswith("sc-domain:") or gsc_site.startswith("http")):
        _report("GSC property", "warn", "should start with 'sc-domain:' or 'https://'; saved, but verify it")
    sections["gsc"]["default_site"] = gsc_site

    ga4_prop = _ask_text(ask, "  Default GA4 property (properties/123456789 or 123456789)", ga4_e.get("property_id"))
    if ga4_prop and not ga4_prop.replace("properties/", "").isdigit():
        _report("GA4 property", "warn", "expected a numeric id like 'properties/123456789'; saved, but verify it")
    sections["ga4"]["property_id"] = ga4_prop

    written = write_config_toml(config_path, sections)
    print(f"\nWrote {written} (0600).")

    if sections["google"].get("oauth_client"):
        print("Google OAuth client is configured. Run `seo-monster auth` to complete consent.")
    else:
        print(
            "Google (Search Console + GA4) uses a separate one-time step: set "
            "SEO_MCP_GOOGLE_OAUTH_CLIENT + SEO_MCP_GOOGLE_TOKEN, then run "
            "`seo-monster auth`."
        )
    print("Tip: run `seo-monster` (or `system_status` from your host) to verify.")
    return 0
