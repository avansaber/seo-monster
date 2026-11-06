"""GA4 filter validation regressions with injected offline analytics."""

import json

import pytest

from seo_mcp.clients.errors import ApiError
from seo_mcp.errors import ErrorCode
from seo_mcp.tools import ga4_tools

INVALID_FILTERS = [
    pytest.param({"notExpression": {"filter": {"fieldName": "country"}}}, id="native-not"),
    pytest.param({"andGroup": {"expressions": []}}, id="native-and"),
    pytest.param({"or_group": {"expressions": []}}, id="snake-or"),
    pytest.param({"filter": {"fieldName": "country"}}, id="native-filter"),
    pytest.param({}, id="empty-object"),
    pytest.param([], id="array"),
    pytest.param(False, id="false"),
    pytest.param(0, id="zero"),
    pytest.param("country", id="string"),
    pytest.param({"value": "fixture"}, id="missing-field"),
    pytest.param({"field": ""}, id="empty-field"),
    pytest.param({"field": " "}, id="blank-field"),
    pytest.param({"field": 123, "value": "fixture"}, id="numeric-field"),
    pytest.param({"field": None, "value": "fixture"}, id="null-field"),
    pytest.param({"field": "country"}, id="missing-string-value"),
    pytest.param({"field": "country", "value": None}, id="null-string-value"),
    pytest.param({"field": "country", "value": []}, id="array-string-value"),
    pytest.param({"field": "country", "value": {}}, id="object-string-value"),
    pytest.param({"field": "country", "value": "fixture", "match_type": "NOT_EQUALS"}, id="unknown-match"),
    pytest.param({"field": "country", "value": "fixture", "match_type": "MATCH_TYPE_UNSPECIFIED"}, id="unspecified-match"),
    pytest.param({"field": "country", "value": "fixture", "match_type": None}, id="null-match"),
    pytest.param({"field": "country", "value": "fixture", "match_type": ""}, id="empty-match"),
    pytest.param({"field": "country", "value": "fixture", "match_type": False}, id="boolean-match"),
    pytest.param({"field": "country", "value": "fixture", "match_type": 1}, id="numeric-match"),
    pytest.param({"field": "country", "value": "fixture", "notExpression": {}}, id="mixed-native-shape"),
    pytest.param({"field": "country", "value": "fixture", "case_sensitive": True}, id="unsupported-option"),
    pytest.param({"field": "country", "in_list": []}, id="empty-list"),
    pytest.param({"field": "country", "in_list": None}, id="null-list"),
    pytest.param({"field": "country", "in_list": "fixture"}, id="string-list"),
    pytest.param({"field": "country", "in_list": {}}, id="object-list"),
    pytest.param({"field": "country", "in_list": [None]}, id="null-list-item"),
    pytest.param({"field": "country", "in_list": [{}]}, id="object-list-item"),
    pytest.param({"field": "country", "in_list": ["fixture"], "value": "fixture"}, id="mixed-value-list"),
    pytest.param({"field": "country", "in_list": ["fixture"], "match_type": "EXACT"}, id="ignored-list-match"),
    pytest.param({"field": "country", "in_list": ["fixture"], "values": ["fixture"]}, id="two-list-aliases"),
    pytest.param({"field": "country", "in_list": [], "values": ["fixture"]}, id="empty-list-fallback"),
    pytest.param({"field": "country", "values": []}, id="empty-values-alias"),
]


def _report(spec, make_config, make_ga4_client, ga4_response):
    client = make_ga4_client(ga4_response(["country"], ["sessions"], []))
    result = ga4_tools.ga4_run_report(
        {"property_id": "123", "dimension_filter": spec},
        make_config(),
        {"ga4": client},
    )
    return result, client


@pytest.mark.parametrize("spec", INVALID_FILTERS)
def test_invalid_filter_returns_safe_error_before_provider(spec, make_config, make_ga4_client, ga4_response):
    result, client = _report(spec, make_config, make_ga4_client, ga4_response)
    assert client._analytics.requests == []
    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"]["code"] == "INVALID_INPUT"
    assert result["error"]["service"] == "ga4"
    assert result["error"]["remediation"]
    assert set(result) == {"ok", "data", "error"}


@pytest.mark.parametrize("spec", [None])
def test_absent_filter_remains_unfiltered(spec, make_config, make_ga4_client, ga4_response):
    result, client = _report(spec, make_config, make_ga4_client, ga4_response)
    assert result["ok"] is True
    assert not client._analytics.requests[0]._pb.HasField("dimension_filter")


@pytest.mark.parametrize(
    ("value", "match_name"),
    [
        ("fixture", "EXACT"), ("fixture", "exact"), ("fixture", "BEGINS_WITH"),
        ("fixture", "ENDS_WITH"), ("fixture", "CONTAINS"), ("fixture", "contains"),
        ("fixture.*", "FULL_REGEXP"), ("fixture", "PARTIAL_REGEXP"),
        ("", "EXACT"), (0, "EXACT"), (False, "EXACT"), (0.5, "EXACT"),
    ],
)
def test_supported_string_filter_preserves_request(value, match_name, make_config, make_ga4_client, ga4_response):
    from google.analytics.data_v1beta.types import Filter

    result, client = _report(
        {"field": "country", "value": value, "match_type": match_name},
        make_config, make_ga4_client, ga4_response,
    )
    assert result["ok"] is True
    filt = client._analytics.requests[0].dimension_filter.filter
    assert filt.field_name == "country"
    assert filt.string_filter.value == str(value)
    assert filt.string_filter.match_type == Filter.StringFilter.MatchType[match_name.upper()]


def test_omitted_match_type_still_defaults_to_exact(make_config, make_ga4_client, ga4_response):
    from google.analytics.data_v1beta.types import Filter

    result, client = _report({"field": "country", "value": "fixture"}, make_config, make_ga4_client, ga4_response)
    assert result["ok"] is True
    assert client._analytics.requests[0].dimension_filter.filter.string_filter.match_type == Filter.StringFilter.MatchType.EXACT


@pytest.mark.parametrize("key", ["in_list", "values"])
@pytest.mark.parametrize("values", [["fixture"], [0, False, ""], ("fixture", "other")])
def test_supported_list_filter_and_legacy_alias(key, values, make_config, make_ga4_client, ga4_response):
    result, client = _report({"field": "country", key: values}, make_config, make_ga4_client, ga4_response)
    assert result["ok"] is True
    filt = client._analytics.requests[0].dimension_filter.filter
    assert filt.field_name == "country"
    assert list(filt.in_list_filter.values) == [str(value) for value in values]


def test_direct_client_raises_existing_api_error_without_calling_provider(make_ga4_client, ga4_response):
    client = make_ga4_client(ga4_response(["country"], ["sessions"], []))
    with pytest.raises(ApiError) as caught:
        client.run_report(
            "properties/123", dimensions=["country"], metrics=["sessions"],
            start_date="yesterday", end_date="today", dimension_filter={"notExpression": {}},
        )
    assert caught.value.code == ErrorCode.INVALID_INPUT
    assert client._analytics.requests == []


def test_invalid_filter_error_does_not_echo_payload(make_config, make_ga4_client, ga4_response):
    sentinel = "filter-payload-sentinel"
    result, client = _report(
        {"field": "country", "value": sentinel, "match_type": sentinel},
        make_config, make_ga4_client, ga4_response,
    )
    assert client._analytics.requests == []
    assert sentinel not in json.dumps(result)
    assert result["error"]["code"] == "INVALID_INPUT"
