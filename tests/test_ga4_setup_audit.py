"""Tests for ga4_setup_audit: the pure ruleset (_audit_setup), the
Ga4AdminClient.get_setup normalization (against a fake discovery service), and
the handler paths."""

from __future__ import annotations

from seo_mcp.clients.ga4_admin import Ga4AdminClient
from seo_mcp.tools import ga4_tools


def _good_cfg(**over):
    cfg = {
        "property_id": "properties/123",
        "stream_count": 1,
        "web_stream_count": 1,
        "web_streams": [{"name": "x", "display_name": "Web", "measurement_id": "G-X"}],
        "key_event_count": 2,
        "key_events": ["purchase", "sign_up"],
        "data_retention": "FOURTEEN_MONTHS",
        "reset_on_new_activity": False,
        "custom_dimension_count": 1,
        "custom_dimensions": [{"parameter_name": "content_group", "display_name": "CG", "scope": "EVENT"}],
    }
    cfg.update(over)
    return cfg


# --- pure ruleset ---------------------------------------------------------


def test_audit_clean_setup_has_no_findings():
    assert ga4_tools._audit_setup(_good_cfg()) == []


def test_audit_no_web_stream_is_critical():
    f = ga4_tools._audit_setup(_good_cfg(web_stream_count=0))
    assert any(x["rule_id"] == "ga4.web_stream" and x["severity"] == "critical" for x in f)


def test_audit_no_key_events_is_high():
    f = ga4_tools._audit_setup(_good_cfg(key_event_count=0, key_events=[]))
    assert any(x["rule_id"] == "ga4.key_events" and x["severity"] == "high" for x in f)


def test_audit_two_month_retention_flagged_medium():
    f = ga4_tools._audit_setup(_good_cfg(data_retention="TWO_MONTHS"))
    assert any(x["rule_id"] == "ga4.data_retention" and x["severity"] == "medium" for x in f)


def test_audit_long_retention_not_flagged():
    assert all(x["rule_id"] != "ga4.data_retention" for x in ga4_tools._audit_setup(_good_cfg(data_retention="THIRTY_EIGHT_MONTHS")))


def test_audit_no_custom_dimensions_is_low():
    f = ga4_tools._audit_setup(_good_cfg(custom_dimension_count=0, custom_dimensions=[]))
    assert any(x["rule_id"] == "ga4.content_grouping" and x["severity"] == "low" for x in f)


def test_every_finding_grades_with_why_and_benign():
    f = ga4_tools._audit_setup(_good_cfg(web_stream_count=0, key_event_count=0, data_retention="TWO_MONTHS", custom_dimension_count=0))
    assert f
    for x in f:
        assert x["rule_id"] and x["severity"] and x["why"] and x["benign_exception"]


# --- client normalization (fake analyticsadmin discovery service) ---------


class _Req:
    def __init__(self, resp):
        self._r = resp

    def execute(self):
        return self._r


class _Coll:
    def __init__(self, resp):
        self._r = resp

    def list(self, parent=None):
        return _Req(self._r)


class _Props:
    def __init__(self, d):
        self._d = d

    def dataStreams(self):
        return _Coll(self._d["dataStreams"])

    def keyEvents(self):
        return _Coll(self._d["keyEvents"])

    def customDimensions(self):
        return _Coll(self._d["customDimensions"])

    def getDataRetentionSettings(self, name=None):
        return _Req(self._d["retention"])


class _Svc:
    def __init__(self, d):
        self._d = d

    def properties(self):
        return _Props(self._d)


def test_get_setup_normalizes_discovery_responses():
    data = {
        "dataStreams": {"dataStreams": [
            {"name": "p/1/dataStreams/1", "displayName": "Web", "type": "WEB_DATA_STREAM", "webStreamData": {"measurementId": "G-ABC"}},
            {"name": "p/1/dataStreams/2", "displayName": "iOS", "type": "IOS_APP_DATA_STREAM"},
        ]},
        "keyEvents": {"keyEvents": [{"eventName": "purchase"}, {"eventName": "sign_up"}]},
        "retention": {"eventDataRetention": "FOURTEEN_MONTHS", "resetUserDataOnNewActivity": False},
        "customDimensions": {"customDimensions": [{"parameterName": "content_group", "displayName": "CG", "scope": "EVENT"}]},
    }
    cfg = Ga4AdminClient(_Svc(data)).get_setup("properties/1")
    assert cfg["stream_count"] == 2
    assert cfg["web_stream_count"] == 1  # only the WEB_DATA_STREAM counts
    assert cfg["web_streams"][0]["measurement_id"] == "G-ABC"
    assert cfg["key_event_count"] == 2
    assert cfg["data_retention"] == "FOURTEEN_MONTHS"
    assert cfg["custom_dimension_count"] == 1


# --- handler --------------------------------------------------------------


class _FakeAdmin:
    def __init__(self, cfg):
        self._cfg = cfg

    def get_setup(self, prop):
        c = dict(self._cfg)
        c["property_id"] = prop
        return c


