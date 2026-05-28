"""lighthouse_budget (1). Performance budget check on top of PageSpeed Insights.

The user supplies a budget dict like::

    {
      "performance": 80,        # 0-100 Lighthouse score (higher is better)
      "accessibility": 90,
      "LCP_ms": 2500,           # milliseconds (lower is better)
      "CLS": 0.1,               # unitless shift score (lower is better)
      "TBT_ms": 200             # milliseconds (lower is better)
    }

We run ``psi_analyze`` against the URL (reusing PsiClient) and verdict
each budget entry against the live measurement. Direction is decided per
metric: Lighthouse category scores must be >= the budget; latency / shift
metrics must be <= the budget. The per-metric verdict and the overall
verdict make this trivially usable as a CI / pre-deploy gate inside an
LLM session.

**Budget keys must use the canonical names below**: a typo gets surfaced
as a non-fatal finding with a "did you mean" hint rather than silently
ignored (Round-5 validation §10b.U pointed out the silent path made
budgets look like they were applied when they weren't).

We deliberately do not add a new client. This tool is a pure adapter
sitting on top of the existing PSI client.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import ANNOT_READ, require_client


_SERVICE = "psi"
_REMEDIATION = "PageSpeed Insights needs no key to work, but a PSI_API_KEY relaxes rate limits."


# Direction per metric: True means higher is better (Lighthouse scores),
# False means lower is better (latency, shift). The key is the budget key
# the user supplies; the value is the path into the psi_analyze envelope.
_BUDGET_KEYS: dict[str, tuple[str, str, bool]] = {
    # category-score budgets (0-100; >= budget is pass)
    "performance": ("lighthouse_scores", "performance", True),
    "accessibility": ("lighthouse_scores", "accessibility", True),
    "best-practices": ("lighthouse_scores", "best-practices", True),
    "seo": ("lighthouse_scores", "seo", True),
    # lab CWV display values; we parse the ms / score off the displayValue
    "LCP_ms": ("lab_core_web_vitals", "LCP", False),
    "FCP_ms": ("lab_core_web_vitals", "FCP", False),
    "TBT_ms": ("lab_core_web_vitals", "TBT", False),
    "TTI_ms": ("lab_core_web_vitals", "TTI", False),
    "CLS": ("lab_core_web_vitals", "CLS", False),
    "speed_index_ms": ("lab_core_web_vitals", "speed_index", False),
}


def _parse_numeric(display_value: Any) -> float | None:
    """Extract a number from Lighthouse's display strings.

    Examples: '2.3 s' -> 2300 (ms), '120 ms' -> 120, '0.12' -> 0.12,
    '85' -> 85. Lighthouse mixes units; this picks the leading number and
    converts seconds to milliseconds for latency metrics. CLS is unitless.
    """
    if display_value is None:
        return None
    if isinstance(display_value, (int, float)):
        return float(display_value)
    text = str(display_value).strip().replace(",", "")
    # Pull the leading numeric token.
    num_chars: list[str] = []
    for ch in text:
        if ch.isdigit() or ch in ".-":
            num_chars.append(ch)
        else:
            break
    if not num_chars:
        return None
    try:
        value = float("".join(num_chars))
    except ValueError:
        return None
    # If the unit follows, convert seconds to ms so the budget is consistent
    # with the *_ms-suffixed budget keys.
    rest = text[len(num_chars):].strip().lower()
    if rest.startswith("s") and not rest.startswith("ms"):
        value *= 1000
    return value


TOOL = {
    "name": "lighthouse_budget",
    "description": (
        "Run PageSpeed Insights on a URL and verdict the results against a "
        "performance budget. Valid budget keys: "
        "higher-is-better Lighthouse scores on a 0-100 scale "
        "('performance', 'accessibility', 'best-practices', 'seo'); "
        "lower-is-better latency in milliseconds "
        "('LCP_ms', 'FCP_ms', 'TBT_ms', 'TTI_ms', 'speed_index_ms'); "
        "lower-is-better unitless shift score ('CLS'). "
        "Unknown keys are surfaced as a non-fatal finding with a "
        "'did you mean' hint; the metric is NOT silently ignored. "
        "Returns per-metric verdict and an overall pass/fail. "
        "Useful as a CI / pre-deploy gate."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Page URL to analyze. Required."},
            "budget": {
                "type": "object",
                "description": (
                    "Budget dict. Lighthouse scores on the 0-100 scale "
                    "(performance=80 means a score of 80, not 0.8); latency "
                    "metrics in milliseconds (LCP_ms=2500 means 2.5 s); CLS "
                    "is unitless (CLS=0.1 means a 0.1 layout-shift score). "
                    "Example: {performance: 80, LCP_ms: 2500, CLS: 0.1}."
                ),
                "additionalProperties": {"type": "number"},
            },
            "strategy": {"type": "string", "enum": ["mobile", "desktop"], "description": "Defaults to mobile."},
        },
        "required": ["url", "budget"],
        "additionalProperties": False,
    },
    "annotations": ANNOT_READ,
}


def _did_you_mean(unknown_key: str) -> str | None:
    """Best-effort suggestion for a mis-typed budget key. Picks the closest
    canonical key by character-set overlap so a user passing ``LCP`` instead
    of ``LCP_ms`` (the most common mistake the validator caught) gets a
    pointer back to the right name."""
    canonical = list(_BUDGET_KEYS.keys())
    key_lower = unknown_key.lower()
    # Exact case-folded match first (handles case-only differences).
    for c in canonical:
        if c.lower() == key_lower:
            return c
    # Substring match in either direction: covers LCP vs LCP_ms, perf vs
    # performance, and similar trims.
    for c in canonical:
        if key_lower in c.lower() or c.lower() in key_lower:
            return c
    return None


def lighthouse_budget(arguments, config, clients) -> dict[str, Any]:
    client, error = require_client(clients, "psi", _SERVICE, remediation=_REMEDIATION)
    if error:
        return error
    url = arguments.get("url")
    if not url:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "url is required.", docs_url=DOCS_BASE + "psi")
    budget = arguments.get("budget") or {}
    if not isinstance(budget, dict) or not budget:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "budget must be a non-empty object.")
    strategy = arguments.get("strategy", "mobile")

    try:
        raw = client.analyze(url, strategy=strategy)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    # Shape via the same helpers psi_analyze would use. We re-import here to
    # avoid a circular dependency at module load - lazy import is fine because
    # lighthouse_budget is a callable, not a module-level expression.
    from .psi_tools import _field_cwv, _lab_cwv, _lighthouse_scores

    lighthouse = raw.get("lighthouseResult", {})
    field = _field_cwv(raw.get("loadingExperience", {}))
    psi_envelope = {
        "lighthouse_scores": _lighthouse_scores(lighthouse),
        "lab_core_web_vitals": _lab_cwv(lighthouse),
        "field_core_web_vitals": field,
    }

    results: list[dict[str, Any]] = []
    unknown_keys: list[dict[str, Any]] = []
    overall_pass = True
    for key, target in budget.items():
        spec = _BUDGET_KEYS.get(key)
        if spec is None:
            suggestion = _did_you_mean(key)
            unknown_keys.append({
                "key": key,
                "did_you_mean": suggestion,
                "note": (
                    f"Budget key '{key}' is not recognized; the budget for "
                    "it was NOT applied. "
                    + (f"Did you mean '{suggestion}'?" if suggestion else
                       "Use one of the canonical names listed in the tool description.")
                ),
            })
            continue
        section, field_name, higher_is_better = spec
        raw_value = psi_envelope.get(section, {}).get(field_name)
        actual = _parse_numeric(raw_value)
        if actual is None:
            results.append({
                "metric": key,
                "budget": target,
                "actual": None,
                "verdict": "no_data",
            })
            continue
        if higher_is_better:
            verdict = "pass" if actual >= target else "fail"
        else:
            verdict = "pass" if actual <= target else "fail"
        if verdict == "fail":
            overall_pass = False
        results.append({
            "metric": key,
            "budget": target,
            "actual": actual,
            "direction": "higher_is_better" if higher_is_better else "lower_is_better",
            "verdict": verdict,
        })

    return ok({
        "url": url,
        "strategy": strategy,
        "overall_verdict": "pass" if overall_pass else "fail",
        "results": results,
        "unknown_budget_keys": unknown_keys,
        "psi_snapshot": psi_envelope,
    })


TOOLS = [TOOL]
HANDLERS = {"lighthouse_budget": lighthouse_budget}
