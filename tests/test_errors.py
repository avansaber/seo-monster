"""Tests for the result envelope and error codes."""

from __future__ import annotations

from seo_mcp.errors import DOCS_BASE, ErrorCode, err, ok


def test_ok_envelope_shape():
    env = ok({"hello": "world"})
    assert env == {"ok": True, "data": {"hello": "world"}, "error": None}


def test_err_envelope_shape():
    env = err(
        ErrorCode.AUTH_MISSING,
        "gsc",
        "No creds",
        remediation="Set the env var",
        docs_url=DOCS_BASE + "auth",
        details={"hint": "x"},
    )
    assert env["ok"] is False
    assert env["data"] is None
    e = env["error"]
    assert e["code"] == "AUTH_MISSING"
    assert e["service"] == "gsc"
    assert e["message"] == "No creds"
    assert e["remediation"] == "Set the env var"
    assert e["docs_url"].endswith("#auth")
    assert e["details"] == {"hint": "x"}


def test_err_optional_fields_default_to_none():
    e = err(ErrorCode.NOT_FOUND, "cf", "missing")["error"]
    assert e["remediation"] is None
    assert e["docs_url"] is None
    assert e["details"] is None


def test_every_error_code_builds_a_valid_envelope():
    # The closed set: each code must produce a well-formed envelope.
    for code in ErrorCode:
        env = err(code, "general", f"message for {code}")
        assert env["ok"] is False
        assert env["error"]["code"] == str(code)
        # round-trips as a plain string (StrEnum), so json.dumps will work.
        assert isinstance(env["error"]["code"], str)


def test_error_codes_are_the_expected_closed_set():
    assert {c.value for c in ErrorCode} == {
        "AUTH_MISSING",
        "AUTH_INVALID",
        "SCOPE_INSUFFICIENT",
        "DESTRUCTIVE_DISABLED",
        "CONFIRM_REQUIRED",
        "NOT_FOUND",
        "INVALID_INPUT",
        "RATE_LIMITED",
        "SERVICE_DISABLED",
        "UPSTREAM_ERROR",
    }
