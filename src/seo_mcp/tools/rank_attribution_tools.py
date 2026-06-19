"""rank_change_attribution (roadmap Track D, Wave 2) -- the humility tool.

Credit (or refuse to credit) a specific on-site change with a clicks change for
a page, WITHOUT false causation. Naked before/after is forbidden; instead this
does a difference-in-differences against a matched control group of untouched
pages, reports a confidence interval and a three-state verdict, and prints a
confounders block. Pure GSC arithmetic -- no LLM, no paid API.

Design (design doc §6 D2):
  * Control group = untouched pages (same section by default) present in both
    windows; their average relative trend is the counterfactual. lift =
    observed_post - (treated_pre * peer_trend_ratio). This subtracts out core
    updates, seasonality and sitewide trends that hit both groups.
  * Verdict from whether the CI on lift crosses zero: likely_positive /
    likely_negative / inconclusive. Never "this change caused".
  * Clicks is the trusted metric; position/CTR were corrupted by the 2025 GSC
    impression bug + num=100 change, so when a window overlaps those, position
    is reported but flagged unreliable.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta
from math import sqrt
from typing import Any, Mapping
from urllib.parse import urlparse

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations, missing_site_error, require_client, resolve_site

_SERVICE = "gsc"
_REMEDIATION = (
    "Configure Google auth with Search Console access. See README > Auth."
)
_ROW_LIMIT = 25000
_MIN_CONTROL = 3            # need at least this many control pages for a CI
_MIN_TREATED_CLICKS = 5     # below this, the before signal is too thin
_MIN_CONTROL_PRE = 5        # control pages below this are too noisy for a ratio

# Well-sourced GSC data-regime breaks that corrupt position/CTR (clicks are
# unaffected). See design doc §0.4.
_IMPRESSION_BUG = (date(2025, 5, 13), date(2026, 4, 30))
_NUM100 = date(2025, 9, 11)


TOOL = {
    "name": "rank_change_attribution",
    "description": (
        "Estimate whether an on-site change moved a page's clicks, using "
        "difference-in-differences against a control group of untouched pages "
        "(never a naked before/after). Returns an estimated lift with a 95% "
        "confidence interval and a three-state verdict (likely_positive / "
        "likely_negative / inconclusive), plus a confounders block (algo-update "
        "proximity, GSC data-regime breaks, control quality, sample sizes). "
        "Clicks-based (position/CTR were corrupted by the 2025 GSC bugs). "
        "Observational, not proof of causation -- a server-side split test is "
        "the only true causal test."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The changed page URL (or use `urls`)."},
            "urls": {"type": "array", "items": {"type": "string"}, "description": "Multiple changed page URLs."},
            "change_date": {"type": "string", "description": "Date the change shipped, ISO YYYY-MM-DD. Required."},
            "query": {"type": "string", "description": "Optional: restrict to one query."},
            "site_url": {"type": "string", "description": "Defaults to the configured default site."},
            "pre_days": {"type": "integer", "minimum": 7, "maximum": 365, "description": "Pre-window length. Default 56 (>=2x post, per CausalImpact)."},
            "post_days": {"type": "integer", "minimum": 7, "maximum": 365, "description": "Post-window length. Default 28."},
            "gap_days": {"type": "integer", "minimum": 0, "maximum": 60, "description": "Washout gap after the change for recrawl/re-rank. Default 7."},
            "control_scope": {"type": "string", "enum": ["section", "site"], "description": "Control pool: same path section, or whole site. Default section."},
        },
        "required": ["change_date"],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def _norm(url: str) -> str:
    p = urlparse(str(url))
    if not p.scheme:
        return str(url)
    return f"{p.scheme}://{p.netloc.lower()}{p.path or '/'}"


def _section(url: str) -> str:
    p = urlparse(url)
    seg = [s for s in (p.path or "/").split("/") if s]
    first = seg[0] if seg else ""
    return f"{p.scheme}://{p.netloc.lower()}/{first}"


def _page_map(resp: dict[str, Any]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for r in resp.get("rows", []):
        keys = r.get("keys") or []
        if not keys:
            continue
        page = _norm(keys[0])
        out[page] = {
            "clicks": float(r.get("clicks", 0) or 0),
            "impressions": float(r.get("impressions", 0) or 0),
            "position": float(r.get("position", 0) or 0),
        }
    return out


def _overlaps(ws: date, we: date, a: date, b: date) -> bool:
    return ws <= b and a <= we


def rank_change_attribution(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    client, error = require_client(clients, "gsc", _SERVICE, remediation=_REMEDIATION)
    if error:
        return error

    treated_in = arguments.get("urls") or ([arguments["url"]] if arguments.get("url") else [])
    if not treated_in:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "Provide url or urls (the changed page[s]).", docs_url=DOCS_BASE + "gsc")
    change_raw = arguments.get("change_date")
    if not change_raw:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, "change_date (ISO YYYY-MM-DD) is required.")
    try:
        change_date = date.fromisoformat(str(change_raw))
    except ValueError:
        return err(ErrorCode.INVALID_INPUT, _SERVICE, f"change_date must be ISO YYYY-MM-DD, got {change_raw!r}.")

    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()

    pre_days = int(arguments.get("pre_days", 56))
    post_days = int(arguments.get("post_days", 28))
    gap_days = int(arguments.get("gap_days", 7))
    control_scope = str(arguments.get("control_scope", "section"))
    query = arguments.get("query")

    pre_start = (change_date - timedelta(days=pre_days)).isoformat()
    pre_end = change_date.isoformat()
    post_start = (change_date + timedelta(days=gap_days)).isoformat()
    post_end = (change_date + timedelta(days=gap_days + post_days)).isoformat()

    def _body(start: str, end: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "startDate": start, "endDate": end, "dimensions": ["page"],
            "rowLimit": _ROW_LIMIT, "type": "web",
            "dataState": getattr(config, "gsc_data_state", "final"),
        }
        if query:
            body["dimensionFilterGroups"] = [
                {"filters": [{"dimension": "query", "operator": "equals", "expression": str(query)}]}
            ]
        return body

    try:
        pre = _page_map(client.search_analytics(site, _body(pre_start, pre_end)))
        post = _page_map(client.search_analytics(site, _body(post_start, post_end)))
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    treated = [_norm(u) for u in treated_in]
    treated_set = set(treated)
    sections = {_section(u) for u in treated}

    treated_pre = sum(pre.get(t, {}).get("clicks", 0.0) for t in treated_set)
    treated_post = sum(post.get(t, {}).get("clicks", 0.0) for t in treated_set)

    # Control pool: pages in BOTH windows, not treated, (optionally) same section.
    control_pairs: list[tuple[float, float]] = []
    n_section = 0
    for page, prerec in pre.items():
        if page in treated_set or page not in post:
            continue
        if control_scope == "section":
            if _section(page) not in sections:
                continue
            n_section += 1
        pre_c = prerec["clicks"]
        post_c = post[page]["clicks"]
        if pre_c >= _MIN_CONTROL_PRE:
            control_pairs.append((pre_c, post_c))

    scope_used = control_scope
    note: str | None = None
    if control_scope == "section" and len(control_pairs) < _MIN_CONTROL:
        # Fall back to site-wide control.
        scope_used = "site"
        control_pairs = []
        for page, prerec in pre.items():
            if page in treated_set or page not in post:
                continue
            if prerec["clicks"] >= _MIN_CONTROL_PRE:
                control_pairs.append((prerec["clicks"], post[page]["clicks"]))
        note = "Section control pool too small; fell back to site-wide control."

    confounders = _confounders(change_date, pre_start, pre_end, post_start, post_end, len(control_pairs))

    # Verdict gates.
    if treated_pre < _MIN_TREATED_CLICKS:
        verdict, estimate = "insufficient_data", None
        note = (note + " " if note else "") + f"Treated pre-window clicks ({treated_pre:.0f}) below floor {_MIN_TREATED_CLICKS}."
    elif len(control_pairs) < _MIN_CONTROL:
        verdict, estimate = "insufficient_control", None
        note = (note + " " if note else "") + f"Only {len(control_pairs)} usable control pages (need {_MIN_CONTROL})."
    else:
        ratios = [post_c / pre_c for pre_c, post_c in control_pairs if pre_c > 0]
        mean_r = statistics.fmean(ratios)
        sd = statistics.stdev(ratios) if len(ratios) >= 2 else 0.0
        se = sd / sqrt(len(ratios)) if ratios else 0.0
        ratio_lo = max(0.0, mean_r - 1.96 * se)
        ratio_hi = mean_r + 1.96 * se
        counterfactual = treated_pre * mean_r
        lift = treated_post - counterfactual
        lift_lo = treated_post - treated_pre * ratio_hi
        lift_hi = treated_post - treated_pre * ratio_lo
        if lift_lo > 0:
            verdict = "likely_positive"
        elif lift_hi < 0:
            verdict = "likely_negative"
        else:
            verdict = "inconclusive"
        estimate = {
            "metric": "clicks",
            "treated_pre": round(treated_pre, 1),
            "treated_post": round(treated_post, 1),
            "peer_trend_ratio": round(mean_r, 4),
            "counterfactual_post": round(counterfactual, 1),
            "estimated_lift": round(lift, 1),
            "ci95": [round(lift_lo, 1), round(lift_hi, 1)],
        }

    treated_detail = [{
        "url": t,
        "pre_clicks": round(pre.get(t, {}).get("clicks", 0.0), 1),
        "post_clicks": round(post.get(t, {}).get("clicks", 0.0), 1),
        "pre_position": round(pre.get(t, {}).get("position", 0.0), 1),
        "post_position": round(post.get(t, {}).get("position", 0.0), 1),
    } for t in treated]

    return ok({
        "site_url": site,
        "change_date": change_date.isoformat(),
        "query": query,
        "windows": {"pre": [pre_start, pre_end], "post": [post_start, post_end], "gap_days": gap_days},
        "treated": treated_detail,
        "control": {"scope": scope_used, "pages": len(control_pairs)},
        "verdict": verdict,
        "estimate": estimate,
        "confounders": confounders,
        "note": note,
        "caveats": [
            "Observational difference-in-differences, NOT proof of causation. "
            "The only true causal test is a server-side page-level split test.",
            "Position/CTR are reported but unreliable when a window overlaps the "
            "2025 GSC impression bug (to ~2026-04) or the num=100 change "
            "(2025-09-11); the clicks-based estimate is the trustworthy one.",
            "Assumes the control pages would have trended like the treated page "
            "absent the change (parallel-trends). A page-type-specific algo "
            "update can break that -- see confounders.algo_update_note.",
        ],
    })


def _confounders(change_date, pre_start, pre_end, post_start, post_end, n_control) -> dict[str, Any]:
    ps, pe = date.fromisoformat(pre_start), date.fromisoformat(pre_end)
    qs, qe = date.fromisoformat(post_start), date.fromisoformat(post_end)
    bug = _overlaps(ps, pe, *_IMPRESSION_BUG) or _overlaps(qs, qe, *_IMPRESSION_BUG)
    num100 = (ps <= _NUM100 <= pe) or (qs <= _NUM100 <= qe) or (ps <= _NUM100 <= qe)
    breaks = []
    if bug:
        breaks.append("gsc_impression_bug_2025-05-13_to_~2026-04")
    if num100:
        breaks.append("num=100_deprecation_2025-09-11")
    return {
        "data_regime_breaks": breaks,
        "position_reliable": not breaks,
        "control_pages": n_control,
        "parallel_trends_assumption": (
            "Lift credited only above the peer trend; if a core/spam update hit "
            "this page-type specifically, the assumption breaks."
        ),
        "algo_update_note": (
            "Check Google's Search Status Dashboard for core/spam updates within "
            "~2 weeks of the change_date; DiD absorbs site-wide updates via the "
            "control but not page-type-specific ones."
        ),
    }


TOOLS = [TOOL]
HANDLERS = {"rank_change_attribution": rank_change_attribution}
