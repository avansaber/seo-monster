"""Offline tests for lighthouse_budget. We mock the PsiClient.analyze
return value with a canned PSI payload."""

from __future__ import annotations

from typing import Any


from seo_mcp.clients.errors import ApiError
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import budget_tools


class FakePsiClient:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def analyze(self, url: str, *, strategy: str = "mobile", categories=None) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _payload(scores: dict[str, float], lab: dict[str, str]) -> dict[str, Any]:
    """Build a PSI payload that the lighthouse_budget tool can shape."""
    return {
        "lighthouseResult": {
            "categories": {k: {"score": v} for k, v in scores.items()},
            "audits": {
                "largest-contentful-paint": {"displayValue": lab.get("LCP", "2.0 s")},
                "cumulative-layout-shift": {"displayValue": lab.get("CLS", "0.05")},
                "total-blocking-time": {"displayValue": lab.get("TBT", "100 ms")},
                "speed-index": {"displayValue": lab.get("speed_index", "3.0 s")},
                "interactive": {"displayValue": lab.get("TTI", "4.0 s")},
                "first-contentful-paint": {"displayValue": lab.get("FCP", "1.5 s")},
            },
        },
        "loadingExperience": {},
    }


def _clients(payload) -> dict[str, Any]:
    return {"psi": FakePsiClient(payload)}


# --- parsing helpers ------------------------------------------------------


def test_parse_numeric_handles_units():
    assert budget_tools._parse_numeric("2.3 s") == 2300.0
    assert budget_tools._parse_numeric("450 ms") == 450.0
    assert budget_tools._parse_numeric("0.12") == 0.12
    assert budget_tools._parse_numeric("85") == 85.0
    assert budget_tools._parse_numeric(None) is None
    assert budget_tools._parse_numeric("not-a-number") is None


# --- happy path -----------------------------------------------------------


def test_budget_all_pass(make_config):
    payload = _payload(
        scores={"performance": 0.85, "accessibility": 0.95, "best-practices": 0.9, "seo": 1.0},
        lab={"LCP": "2.0 s", "CLS": "0.05", "TBT": "150 ms"},
    )
    budget = {"performance": 80, "LCP_ms": 2500, "CLS": 0.1, "TBT_ms": 200}
    result = budget_tools.lighthouse_budget(
        {"url": "https://example.com/", "budget": budget},
        make_config(),
        _clients(payload),
    )
    d = result["data"]
    assert d["overall_verdict"] == "pass"
    assert all(r["verdict"] == "pass" for r in d["results"])


def test_budget_one_metric_fails_flips_overall(make_config):
    payload = _payload(
        scores={"performance": 0.5},
        lab={"LCP": "3.5 s"},
    )
    budget = {"performance": 80, "LCP_ms": 2500}
    result = budget_tools.lighthouse_budget(
        {"url": "https://example.com/", "budget": budget},
        make_config(),
        _clients(payload),
    )
    d = result["data"]
    assert d["overall_verdict"] == "fail"
    perf = next(r for r in d["results"] if r["metric"] == "performance")
    assert perf["verdict"] == "fail"
    assert perf["actual"] == 50.0  # 0.5 * 100
    lcp = next(r for r in d["results"] if r["metric"] == "LCP_ms")
    assert lcp["verdict"] == "fail"
    assert lcp["actual"] == 3500.0  # 3.5s -> 3500ms


def test_budget_direction_is_correct(make_config):
    # CLS lower is better; 0.05 < 0.1 budget -> pass
    payload = _payload(scores={"performance": 0.9}, lab={"CLS": "0.05"})
    result = budget_tools.lighthouse_budget(
        {"url": "https://example.com/", "budget": {"CLS": 0.1}},
        make_config(),
        _clients(payload),
    )
    cls = result["data"]["results"][0]
    assert cls["verdict"] == "pass"
    assert cls["direction"] == "lower_is_better"


def test_budget_score_higher_is_better(make_config):
    payload = _payload(scores={"performance": 0.85}, lab={})
    result = budget_tools.lighthouse_budget(
        {"url": "https://example.com/", "budget": {"performance": 80}},
        make_config(),
        _clients(payload),
    )
    perf = result["data"]["results"][0]
    assert perf["direction"] == "higher_is_better"
    assert perf["verdict"] == "pass"


# --- unknown / missing data -----------------------------------------------


def test_budget_unknown_keys_carry_did_you_mean(make_config):
    """Validator round 5 §10b.U: silent rejection was a footgun. Unknown
    budget keys now surface as structured findings with a 'did you mean'
    hint when a near-match canonical name exists."""
    payload = _payload(scores={"performance": 0.9}, lab={})
    result = budget_tools.lighthouse_budget(
        {"url": "https://example.com/", "budget": {"performance": 80, "bogus_metric": 100}},
        make_config(),
        _clients(payload),
    )
    d = result["data"]
    assert len(d["unknown_budget_keys"]) == 1
    entry = d["unknown_budget_keys"][0]
    assert entry["key"] == "bogus_metric"
    # No canonical key matches "bogus_metric", so did_you_mean is None.
    assert entry["did_you_mean"] is None
    assert "not recognized" in entry["note"].lower()


def test_budget_lcp_typo_gets_did_you_mean_lcp_ms(make_config):
    """The validator's specific footgun: passing 'LCP' instead of 'LCP_ms'.
    Used to silently ignore the budget; now surfaces with a clear hint."""
    payload = _payload(scores={"performance": 0.9}, lab={"LCP": "2.0 s"})
    result = budget_tools.lighthouse_budget(
        {"url": "https://example.com/", "budget": {"performance": 80, "LCP": 4000}},
        make_config(),
        _clients(payload),
    )
    d = result["data"]
    assert len(d["unknown_budget_keys"]) == 1
    entry = d["unknown_budget_keys"][0]
    assert entry["key"] == "LCP"
    assert entry["did_you_mean"] == "LCP_ms"
    assert "lcp_ms" in entry["note"].lower()


def test_budget_no_data_verdict_when_metric_missing(make_config):
    payload = {
        "lighthouseResult": {
            "categories": {"performance": {"score": 0.9}},
            "audits": {},
        },
        "loadingExperience": {},
    }
    result = budget_tools.lighthouse_budget(
        {"url": "https://example.com/", "budget": {"LCP_ms": 2500}},
        make_config(),
        _clients(payload),
    )
    lcp = result["data"]["results"][0]
    assert lcp["verdict"] == "no_data"
    assert lcp["actual"] is None


# --- input validation -----------------------------------------------------


def test_budget_missing_budget_dict(make_config):
    result = budget_tools.lighthouse_budget(
        {"url": "https://example.com/"},
        make_config(),
        _clients({}),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.INVALID_INPUT


def test_budget_surfaces_upstream_error(make_config):
    result = budget_tools.lighthouse_budget(
        {"url": "https://example.com/", "budget": {"performance": 80}},
        make_config(),
        _clients(ApiError(ErrorCode.RATE_LIMITED, "PSI rate limit")),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == ErrorCode.RATE_LIMITED
