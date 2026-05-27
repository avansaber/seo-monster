"""Google Search Console client: searchconsole v1 + indexing v3.

``GscClient`` wraps the two discovery services. It is constructed with the
service objects injected, so tests build it with fake services and never call
``build()`` or touch the network. ``build_gsc_client`` is the real factory used
by the server; it constructs credentials and the discovery services.

Each upstream call goes through ``_execute`` which maps any failure to a
normalized ``ApiError`` (see ``clients/errors.py``). Client methods return the
raw API response dict; output shaping lives in the tools.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..errors import ErrorCode
from .errors import ApiError, map_google_exception


class GscClient:
    """Thin wrapper over the Search Console and Indexing discovery services."""

    def __init__(self, search_service: Any, indexing_service: Any | None = None) -> None:
        self._search = search_service
        self._indexing = indexing_service

    @staticmethod
    def _execute(request: Any) -> Any:
        try:
            return request.execute()
        except ApiError:
            raise
        except Exception as exc:  # network boundary: normalize everything
            raise map_google_exception(exc) from exc

    # --- Search Console ---------------------------------------------------

    def list_sites(self) -> dict[str, Any]:
        return self._execute(self._search.sites().list())

    def search_analytics(self, site_url: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._execute(
            self._search.searchanalytics().query(siteUrl=site_url, body=body)
        )

    def inspect_url(self, url: str, site_url: str) -> dict[str, Any]:
        body = {"inspectionUrl": url, "siteUrl": site_url}
        return self._execute(self._search.urlInspection().index().inspect(body=body))

    def list_sitemaps(self, site_url: str) -> dict[str, Any]:
        return self._execute(self._search.sitemaps().list(siteUrl=site_url))

    def submit_sitemap(self, site_url: str, feedpath: str) -> None:
        # submit returns an empty body on success.
        self._execute(self._search.sitemaps().submit(siteUrl=site_url, feedpath=feedpath))

    # --- Indexing API -----------------------------------------------------

    def request_indexing(self, url: str) -> dict[str, Any]:
        if self._indexing is None:
            raise ApiError(
                ErrorCode.SCOPE_INSUFFICIENT,
                "The Indexing API service is not available for these credentials.",
                remediation="Re-consent with the indexing scope. See README > Auth.",
            )
        body = {"url": url, "type": "URL_UPDATED"}
        return self._execute(self._indexing.urlNotifications().publish(body=body))

    # --- health -----------------------------------------------------------

    def probe(self) -> bool:
        """Cheap reachability check used by system_status: list properties."""
        self.list_sites()
        return True


def build_gsc_client(config: Config) -> GscClient:
    """Construct a real GscClient from credentials. Imports Google libraries
    lazily so module import stays cheap and SDK-free for unit tests."""
    from googleapiclient.discovery import build  # lazy

    from ..auth import required_scopes
    from .google_auth import build_google_credentials

    creds = build_google_credentials(config, required_scopes(config))
    search = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    indexing = build("indexing", "v3", credentials=creds, cache_discovery=False)
    return GscClient(search, indexing)
