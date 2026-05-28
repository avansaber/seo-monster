"""Tests for the MCP prompts/ capability and the workflow renderers."""

from __future__ import annotations

import pytest

from seo_mcp import prompts


# --- registry contract ----------------------------------------------------


def test_v04_prompts_registered():
    names = prompts.prompt_names()
    expected = {
        "post_deploy_verify",
        "weekly_review",
        "content_audit",
        "migration_check",
        "technical_seo_audit",
        "structured_data_audit",
    }
    assert set(names) == expected
    assert len(names) == len(expected)


def test_every_prompt_has_required_metadata_fields():
    required = {"name", "description", "arguments"}
    for p in prompts.PROMPTS:
        assert required <= set(p), f"{p.get('name')}: missing {required - set(p)}"
        for arg in p["arguments"]:
            assert set(arg) >= {"name", "description", "required"}


def test_prompts_load_as_mcp_models():
    # Server uses Prompt.model_validate; pin that round-trip works for every
    # registered prompt so a malformed entry fails fast in CI.
    pytest.importorskip("mcp")
    from mcp.types import Prompt

    for p in prompts.PROMPTS:
        Prompt.model_validate(p)


def test_render_unknown_prompt_returns_error_string():
    body = prompts.render("does_not_exist", {})
    assert "Unknown prompt" in body
    # Lists the known prompts so the LLM can suggest one.
    for name in prompts.prompt_names():
        assert name in body


# --- per-prompt rendering -------------------------------------------------


def test_post_deploy_verify_includes_arguments_and_workflow():
    body = prompts.render("post_deploy_verify", {"urls": ["https://x.com/a", "https://x.com/b"], "zone": "x.com"})
    # Workflow steps
    assert "cf_purge_cache" in body
    assert "gsc_request_indexing" in body
    assert "indexnow_bulk_submit" in body
    assert "psi_analyze" in body
    # Arguments echoed
    assert "x.com" in body
    # Destructive flag noted (so the LLM does not get surprised by the gate)
    assert "SEO_MCP_ALLOW_DESTRUCTIVE" in body


def test_post_deploy_verify_skip_psi():
    body = prompts.render("post_deploy_verify", {"urls": ["https://x.com/a"], "skip_psi": "true"})
    assert "skip_psi=true" in body or "Skipped" in body
    # The PSI step is annotated as skipped; the call should not be in the
    # actionable steps for this run.
    assert body.count("psi_analyze") <= 1  # at most one mention (the skip note)


def test_weekly_review_substitutes_days_and_site_url():
    body = prompts.render("weekly_review", {"days": 14, "site_url": "sc-domain:example.com"})
    # days substituted into the gsc_compare_periods call shape
    assert '"days": 14' in body
    # site_url passed through to each call
    assert '"site_url": "sc-domain:example.com"' in body
    # All 5 tools mentioned
    for tool in ("gsc_compare_periods", "gsc_query_opportunities", "gsc_query_gaps", "ga4_organic_search_overview"):
        assert tool in body


def test_weekly_review_defaults_to_7_days():
    body = prompts.render("weekly_review", {})
    assert '"days": 7' in body


def test_content_audit_workflow_steps():
    body = prompts.render("content_audit", {"days": 14, "top_n_queries": 5})
    assert "gsc_top_queries" in body
    assert "gsc_top_pages_by_query" in body
    assert '"days": 14' in body
    assert '"limit": 5' in body
    # The three recommendation classes the LLM should choose from
    for verdict in ("Consolidate", "Differentiate", "Accept"):
        assert verdict in body


def test_migration_check_workflow_steps():
    body = prompts.render("migration_check", {"urls": ["https://x.com/a"]})
    assert "gsc_batch_inspect_urls" in body
    assert "gsc_list_sitemaps" in body
    # Asks the LLM to compare google_canonical vs user_canonical
    assert "google_canonical" in body and "user_canonical" in body


# --- end-to-end via server dispatcher ------------------------------------


def test_get_prompt_returns_user_role_message():
    pytest.importorskip("mcp")
    import asyncio
    from seo_mcp import server

    res = asyncio.run(server.get_prompt("weekly_review", {"days": 7}))
    assert res.description
    assert len(res.messages) == 1
    msg = res.messages[0]
    assert msg.role == "user"
    assert msg.content.type == "text"
    assert "gsc_compare_periods" in msg.content.text


def test_list_prompts_matches_registry():
    pytest.importorskip("mcp")
    import asyncio
    from seo_mcp import server

    listed = asyncio.run(server.list_prompts())
    assert {p.name for p in listed} == set(prompts.prompt_names())
    assert len(listed) == len(prompts.prompt_names())


def test_system_status_surfaces_prompt_names(make_config):
    pytest.importorskip("mcp")
    from seo_mcp.tools.system_status import handle

    data = handle({}, make_config(), {}, ["system_status"], ["a", "b"])["data"]
    assert data["prompts"] == ["a", "b"]
