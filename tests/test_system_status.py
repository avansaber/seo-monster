"""Tests for the system_status tool: configured/auth_method matrix, probe
plumbing, and the tool catalog. Driven through the dispatcher with injected
fake clients so nothing touches the network."""

from __future__ import annotations

import json

import pytest

from seo_mcp.tools.system_status import group_tools, handle


# --- direct handler tests (no mcp needed) ---------------------------------


def test_unconfigured_reports_not_configured(make_config):
    cfg = make_config()  # empty env
    result = handle({}, cfg, {}, ["system_status"])
    assert result["ok"] is True
    svc = result["data"]["services"]
    assert svc["gsc"]["configured"] is False
    assert svc["gsc"]["auth_method"] is None
    assert svc["gsc"]["scopes"] is None
    assert svc["ga4"]["configured"] is False
    assert svc["cf"]["configured"] is False
    # PSI is always usable (anonymous endpoint), so configured even with no key.
    assert svc["psi"]["configured"] is True
    assert svc["psi"]["auth_method"] == "anonymous"


def test_oauth_is_reported_as_primary_method(make_config):
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
    )
    result = handle({}, cfg, {}, ["system_status"])
    gsc = result["data"]["services"]["gsc"]
    assert gsc["configured"] is True
    assert gsc["auth_method"] == "oauth"
    assert isinstance(gsc["scopes"], list) and gsc["scopes"]
    assert result["data"]["services"]["ga4"]["auth_method"] == "oauth"


def test_service_account_method_when_only_credentials_set(make_config):
    cfg = make_config(SEO_MCP_GOOGLE_CREDENTIALS="/c/sa.json")
    gsc = handle({}, cfg, {}, ["system_status"])["data"]["services"]["gsc"]
    assert gsc["auth_method"] == "service_account"


def test_oauth_wins_when_both_present(make_config):
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
        SEO_MCP_GOOGLE_CREDENTIALS="/c/sa.json",
    )
    gsc = handle({}, cfg, {}, ["system_status"])["data"]["services"]["gsc"]
    assert gsc["auth_method"] == "oauth"


def test_psi_api_key_method_when_key_present(make_config):
    cfg = make_config(PSI_API_KEY="AIzaKEY")
    psi = handle({}, cfg, {}, ["system_status"])["data"]["services"]["psi"]
    assert psi["auth_method"] == "api_key"


def test_indexnow_configured_when_key_present(make_config):
    cfg = make_config(SEO_MCP_INDEXNOW_KEY="abc123", SEO_MCP_INDEXNOW_KEY_LOCATION="https://x.com/abc123.txt")
    inx = handle({}, cfg, {}, ["system_status"])["data"]["services"]["indexnow"]
    assert inx["configured"] is True
    assert inx["auth_method"] == "shared_key"
    assert inx["key_location"] == "https://x.com/abc123.txt"


def test_indexnow_unconfigured(make_config):
    inx = handle({}, make_config(), {}, ["system_status"])["data"]["services"]["indexnow"]
    assert inx["configured"] is False
    assert inx["auth_method"] is None


def test_cf_configured_when_token_present(make_config):
    cfg = make_config(CF_API_TOKEN="cftoken", CF_ZONE="example.com")
    cf = handle({}, cfg, {}, ["system_status"])["data"]["services"]["cf"]
    assert cf["configured"] is True
    assert cf["auth_method"] == "api_token"
    assert cf["default_zone"] == "example.com"


def test_destructive_flag_surfaced(make_config):
    cfg = make_config(SEO_MCP_ALLOW_DESTRUCTIVE="true")
    assert handle({}, cfg, {}, ["system_status"])["data"]["destructive_enabled"] is True


def test_config_source_is_env_when_no_file(make_config):
    data = handle({}, make_config(), {}, ["system_status"])["data"]
    assert data["config_source"] == "env"


def test_config_source_reports_toml_path(tmp_path, make_config):
    path = tmp_path / "seomonster.toml"
    path.write_text('[gsc]\ndefault_site = "sc-domain:from-toml.com"\n')
    cfg = make_config(config_path=str(path))
    data = handle({}, cfg, {}, ["system_status"])["data"]
    assert data["config_source"] == str(path)


def test_ga4_reason_when_no_google(make_config):
    data = handle({}, make_config(), {}, ["system_status"])["data"]
    assert data["services"]["ga4"]["reason"] == "no Google auth configured"


def test_ga4_reason_when_google_but_no_property(make_config):
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
    )
    data = handle({}, cfg, {}, ["system_status"])["data"]
    assert "no default property" in data["services"]["ga4"]["reason"]


def test_ga4_reason_cleared_when_property_set(make_config):
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
        SEO_MCP_GA4_PROPERTY_ID="properties/123",
    )
    data = handle({}, cfg, {}, ["system_status"])["data"]
    assert data["services"]["ga4"]["reason"] is None


def test_default_site_and_property_surfaced(make_config):
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
        SEO_MCP_GSC_DEFAULT_SITE="sc-domain:example.com",
        SEO_MCP_GA4_PROPERTY_ID="properties/123",
    )
    svc = handle({}, cfg, {}, ["system_status"])["data"]["services"]
    assert svc["gsc"]["default_site"] == "sc-domain:example.com"
    assert svc["ga4"]["default_property"] == "properties/123"


# --- probe plumbing -------------------------------------------------------


