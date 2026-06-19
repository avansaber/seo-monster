"""Evidence-tiered banded scoring helper (roadmap F7).

Cross-cutting design law of the 2026 roadmap (design doc §1 P2): every score
SEOMonster emits is *banded* (low / moderate / high, not a false-precision
0-100) and *evidence-tagged* (Tier A causal / B correlational / C
informational) so the caller can tell a well-grounded signal from a
speculative one. This generalizes the hand-rolled weight+component pattern in
``content_opportunities`` into one reusable shape.

No new dependency: pure arithmetic over plain dicts, fully unit-testable.
"""

from __future__ import annotations

from typing import Any, Mapping

# Evidence tiers. A = causal / experimental support (e.g. the GEO paper's
# measured lifts); B = correlational; C = informational only (report, do not
# weight -- e.g. schema.org / llms.txt for AI citation, which the 2026 evidence
# says are neutral-to-negative).
TIER_A = "A"
TIER_B = "B"
TIER_C = "C"

# Default band cut points on a normalized 0..1 score.
_BAND_HIGH = 0.66
_BAND_MODERATE = 0.33


def _clamp01(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def band_for(score: float, *, high: float = _BAND_HIGH, moderate: float = _BAND_MODERATE) -> str:
    """Map a 0..1 score to a coarse band. We never present a bare number as the
    headline; the band is what the user reads."""
    if score >= high:
        return "high"
    if score >= moderate:
        return "moderate"
    return "low"


def banded_score(
    components: Mapping[str, float],
    weights: Mapping[str, float],
    *,
    evidence_tier: str,
    caveats: list[str] | None = None,
    high: float = _BAND_HIGH,
    moderate: float = _BAND_MODERATE,
) -> dict[str, Any]:
    """Combine 0..1 ``components`` by ``weights`` into a banded, evidence-tagged
    score envelope.

    Only keys present in BOTH ``components`` and ``weights`` contribute. The
    weighted sum is renormalized by the weight actually applied, so a missing
    component does not silently deflate the score -- instead ``applied_weight``
    drops, signalling reduced coverage. Returns the canonical scoring shape
    reused across the roadmap tools (band + score + components + weights +
    evidence_tier + caveats).
    """
    applied = {k: float(weights[k]) for k in components if k in weights}
    total_w = sum(applied.values())
    if total_w <= 0:
        score = 0.0
    else:
        score = sum(_clamp01(components[k]) * w for k, w in applied.items()) / total_w
    score = round(score, 4)
    return {
        "band": band_for(score, high=high, moderate=moderate),
        "score": score,
        "components": {k: round(_clamp01(components[k]), 4) for k in applied},
        "weights": {k: round(applied[k], 4) for k in applied},
        "applied_weight": round(total_w, 4),
        "evidence_tier": evidence_tier,
        "caveats": list(caveats or []),
    }
