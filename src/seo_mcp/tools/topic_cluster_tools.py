"""topic_cluster_map (roadmap Track C, Wave 2).

Map a content cluster from the property's OWN Search Console data and surface
the missing-subtopic gaps, using the four-quadrant intent model
(Defend / Optimize / Create / Monitor). The host then groups the Create-quadrant
queries into named subtopics -- the semantic step stays with the host LLM
(design doc §5 C3); this tool supplies the grounded data.

Quadrants (per query the cluster shows for):
  * defend   -- high demand, we own it (best position <= 3)
  * optimize -- high demand, we rank but can improve (3 < best position <= 20),
                or cannibalized (>= 2 cluster pages competing)
  * create   -- high demand, weak/no good page (best position > 20) => the
                missing-subtopic signal
  * monitor  -- low demand

Free, GSC-only. Honest bound: GSC anonymizes ~47% of queries, so "create" means
"no GSC-VISIBLE strong coverage", not "no coverage"; and there is no external
volume here -- ranking is by your own impressions.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping
from urllib.parse import urlparse

from ..clients.errors import ApiError
from ..errors import DOCS_BASE, ErrorCode, err, ok
from ._helpers import annotations, missing_site_error, require_client, resolve_site

_SERVICE = "gsc"
_REMEDIATION = "Configure Google auth with Search Console access. See README > Auth."
_ROW_LIMIT = 25000
_DEFEND_POS = 3.0
_OPTIMIZE_POS = 20.0


TOOL = {
    "name": "topic_cluster_map",
    "description": (
        "Map a content cluster from your own Search Console data and surface "
        "missing subtopics. Define the cluster by cluster_path (a URL path "
        "prefix) or pillar_url. For each query the cluster's pages show for, "
        "classifies it into defend / optimize / create / monitor by demand and "
        "best position, flags cannibalization, and lists the create-quadrant "
        "queries (high demand, weak position) as missing-subtopic candidates for "
        "you to group into named subtopics. Free, GSC-only. 'create' = no "
        "VISIBLE strong coverage (GSC hides ~47% of queries); no external volume."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "cluster_path": {"type": "string", "description": "URL path prefix defining the cluster, e.g. '/blog/widgets/'."},
            "pillar_url": {"type": "string", "description": "Pillar page URL; the cluster path is derived from its directory if cluster_path is omitted."},
            "site_url": {"type": "string", "description": "Defaults to the configured default site."},
            "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "Window. Default 90 (wider surfaces more)."},
            "impressions_min": {"type": "integer", "minimum": 1, "description": "Demand threshold: at/above this is 'high demand'. Default 50."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Max queries listed per quadrant. Default 50."},
        },
        "additionalProperties": False,
    },
    "annotations": annotations(read=True),
}


def _cluster_prefix(arguments: Mapping[str, Any]) -> str | None:
    cp = arguments.get("cluster_path")
    if cp:
        return str(cp)
    pillar = arguments.get("pillar_url")
    if pillar:
        path = urlparse(str(pillar)).path or "/"
        # directory of the pillar (strip the trailing file segment)
        if not path.endswith("/"):
            path = path.rsplit("/", 1)[0] + "/"
        return path
    return None


def _in_cluster(page: str, prefix: str) -> bool:
    return (urlparse(page).path or "/").startswith(prefix)


def topic_cluster_map(arguments: Mapping[str, Any], config: Any, clients: Mapping[str, Any]) -> dict[str, Any]:
    client, error = require_client(clients, "gsc", _SERVICE, remediation=_REMEDIATION)
    if error:
        return error

    prefix = _cluster_prefix(arguments)
    if not prefix:
        return err(
            ErrorCode.INVALID_INPUT,
            _SERVICE,
            "Provide cluster_path (a URL path prefix) or pillar_url to define the cluster.",
            docs_url=DOCS_BASE + "gsc",
        )

    site = resolve_site(arguments, config)
    if not site:
        return missing_site_error()

    days = int(arguments.get("days", 90))
    impressions_min = int(arguments.get("impressions_min", 50))
    limit = int(arguments.get("limit", 50))

    today = date.today()
    body = {
        "startDate": (today - timedelta(days=days)).isoformat(),
        "endDate": today.isoformat(),
        "dimensions": ["page", "query"],
        "rowLimit": _ROW_LIMIT,
        "type": "web",
        "dataState": getattr(config, "gsc_data_state", "final"),
    }
    try:
        resp = client.search_analytics(site, body)
    except ApiError as exc:
        return exc.to_envelope(_SERVICE)

    # Aggregate per query across the cluster's pages.
    agg: dict[str, dict[str, Any]] = {}
    cluster_pages: set[str] = set()
    for r in resp.get("rows", []):
        keys = r.get("keys") or []
        if len(keys) < 2:
            continue
        page, query = keys[0], keys[1]
        if not _in_cluster(page, prefix):
            continue
        cluster_pages.add(page)
        impr = float(r.get("impressions", 0) or 0)
        clicks = float(r.get("clicks", 0) or 0)
        position = float(r.get("position", 0) or 0)
        slot = agg.setdefault(query, {"impressions": 0.0, "clicks": 0.0, "best_position": None, "pages": set()})
        slot["impressions"] += impr
        slot["clicks"] += clicks
        slot["pages"].add(page)
        if position > 0 and (slot["best_position"] is None or position < slot["best_position"]):
            slot["best_position"] = position

    quadrants: dict[str, list[dict[str, Any]]] = {"defend": [], "optimize": [], "create": [], "monitor": []}
    cannibalization: list[dict[str, Any]] = []
    for query, s in agg.items():
        impr = s["impressions"]
        best = s["best_position"]
        n_pages = len(s["pages"])
        entry = {
            "query": query,
            "impressions": int(impr),
            "clicks": int(s["clicks"]),
            "best_position": round(best, 1) if best is not None else None,
            "ranking_pages": n_pages,
        }
        if impr < impressions_min:
            quadrant = "monitor"
        elif best is not None and best <= _DEFEND_POS:
            quadrant = "defend"
        elif best is not None and best <= _OPTIMIZE_POS:
            quadrant = "optimize"
        else:
            quadrant = "create"
        entry["quadrant"] = quadrant
        quadrants[quadrant].append(entry)
        if n_pages >= 2 and quadrant in ("defend", "optimize"):
            cannibalization.append(entry)

    for q in quadrants:
        quadrants[q].sort(key=lambda e: e["impressions"], reverse=True)

    missing = quadrants["create"][:limit]

    return ok({
        "site_url": site,
        "cluster_prefix": prefix,
        "days": days,
        "cluster_pages": sorted(cluster_pages),
        "query_count": sum(len(v) for v in quadrants.values()),
        "quadrant_counts": {k: len(v) for k, v in quadrants.items()},
        "quadrants": {k: v[:limit] for k, v in quadrants.items()},
        "missing_subtopics": missing,
        "cannibalization": cannibalization[:limit],
        "filters_applied": {"impressions_min": impressions_min, "defend_position": _DEFEND_POS, "optimize_position": _OPTIMIZE_POS},
        "caveats": [
            "'create' (missing-subtopic) queries are high-demand queries your "
            "cluster ranks poorly for -- group them into named subtopics and "
            "decide new-page vs improve-existing. This is the semantic step the "
            "host should do.",
            "GSC anonymizes ~47% of queries, so 'create' means no VISIBLE strong "
            "coverage, not none. There is no external volume here -- ranking is "
            "by your own impressions only.",
            "Cluster membership is a path-prefix heuristic; verify the cluster "
            "boundary matches your information architecture.",
        ],
    })


TOOLS = [TOOL]
HANDLERS = {"topic_cluster_map": topic_cluster_map}