def test_probe_false_makes_no_client_calls(make_config, fake_client):
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
        CF_API_TOKEN="cftoken",
    )
    gsc_client = fake_client(ok=True)
    cf_client = fake_client(ok=True)
    clients = {"gsc": gsc_client, "cf": cf_client}
    result = handle({"probe": False}, cfg, clients, ["system_status"])
    assert gsc_client.calls == 0
    assert cf_client.calls == 0
    assert result["data"]["services"]["gsc"]["reachable"] is None
    assert result["data"]["services"]["cf"]["reachable"] is None


def test_probe_true_runs_configured_clients(make_config, fake_client):
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
        CF_API_TOKEN="cftoken",
    )
    gsc_client = fake_client(ok=True)
    cf_client = fake_client(ok=False)  # simulates an upstream failure
    clients = {"gsc": gsc_client, "cf": cf_client}
    result = handle({"probe": True}, cfg, clients, ["system_status"])
    assert gsc_client.calls == 1
    assert cf_client.calls == 1
    assert result["data"]["services"]["gsc"]["reachable"] is True
    assert result["data"]["services"]["cf"]["reachable"] is False


def test_probe_true_with_no_client_wired_stays_null(make_config):
    # Configured service but no client in the mapping (Phase 1 reality).
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
    )
    result = handle({"probe": True}, cfg, {}, ["system_status"])
    assert result["data"]["services"]["gsc"]["reachable"] is None


def test_ga4_probe_runs_only_with_property(make_config, fake_client):
    # GA4 needs a property to probe against. With google configured but no
    # property, reachable stays null even when probe is on and a client wired.
    no_prop = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
    )
    ga4_client = fake_client(ok=True)
    result = handle({"probe": True}, no_prop, {"ga4": ga4_client}, ["system_status"])
    assert result["data"]["services"]["ga4"]["reachable"] is None
    assert ga4_client.calls == 0

    # With a property configured, the GA4 probe runs.
    with_prop = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
        SEO_MCP_GA4_PROPERTY_ID="properties/123",
    )
    ga4_client2 = fake_client(ok=True)
    result2 = handle({"probe": True}, with_prop, {"ga4": ga4_client2}, ["system_status"])
    assert result2["data"]["services"]["ga4"]["reachable"] is True
    assert ga4_client2.calls == 1


def test_probe_skips_unconfigured_service(make_config, fake_client):
    # CF not configured: even with a client wired and probe on, reachable stays
    # null and the client is not called.
    cfg = make_config()
    cf_client = fake_client(ok=True)
    result = handle({"probe": True}, cfg, {"cf": cf_client}, ["system_status"])
    assert cf_client.calls == 0
    assert result["data"]["services"]["cf"]["reachable"] is None


# --- tool catalog ---------------------------------------------------------


def test_group_tools_buckets_by_prefix():
    catalog = group_tools(
        ["system_status", "gsc_top_queries", "ga4_run_report", "psi_analyze", "cf_list_zones"]
    )
    assert catalog["general"] == ["system_status"]
    assert catalog["gsc"] == ["gsc_top_queries"]
    assert catalog["ga4"] == ["ga4_run_report"]
    assert catalog["psi"] == ["psi_analyze"]
    assert catalog["cf"] == ["cf_list_zones"]


def test_group_tools_always_has_all_service_keys():
    catalog = group_tools([])
    assert set(catalog) == {"gsc", "ga4", "psi", "cf", "indexnow", "crux", "ai", "technical", "content", "discovery", "general"}
    assert all(v == [] for v in catalog.values())


def test_group_tools_routes_v03_technical_tools():
    catalog = group_tools([
        "inspect_meta",
        "check_canonical",
        "mixed_content_check",
        "redirect_chain_audit",
        "robots_txt_validate",
        "sitemap_validate",
        "sitemap_health",
        "crux_history",
        "system_status",
        "gsc_top_queries",
    ])
    assert set(catalog["technical"]) == {
        "inspect_meta", "check_canonical", "mixed_content_check",
        "redirect_chain_audit", "robots_txt_validate",
        "sitemap_validate", "sitemap_health",
    }
    assert catalog["crux"] == ["crux_history"]
    assert catalog["general"] == ["system_status"]
    assert catalog["gsc"] == ["gsc_top_queries"]


def test_catalog_in_phase_1_lists_only_system_status(make_config):
    result = handle({}, make_config(), {}, ["system_status"])
    assert result["data"]["tools"]["general"] == ["system_status"]
    assert result["data"]["tools"]["gsc"] == []


# --- serialization + dispatcher path --------------------------------------


def test_result_is_json_serializable(make_config):
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
    )
    result = handle({}, cfg, {}, ["system_status"])
    json.dumps(result)  # must not raise


def test_dispatch_routes_system_status(make_config, make_dispatcher, fake_client):
    pytest.importorskip("mcp")
    cfg = make_config(
        SEO_MCP_GOOGLE_OAUTH_CLIENT="/c/client.json",
        SEO_MCP_GOOGLE_TOKEN="/c/token.json",
    )
    dispatch = make_dispatcher(clients={"gsc": fake_client(ok=True)})
    result = dispatch("system_status", {"probe": True}, cfg)
    assert result["ok"] is True
    assert result["data"]["services"]["gsc"]["reachable"] is True


def test_dispatch_unknown_tool_returns_invalid_input(make_config, make_dispatcher):
    pytest.importorskip("mcp")
    dispatch = make_dispatcher()
    result = dispatch("does_not_exist", {}, make_config())
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"