def test_handler_happy_path_verdict_issues(make_config):
    cfg = make_config(SEO_MCP_GA4_PROPERTY_ID="properties/123")
    clients = {"ga4_admin": _FakeAdmin(_good_cfg(key_event_count=0, key_events=[]))}
    res = ga4_tools.ga4_setup_audit({}, cfg, clients)
    assert res["ok"] is True
    d = res["data"]
    assert d["property_id"] == "properties/123"
    assert d["summary"]["verdict"] == "issues"  # missing key events = high
    assert d["deferred_checks"] == []  # v1alpha checks are now implemented


def test_handler_clean_verdict(make_config):
    cfg = make_config(SEO_MCP_GA4_PROPERTY_ID="properties/123")
    clients = {"ga4_admin": _FakeAdmin(_good_cfg())}
    res = ga4_tools.ga4_setup_audit({}, cfg, clients)
    assert res["data"]["summary"]["verdict"] == "clean"
    assert res["data"]["findings"] == []


def test_handler_auth_missing(make_config):
    cfg = make_config(SEO_MCP_GA4_PROPERTY_ID="properties/123")
    res = ga4_tools.ga4_setup_audit({}, cfg, {})  # no ga4_admin client
    assert res["ok"] is False
    assert res["error"]["code"] == "AUTH_MISSING"


def test_handler_missing_property(make_config):
    cfg = make_config()  # no default property
    clients = {"ga4_admin": _FakeAdmin(_good_cfg())}
    res = ga4_tools.ga4_setup_audit({}, cfg, clients)
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_INPUT"


# --- v1alpha checks (enhanced measurement / site search / Google Signals) ---


def test_audit_enhanced_measurement_off_is_medium():
    f = ga4_tools._audit_setup(_good_cfg(enhanced_measurement=False))
    assert any(x["rule_id"] == "ga4.enhanced_measurement" and x["severity"] == "medium" for x in f)


def test_audit_site_search_off_when_em_on_is_medium():
    f = ga4_tools._audit_setup(_good_cfg(enhanced_measurement=True, site_search_enabled=False))
    assert any(x["rule_id"] == "ga4.site_search" and x["severity"] == "medium" for x in f)


def test_audit_site_search_skipped_when_em_off():
    # EM-off is already flagged; don't also nag about site search.
    f = ga4_tools._audit_setup(_good_cfg(enhanced_measurement=False, site_search_enabled=False))
    assert all(x["rule_id"] != "ga4.site_search" for x in f)


def test_audit_google_signals_reported_as_info():
    f = ga4_tools._audit_setup(_good_cfg(google_signals_state="GOOGLE_SIGNALS_ENABLED"))
    assert any(x["rule_id"] == "ga4.google_signals" and x["severity"] == "info" for x in f)


def test_audit_v1alpha_fields_none_means_skip():
    # None (v1alpha unavailable) -> those checks are skipped, not failed.
    ids = {x["rule_id"] for x in ga4_tools._audit_setup(_good_cfg())}
    assert not ({"ga4.enhanced_measurement", "ga4.site_search", "ga4.google_signals"} & ids)


class _AlphaProps:
    def __init__(self, em, gs):
        self._em, self._gs = em, gs

    def dataStreams(self):
        em = self._em

        class _DS:
            def getEnhancedMeasurementSettings(self, name=None):
                return _Req(em)

        return _DS()

    def getGoogleSignalsSettings(self, name=None):
        return _Req(self._gs)


class _AlphaSvc:
    def __init__(self, em, gs):
        self._em, self._gs = em, gs

    def properties(self):
        return _AlphaProps(self._em, self._gs)


def test_get_setup_reads_v1alpha_when_available():
    data = {
        "dataStreams": {"dataStreams": [
            {"name": "properties/1/dataStreams/1", "displayName": "Web", "type": "WEB_DATA_STREAM", "webStreamData": {"measurementId": "G-ABC"}},
        ]},
        "keyEvents": {"keyEvents": [{"eventName": "purchase"}]},
        "retention": {"eventDataRetention": "FOURTEEN_MONTHS"},
        "customDimensions": {"customDimensions": []},
    }
    alpha = _AlphaSvc(em={"streamEnabled": True, "siteSearchEnabled": False}, gs={"state": "GOOGLE_SIGNALS_ENABLED"})
    cfg = Ga4AdminClient(_Svc(data), alpha).get_setup("properties/1")
    assert cfg["enhanced_measurement"] is True
    assert cfg["site_search_enabled"] is False
    assert cfg["google_signals_state"] == "GOOGLE_SIGNALS_ENABLED"


def test_get_setup_without_alpha_leaves_v1alpha_none():
    data = {
        "dataStreams": {"dataStreams": [{"name": "p/1/dataStreams/1", "type": "WEB_DATA_STREAM"}]},
        "keyEvents": {"keyEvents": []},
        "retention": {"eventDataRetention": "FOURTEEN_MONTHS"},
        "customDimensions": {"customDimensions": []},
    }
    cfg = Ga4AdminClient(_Svc(data)).get_setup("properties/1")  # no alpha service
    assert cfg["enhanced_measurement"] is None
    assert cfg["site_search_enabled"] is None
    assert cfg["google_signals_state"] is None
