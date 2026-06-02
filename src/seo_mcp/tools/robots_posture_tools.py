"""robots_ai_posture (1). A deterministic, offline advisor for the
Content-Signals standard in robots.txt (the `search` / `ai-input` / `ai-train`
levers). It takes a business goal, recommends a posture, lays out the
trade-off alternatives, and emits a ready-to-apply artifact: the
`Content-Signal:` directive line plus a full suggested robots.txt.

No network, no writes. The output is meant to be handed to a future
`cf_managed_robots configure` step or pasted straight into robots.txt.

Honesty discipline: Content-Signal is only honored by crawlers that adopt
the standard. Googlebot ignores it and it is NOT a ranking factor. The
reliable levers remain a clean Allow + Sitemap and NOT Disallow-ing the AI
bots. Every response carries that caveat in a top-level `caveat` field, so it
is impossible to miss. We never oversell the signal.
"""

from __future__ import annotations

from typing import Any

from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations


_SERVICE = "technical"

# The mandatory, non-negotiable honesty caveat. Returned on every success.
_CAVEAT = (
    "Content-Signal is only honored by crawlers that have adopted the "
    "standard. Googlebot ignores it, and it is NOT a ranking factor. It does "
    "not block anything on its own - a crawler that does not implement it will "
    "do whatever it likes. The reliable levers remain a clean 'Allow: /' plus "
    "a 'Sitemap:' line, and NOT 'Disallow'-ing the AI bots you want to reach "
    "you. Treat Content-Signal as a stated preference, not an enforcement "
    "mechanism."
)

# Plain-language explanation of the three levers, by business consequence.
_LEVERS = {
    "search": (
        "Classic search indexing plus snippets - the listing in a results "
        "page that earns the click."
    ),
    "ai-input": (
        "Answer-time citation in ChatGPT, Perplexity, AI Overviews, and "
        "Claude WITH a link back to you - referral traffic and authority."
    ),
    "ai-train": (
        "Bulk model training with NO attribution and no traffic back - your "
        "content becomes weights in a model that never sends a visitor."
    ),
}

_ALLOWED_GOALS = ("content_authority", "maximize_visibility", "protect_ip")
_DEFAULT_GOAL = "content_authority"

# goal -> (search, ai-input, ai-train) recommendation.
_POSTURES: dict[str, dict[str, str]] = {
    "content_authority": {"search": "yes", "ai-input": "yes", "ai-train": "no"},
    "maximize_visibility": {"search": "yes", "ai-input": "yes", "ai-train": "no"},
    "protect_ip": {"search": "yes", "ai-input": "no", "ai-train": "no"},
}

# One-line rationale per goal, in plain business terms.
_RATIONALE = {
    "content_authority": (
        "Stay searchable and let AI assistants cite you with a link back, so "
        "you build authority and earn referral traffic, while declining bulk "
        "training that gives nothing back."
    ),
    "maximize_visibility": (
        "Be present everywhere a buyer might find you - search results and "
        "AI answers with a link back - while still declining attribution-free "
        "bulk training."
    ),
    "protect_ip": (
        "Stay indexed for search, but tell AI systems not to use your content "
        "either as answer-time input or as training data."
    ),
}

# The trade-off menu. Always returned, regardless of the chosen posture, so
# the caller sees an options menu rather than a single verdict.
_ALTERNATIVES = [
    {
        "posture": "all-yes (search=yes, ai-input=yes, ai-train=yes)",
        "tradeoff": (
            "Maximum reach across search and AI, but you donate your content "
            "to model training with no attribution and no traffic back."
        ),
    },
    {
        "posture": "content_authority (search=yes, ai-input=yes, ai-train=no)",
        "tradeoff": (
            "Searchable and citable with a link back, while declining "
            "attribution-free training. Balanced default for most publishers."
        ),
    },
    {
        "posture": "protect_ip (search=yes, ai-input=no, ai-train=no)",
        "tradeoff": (
            "Stay in search, but ask AI systems to neither cite nor train on "
            "you. You trade away AI-channel referral traffic for tighter "
            "control."
        ),
    },
    {
        "posture": "clean / no Content-Signal",
        "tradeoff": (
            "Silent permit - you state no preference, so adopting crawlers do "
            "as they like. Simplest file, zero signal, no leverage."
        ),
    },
]


def _content_signal_line(posture: dict[str, str]) -> str:
    """Render the Content-Signal directive line from a posture dict, in the
    canonical search / ai-input / ai-train order."""
    parts = [f"{lever}={posture[lever]}" for lever in ("search", "ai-input", "ai-train")]
    return "Content-Signal: " + ", ".join(parts)


def _build_robots_txt(posture: dict[str, str], sitemap_url: str | None) -> str:
    """Assemble a full suggested robots.txt: a 'User-agent: *' group with
    'Allow: /', the Content-Signal directive, and a Sitemap line (real URL if
    provided, else a placeholder comment)."""
    lines = [
        "User-agent: *",
        "Allow: /",
        _content_signal_line(posture),
        "",
    ]
    if sitemap_url:
        lines.append(f"Sitemap: {sitemap_url}")
    else:
        lines.append("# Sitemap: https://www.example.com/sitemap.xml  (replace with your sitemap URL)")
    return "\n".join(lines) + "\n"


TOOL = {
    "name": "robots_ai_posture",
    "description": (
        "Recommend a Content-Signals posture (search / ai-input / ai-train) "
        "from a business goal and emit a ready-to-apply artifact: the "
        "Content-Signal directive line plus a full suggested robots.txt. "
        "Read-only, offline, deterministic - no network, no writes. Always "
        "returns the trade-off alternatives and the mandatory caveat that "
        "Content-Signal is honored only by adopting crawlers, is ignored by "
        "Googlebot, and is not a ranking factor."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "enum": list(_ALLOWED_GOALS),
                "description": (
                    "Business goal driving the recommendation. One of "
                    "content_authority, maximize_visibility, protect_ip. "
                    "Defaults to content_authority when omitted."
                ),
            },
            "sitemap_url": {
                "type": "string",
                "description": "Optional sitemap URL embedded into the generated robots.txt artifact.",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def robots_ai_posture(arguments, config, clients) -> dict[str, Any]:
    goal = arguments.get("goal")
    if goal is None:
        goal = _DEFAULT_GOAL
    elif goal not in _ALLOWED_GOALS:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            (
                f"Unknown goal {goal!r}. Allowed values: "
                + ", ".join(_ALLOWED_GOALS)
                + "."
            ),
            docs_url=DOCS_BASE + "technical",
        )

    sitemap_url = arguments.get("sitemap_url")
    if sitemap_url is not None:
        sitemap_url = str(sitemap_url).strip() or None

    posture = _POSTURES[goal]
    content_signal = _content_signal_line(posture)
    robots_txt = _build_robots_txt(posture, sitemap_url)

    return ok({
        "goal": goal,
        "default_applied": arguments.get("goal") is None,
        "levers": _LEVERS,
        "recommendation": {
            "posture": dict(posture),
            "rationale": _RATIONALE[goal],
        },
        "alternatives": _ALTERNATIVES,
        "artifact": {
            "content_signal_line": content_signal,
            "robots_txt": robots_txt,
            "sitemap_url": sitemap_url,
        },
        "caveat": _CAVEAT,
    })


TOOLS = [TOOL]
HANDLERS = {"robots_ai_posture": robots_ai_posture}
