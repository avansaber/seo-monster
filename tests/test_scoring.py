"""Tests for the evidence-tiered banded scoring helper (roadmap F7)."""

from __future__ import annotations

from seo_mcp.tools._scoring import TIER_A, TIER_C, band_for, banded_score


def test_band_for_thresholds():
    assert band_for(0.9) == "high"
    assert band_for(0.66) == "high"
    assert band_for(0.5) == "moderate"
    assert band_for(0.33) == "moderate"
    assert band_for(0.1) == "low"


def test_banded_score_simple_mean_with_equal_weights():
    out = banded_score(
        {"a": 1.0, "b": 0.0},
        {"a": 0.5, "b": 0.5},
        evidence_tier=TIER_A,
    )
    assert out["score"] == 0.5
    assert out["band"] == "moderate"
    assert out["evidence_tier"] == "A"
    assert out["applied_weight"] == 1.0


def test_banded_score_renormalizes_on_missing_component():
    # Only "a" is present; weight for "b" is not applied, so applied_weight=0.4
    # and the score is just a's clamped value (no silent deflation).
    out = banded_score({"a": 1.0}, {"a": 0.4, "b": 0.6}, evidence_tier=TIER_A)
    assert out["score"] == 1.0
    assert out["band"] == "high"
    assert out["applied_weight"] == 0.4
    assert "b" not in out["components"]


def test_banded_score_clamps_components():
    out = banded_score({"a": 5.0, "b": -3.0}, {"a": 0.5, "b": 0.5}, evidence_tier=TIER_C)
    assert out["components"]["a"] == 1.0
    assert out["components"]["b"] == 0.0
    assert out["score"] == 0.5


def test_banded_score_zero_weight_is_safe():
    out = banded_score({"a": 1.0}, {"a": 0.0}, evidence_tier=TIER_A)
    assert out["score"] == 0.0
    assert out["band"] == "low"


def test_caveats_passed_through():
    out = banded_score({"a": 1.0}, {"a": 1.0}, evidence_tier=TIER_A, caveats=["x", "y"])
    assert out["caveats"] == ["x", "y"]
