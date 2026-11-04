"""Local project-site attribution checks; never fetch URLs or assess business claims."""

from urllib.parse import urlsplit, urlunsplit


def host(url):
    if not isinstance(url, str):
        raise ValueError("site scope URL must be a string")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("site scope requires an HTTP(S) URL without credentials")
    return parsed.hostname.encode("idna").decode().lower()


def canonical_page_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("page must be an HTTPS URL without credentials or fragment")
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def resolve_project_site(page_url, sites):
    """Attribute a page to the most specific configured origin/path scope."""
    page = urlsplit(canonical_page_url(page_url))
    matches = []
    for site in sites:
        value = site["canonical_url"] if isinstance(site, dict) else site
        configured = urlsplit(canonical_page_url(value))
        base_path = configured.path.rstrip("/")
        same_origin = (page.scheme, page.netloc) == (configured.scheme, configured.netloc)
        in_path = not base_path or page.path == base_path or page.path.startswith(base_path + "/")
        if same_origin and in_path:
            identity = site.get("id", value) if isinstance(site, dict) else value
            matches.append((len(base_path), str(identity), site))
    if not matches:
        raise ValueError("page is outside configured project site scope")
    matches.sort(key=lambda item: (-item[0], item[1]))
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        raise ValueError("page matches ambiguous project site scopes")
    return matches[0][2]


def validate_capture_scope(result, sites):
    allowed = {host(site) for site in sites}
    data = result.envelope["data"]
    data = data if isinstance(data, dict) else {}
    properties = [v for v in (result.arguments.get("site_url"), data.get("site_url")) if v is not None]
    for value in properties:
        if not isinstance(value, str):
            raise ValueError("property scope must be a string")
        is_domain = value.startswith("sc-domain:")
        domain = value.removeprefix("sc-domain:").encode("idna").decode().lower() if is_domain else host(value)
        in_scope = any(h == domain or h.endswith("." + domain) for h in allowed) if is_domain else domain in allowed
        if not in_scope:
            raise ValueError("capture property is outside configured project sites")
    if len(properties) == 2 and properties[0].rstrip("/").lower() != properties[1].rstrip("/").lower():
        raise ValueError("argument and result property scopes disagree")

    def page(url):
        resolve_project_site(url, sites)
        for value in properties:
            if value.startswith("sc-domain:"):
                domain = value.removeprefix("sc-domain:").encode("idna").decode().lower()
                if host(url) != domain and not host(url).endswith("." + domain):
                    raise ValueError("capture page is outside property scope")
            else:
                prefix = urlsplit(value)
                observed = urlsplit(url)
                if (observed.scheme, observed.netloc) != (prefix.scheme, prefix.netloc) or not (
                    observed.path == prefix.path.rstrip("/") or observed.path.startswith(prefix.path.rstrip("/") + "/")
                ):
                    raise ValueError("capture page is outside property prefix scope")

    if result.tool in {"gsc_search_analytics", "gsc_compare_periods"}:
        dimensions = data.get("dimensions", result.arguments.get("dimensions", []))
        rows = data.get("rows", [])
        if rows and not properties and "page" not in dimensions:
            raise ValueError("GSC capture has no attributable site scope")
        if isinstance(dimensions, list) and "page" in dimensions and isinstance(rows, list):
            index = dimensions.index("page")
            for row in rows:
                keys = row.get("keys", []) if isinstance(row, dict) else []
                if not isinstance(keys, list) or len(keys) <= index:
                    raise ValueError("GSC page dimension is missing")
                page(keys[index])
    if result.tool in {"redirect_chain_audit", "gsc_batch_inspect_urls"}:
        for url in result.arguments.get("urls", []):
            page(url)
        for row in data.get("results", data.get("urls", [])):
            if not isinstance(row, dict):
                raise ValueError("migration rows must be objects")
            if url := row.get("url", row.get("requested_url")):
                page(url)
        # Outbound redirect destinations are retained as evidence, not as owned pages.


def validate_affected_pages(findings, sites):
    import json
    for finding in findings:
        for url in json.loads(finding["affected_pages_json"]):
            resolve_project_site(url, sites)
