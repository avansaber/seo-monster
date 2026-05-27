"""Google Analytics 4 client over the Analytics Data API v1 (runReport).

``Ga4Client`` wraps a ``BetaAnalyticsDataClient``. It is constructed with that
analytics client injected, so tests pass a fake whose ``run_report(request)``
returns a canned response object and records the request. ``build_ga4_client``
is the real factory (credentials + BetaAnalyticsDataClient), with the heavy
imports done lazily.

The client builds the protobuf ``RunReportRequest`` from plain Python arguments
and normalizes the response into a JSON-friendly dict. Building the request from
the real types is offline (no network), so tests both inspect the built request
and exercise normalization.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..errors import ErrorCode
from .errors import ApiError, map_google_exception


def normalize_property_id(value: str | None) -> str | None:
    """Accept a bare numeric id ("123456789") or a prefixed one
    ("properties/123456789") and return the prefixed form. None stays None."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("properties/"):
        return text
    return f"properties/{text}"


def _coerce_number(value: Any) -> Any:
    """GA4 returns metric values as strings. Coerce to int/float, leave other
    strings untouched."""
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return value
    as_int = int(as_float)
    return as_int if as_float == as_int else as_float


def _types() -> Any:
    """Lazy import of the Analytics Data protobuf types."""
    from google.analytics.data_v1beta import types as t  # lazy

    return t


class Ga4Client:
    def __init__(self, analytics_client: Any, default_property: str | None = None) -> None:
        self._analytics = analytics_client
        self._default_property = default_property

    # --- request building -------------------------------------------------

    def _build_dimension_filter(self, spec: dict[str, Any] | None) -> Any | None:
        """Build a FilterExpression from the documented simple form:
        ``{"field": ..., "value": ..., "match_type": "EXACT"}`` for a string
        filter, or ``{"field": ..., "in_list": [...]}`` for an in-list filter."""
        if not spec:
            return None
        field = spec.get("field")
        if not field:
            return None
        t = _types()
        values = spec.get("in_list") or spec.get("values")
        if values:
            return t.FilterExpression(
                filter=t.Filter(
                    field_name=field,
                    in_list_filter=t.Filter.InListFilter(values=[str(v) for v in values]),
                )
            )
        match_name = str(spec.get("match_type") or "EXACT").upper()
        match_type = getattr(
            t.Filter.StringFilter.MatchType, match_name, t.Filter.StringFilter.MatchType.EXACT
        )
        return t.FilterExpression(
            filter=t.Filter(
                field_name=field,
                string_filter=t.Filter.StringFilter(value=str(spec.get("value", "")), match_type=match_type),
            )
        )

    def _build_order_bys(self, spec: dict[str, Any] | None) -> list[Any]:
        if not spec:
            return []
        t = _types()
        desc = bool(spec.get("desc", False))
        if spec.get("metric"):
            return [t.OrderBy(metric=t.OrderBy.MetricOrderBy(metric_name=spec["metric"]), desc=desc)]
        if spec.get("dimension"):
            return [t.OrderBy(dimension=t.OrderBy.DimensionOrderBy(dimension_name=spec["dimension"]), desc=desc)]
        return []

    def _build_request(
        self,
        property_id: str,
        *,
        dimensions: list[str],
        metrics: list[str],
        start_date: str,
        end_date: str,
        row_limit: int,
        dimension_filter: dict[str, Any] | None,
        order_by: dict[str, Any] | None,
    ) -> Any:
        t = _types()
        request = t.RunReportRequest(
            property=property_id,
            dimensions=[t.Dimension(name=d) for d in dimensions],
            metrics=[t.Metric(name=m) for m in metrics],
            date_ranges=[t.DateRange(start_date=start_date, end_date=end_date)],
            limit=row_limit,
        )
        dim_filter = self._build_dimension_filter(dimension_filter)
        if dim_filter is not None:
            request.dimension_filter = dim_filter
        order_bys = self._build_order_bys(order_by)
        if order_bys:
            request.order_bys = order_bys
        return request

    # --- normalization ----------------------------------------------------

    @staticmethod
    def _normalize(resp: Any) -> dict[str, Any]:
        dimension_headers = [h.name for h in resp.dimension_headers]
        metric_headers = []
        for h in resp.metric_headers:
            entry: dict[str, Any] = {"name": h.name}
            mtype = getattr(h, "type_", None)
            if mtype is not None:
                entry["type"] = getattr(mtype, "name", str(mtype))
            metric_headers.append(entry)
        rows = []
        for row in resp.rows:
            rows.append(
                {
                    "dimensions": [dv.value for dv in row.dimension_values],
                    "metrics": [_coerce_number(mv.value) for mv in row.metric_values],
                }
            )
        row_count = getattr(resp, "row_count", None)
        if row_count is None:
            row_count = len(rows)
        return {
            "dimension_headers": dimension_headers,
            "metric_headers": metric_headers,
            "row_count": row_count,
            "rows": rows,
        }

    # --- public -----------------------------------------------------------

    def run_report(
        self,
        property_id: str,
        *,
        dimensions: list[str],
        metrics: list[str],
        start_date: str,
        end_date: str,
        row_limit: int = 1000,
        dimension_filter: dict[str, Any] | None = None,
        order_by: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = self._build_request(
            property_id,
            dimensions=dimensions,
            metrics=metrics,
            start_date=start_date,
            end_date=end_date,
            row_limit=row_limit,
            dimension_filter=dimension_filter,
            order_by=order_by,
        )
        try:
            resp = self._analytics.run_report(request)
        except ApiError:
            raise
        except Exception as exc:  # network boundary
            raise map_google_exception(exc) from exc
        return self._normalize(resp)

    def probe(self) -> bool:
        """Cheap reachability check: a 1-row report against the default property.
        Requires a default property (system_status only probes GA4 when one is
        configured)."""
        if not self._default_property:
            raise ApiError(
                ErrorCode.INVALID_INPUT,
                "GA4 probe needs a configured property id.",
            )
        self.run_report(
            self._default_property,
            dimensions=[],
            metrics=["sessions"],
            start_date="yesterday",
            end_date="today",
            row_limit=1,
        )
        return True


def build_ga4_client(config: Config) -> Ga4Client:
    """Construct a real Ga4Client. Google libraries imported lazily."""
    from google.analytics.data_v1beta import BetaAnalyticsDataClient  # lazy

    from ..auth import required_scopes
    from .google_auth import build_google_credentials

    creds = build_google_credentials(config, required_scopes(config))
    analytics = BetaAnalyticsDataClient(credentials=creds)
    return Ga4Client(analytics, default_property=normalize_property_id(config.ga4_property_id))
