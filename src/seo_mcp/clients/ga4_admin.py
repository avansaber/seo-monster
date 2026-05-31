"""Google Analytics 4 Admin API client (analyticsadmin v1beta) over REST.

Unlike the GA4 Data API (gRPC ``BetaAnalyticsDataClient`` in ``clients/ga4.py``,
which needs ``google-analytics-data``), the GA4 Admin API is available over REST
through ``googleapiclient.discovery`` -- the same client ``gsc.py`` uses for
Search Console. So the setup audit reads a property's configuration with NO new
dependency, reusing ``google-api-python-client``.

``Ga4AdminClient`` wraps the discovery service (injected for tests). ``get_setup``
reads the SEO-relevant configuration of a property and returns it normalized; the
audit rules live in the tool layer (``tools/ga4_tools.py``), so they are testable
as a pure function over this dict.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from .errors import ApiError, map_google_exception


class Ga4AdminClient:
    """Thin wrapper over the analyticsadmin v1beta discovery service."""

    def __init__(self, service: Any) -> None:
        self._svc = service

    @staticmethod
    def _execute(request: Any) -> Any:
        try:
            return request.execute()
        except ApiError:
            raise
        except Exception as exc:  # network boundary: normalize everything
            raise map_google_exception(exc) from exc

    def get_setup(self, property_id: str) -> dict[str, Any]:
        """Read a property's SEO-relevant configuration and normalize it.

        ``property_id`` must be the ``properties/{id}`` form. Returns a plain
        dict (data streams, key events, data retention, custom dimensions) that
        the audit rules in the tool layer grade. Raises ``ApiError`` on failure.
        """
        props = self._svc.properties()
        streams = self._execute(props.dataStreams().list(parent=property_id)).get("dataStreams", [])
        key_events = self._execute(props.keyEvents().list(parent=property_id)).get("keyEvents", [])
        retention = self._execute(
            props.getDataRetentionSettings(name=f"{property_id}/dataRetentionSettings")
        )
        custom_dims = self._execute(
            props.customDimensions().list(parent=property_id)
        ).get("customDimensions", [])

        web_streams = [s for s in streams if s.get("type") == "WEB_DATA_STREAM"]
        return {
            "property_id": property_id,
            "stream_count": len(streams),
            "web_stream_count": len(web_streams),
            "web_streams": [
                {
                    "name": s.get("name"),
                    "display_name": s.get("displayName"),
                    "measurement_id": (s.get("webStreamData") or {}).get("measurementId"),
                }
                for s in web_streams
            ],
            "key_event_count": len(key_events),
            "key_events": [k.get("eventName") for k in key_events],
            "data_retention": retention.get("eventDataRetention"),
            "reset_on_new_activity": retention.get("resetUserDataOnNewActivity"),
            "custom_dimension_count": len(custom_dims),
            "custom_dimensions": [
                {
                    "parameter_name": d.get("parameterName"),
                    "display_name": d.get("displayName"),
                    "scope": d.get("scope"),
                }
                for d in custom_dims
            ],
        }


def build_ga4_admin_client(config: Config) -> Ga4AdminClient:
    """Construct a real Ga4AdminClient via the REST discovery service. Google
    libraries imported lazily (same pattern as ``build_gsc_client``). Reuses the
    standard scopes; ``analytics.readonly`` covers the Admin API read methods."""
    from googleapiclient.discovery import build  # lazy

    from ..auth import required_scopes
    from .google_auth import build_google_credentials

    creds = build_google_credentials(config, required_scopes(config))
    service = build("analyticsadmin", "v1beta", credentials=creds, cache_discovery=False)
    return Ga4AdminClient(service)
