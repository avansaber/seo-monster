"""Offline tests for robots_ai_posture.

The tool is deterministic and takes no clients (no network, no writes), so we
drive the handler directly with an empty clients dict."""

from __future__ import annotations

from seo_mcp.errors import ErrorCode
from seo_mcp.tools import robots_posture_tools


def _call(arguments, make_config):
    return robots_posture_tools.robots_ai_posture(arguments, make_config(), {})


def test_content_authority_recommends_no_train_and_emits_artifact(make_config):
    result = _call({"goal": "content_authority"}, make_config)
    assert result["ok"] is True
    d = result["data"]
    posture = d["recommendation"]["posture"]
    assert posture == {"search": "yes", "ai-input": "yes", "ai-train": "no"}
    artifact = d["artifact"]
    assert "Content-Signal:" in artifact["robots_txt"]
    assert "Allow:" in artifact["robots_txt"]
    # The not-a-ranking-factor caveat must be present.
    assert "ranking factor" in d["caveat"].lower()
    assert "Content-Signal: search=yes, ai-input=yes, ai-train=no" == artifact["content_signal_line"]


def test_protect_ip_declines_ai_input_and_train(make_config):
    result = _call({"goal": "protect_ip"}, make_config)
    posture = result["data"]["recommendation"]["posture"]
    assert posture == {"search": "yes", "ai-input": "no", "ai-train": "no"}


def test_missing_goal_defaults_to_content_authority_with_alternatives(make_config):
    result = _call({}, make_config)
    d = result["data"]
    assert d["goal"] == "content_authority"
    assert d["default_applied"] is True
    assert d["recommendation"]["posture"]["ai-train"] == "no"
    # The trade-off menu (alternatives) is always present.
    assert isinstance(d["alternatives"], list) and len(d["alternatives"]) >= 2
    for alt in d["alternatives"]:
        assert "posture" in alt and "tradeoff" in alt


def test_sitemap_url_appears_in_artifact(make_config):
    sitemap = "https://www.example.com/sitemap.xml"
    result = _call({"goal": "content_authority", "sitemap_url": sitemap}, make_config)
    artifact = result["data"]["artifact"]
    assert artifact["sitemap_url"] == sitemap
    assert f"Sitemap: {sitemap}" in artifact["robots_txt"]


def test_unknown_goal_returns_invalid_input(make_config):
    result = _call({"goal": "world_domination"}, make_config)
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.INVALID_INPUT
    assert "world_domination" in result["error"]["message"]


def test_caveat_present_on_every_success(make_config):
    for goal in (None, "content_authority", "maximize_visibility", "protect_ip"):
        args = {} if goal is None else {"goal": goal}
        result = _call(args, make_config)
        assert result["ok"] is True
        assert result["data"]["caveat"]
        assert "googlebot" in result["data"]["caveat"].lower()
