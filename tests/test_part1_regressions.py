"""Synthetic offline cases for multilingual extraction and scoped edge policy."""

import unicodedata

import pytest

from seo_mcp.clients.cloudflare import CfClient
from seo_mcp.clients.http import HttpResponse
from seo_mcp.tools import (
    ai_readiness_tools,
    cf_tools,
    content_brief_tools,
    onpage_serp_gap_tools,
)
from seo_mcp.tools._html import content_entities, content_terms, parse_content


@pytest.mark.parametrize("text", [
    "Русский видимый контент", "Ελληνικό ορατό περιεχόμενο", "עברית תוכן גלוי",
    "العربية محتوى ظاهر", "हिन्दी सामग्री दिखाई", "café résumé naïve",
])
def test_unicode_extraction_reaches_all_lexical_consumers(text):
    parser = parse_content(f"<body><p>{text}</p></body>")
    assert parser.word_count == 3
    assert len(content_terms(text)) >= 2
    assert content_entities(text)
    assert parser.top_token_ratio()[0] is not None


def test_combining_marks_have_canonical_token_identity():
    text = "café हिन्दी"
    assert content_terms(text) == content_terms(unicodedata.normalize("NFD", text))
    assert parse_content(f"<p>{text}</p>").word_count == 2


class SavedHttp:
    def __init__(self, body):
        self.body = body

    def fetch(self, url, **kwargs):
        return HttpResponse(200, {"content-type": "text/html"}, self.body.encode(), url)


def test_non_latin_readiness_is_not_render_blind(make_config):
    words = "Русский видимый контент статья данные пример страница описание раздел заголовок " \
            "информация источник ответ вопрос текст документ система проект анализ обзор руководство " \
            "результат метод проверка"
    body = f"<body><h2>Раздел</h2><p>{words * 12}</p>" + "<script>ignored</script>" * 3 + "</body>"
    result = ai_readiness_tools.ai_citation_readiness(
        {"url": "https://example.com/ru"}, make_config(), {"http": SavedHttp(body)},
    )["data"]
    assert result["word_count"] >= 200
    assert result["rendered_blind"] is False
    assert result["stuffing"]["flagged"] is False


@pytest.mark.parametrize("text", ["中文可见内容" * 240, "ภาษาไทยเนื้อหา" * 240], ids=["chinese", "thai"])
def test_unsegmented_text_is_visible_but_word_based_scores_are_unavailable(make_config, text):
    body = f"<body><p>{text}</p>" + "<script>ignored</script>" * 3 + "</body>"
    result = ai_readiness_tools.ai_citation_readiness(
        {"url": "https://example.com/page"}, make_config(), {"http": SavedHttp(body)},
    )["data"]
    assert result["rendered_blind"] is False
    assert result["text_measurement"]["word_count_reliable"] is False
    assert result["stuffing"]["flagged"] is None
    assert "statistics" not in result["readiness"]["components"]
    assert "no_keyword_stuffing" not in result["readiness"]["components"]


def test_unsegmented_competitor_counts_do_not_create_word_targets(make_config):
    http = SavedHttp("<body><p>" + "中文可见内容" * 220 + "</p></body>")
    brief = content_brief_tools.content_brief_data(
        {"target_query": "example", "competitor_urls": ["https://example.org/page"]},
        make_config(), {"http": http},
    )["data"]
    assert brief["evidence"]["target_word_count_floor"] is None
    gap = onpage_serp_gap_tools.onpage_serp_gap(
        {"target_url": "https://example.com/page", "competitor_urls": ["https://example.org/page"]},
        make_config(), {"http": http},
    )["data"]
    assert gap["gaps"]["word_count"]["competitor_median"] is None
    assert not any("Content is thin" in action for action in gap["actions"])


def settings_client(rules, *, error=None, default="full"):
    client = CfClient(token="synthetic-test-token")
    calls = []

    def raw(method, path, body=None):
        calls.append((method, path, body))
        if "/rulesets/phases/" in path:
            if error:
                raise error
            return {"success": True, "result": {"id": "fixture-ruleset", "rules": rules}}
        if path.endswith("/settings"):
            return {"success": True, "result": [{"id": "ssl", "value": default}]}
        return {"success": True, "result": [{"id": "fixture-zone", "name": "example.com"}]}

    client._raw_request = raw
    return client, calls


def rule(expression, ssl, *, enabled=True):
    return {"id": "fixture-rule", "expression": expression, "action": "set_config",
            "action_parameters": {"ssl": ssl}, "enabled": enabled}


def audit(make_config, rules, **kwargs):
    client, calls = settings_client(rules, **kwargs)
    result = cf_tools.cf_settings_audit({}, make_config(CF_ZONE="example.com"), {"cf": client})
    assert result["ok"] is True
    assert all(method == "GET" for method, _, _ in calls)
    return result["data"]


def test_mixed_origin_rules_never_propose_zone_wide_strict(make_config):
    result = audit(make_config, [rule('http.host in {"example.com" "www.example.com"}', "strict")])
    coverage = result["ssl_coverage"]
    assert coverage["hosts"]["example.com"]["ssl"] == "strict"
    assert coverage["hosts"]["www.example.com"]["ssl"] == "strict"
    assert coverage["other_hostnames"] == "not_enumerated"
    assert coverage["origin_readiness"] == "unknown"
    assert all("fix" not in f for f in result["findings"] if f["rule_id"] == "cf.ssl_mode")


def test_mixed_origin_weaker_hostname_gets_its_own_review_finding(make_config):
    result = audit(make_config, [
        rule('http.host in {"example.com" "www.example.com"}', "strict"),
        rule('http.host eq "legacy.example.com"', "flexible"),
    ])
    matches = [finding for finding in result["findings"]
               if finding["rule_id"] == "cf.ssl_mode"
               and finding["observed"] == "hostname=legacy.example.com, ssl=flexible"]
    assert len(matches) == 1
    assert matches[0]["scope"] == "hostname_configuration_rule"
    assert matches[0]["affected_hostnames"] == ["legacy.example.com"]
    assert "fix" not in matches[0]
    assert result["ssl_coverage"]["origin_readiness"] == "unknown"


def test_hostname_rules_apply_in_provider_order_and_ignore_disabled(make_config):
    result = audit(make_config, [rule('http.host eq "example.com"', "strict"),
                                rule('http.host eq "example.com"', "full"),
                                rule('http.host eq "example.com"', "off", enabled=False)])
    assert result["ssl_coverage"]["hosts"]["example.com"]["ssl"] == "full"
    assert result["ssl_coverage"]["hosts"]["example.com"]["rule_positions"] == [0, 1]


@pytest.mark.parametrize("expression", [
    'http.host eq "example.com" and http.request.uri.path eq "/admin"',
    'ends_with(http.host, ".example.com")', '__import__("os").system("false")',
])
def test_unsupported_expression_is_unknown_not_a_false_match(make_config, expression):
    result = audit(make_config, [rule('http.host eq "example.com"', "strict"), rule(expression, "full")])
    assert result["ssl_coverage"]["hosts"]["example.com"]["ssl"] is None
    assert result["ssl_coverage"]["status"] == "partial"


def test_missing_rules_permission_preserves_zone_data_with_uncertainty(make_config):
    from seo_mcp.clients.errors import ApiError
    from seo_mcp.errors import ErrorCode

    result = audit(make_config, [], error=ApiError(ErrorCode.SCOPE_INSUFFICIENT, "fixture permission denied"))
    assert result["settings_snapshot"]["ssl"] == "full"
    assert result["ssl_coverage"]["status"] == "unavailable"
    assert result["ssl_coverage"]["error_code"] == "SCOPE_INSUFFICIENT"
    assert all("fix" not in f for f in result["findings"] if f["rule_id"] == "cf.ssl_mode")


def test_cloudflare_9109_is_a_rules_access_gap_not_a_bad_token(make_config):
    from seo_mcp.errors import ErrorCode

    error = CfClient._error_from_http(
        403, '{"errors":[{"code":9109,"message":"Unauthorized to access requested resource"}]}',
    )
    assert error.code == ErrorCode.SCOPE_INSUFFICIENT
    result = audit(make_config, [], error=error)
    assert result["ssl_coverage"]["status"] == "unavailable"
    assert result["ssl_coverage"]["error_code"] == "SCOPE_INSUFFICIENT"
    assert "permission" in result["ssl_coverage"]["error"]["audit_guidance"].lower()


def test_strict_zone_with_weaker_host_override_is_not_clean(make_config):
    result = audit(make_config, [rule('http.host eq "legacy.example.com"', "full")], default="strict")
    assert result["ssl_coverage"]["hosts"]["legacy.example.com"]["ssl"] == "full"
    assert any(f["rule_id"] == "cf.ssl_mode" for f in result["findings"])


def test_empty_rules_still_cannot_attest_origin_readiness(make_config):
    result = audit(make_config, [])
    assert result["ssl_coverage"]["status"] == "observed"
    assert result["ssl_coverage"]["origin_readiness"] == "unknown"
    assert all("fix" not in f for f in result["findings"] if f["rule_id"] == "cf.ssl_mode")


def test_simple_or_hosts_and_later_known_override(make_config):
    rules = [rule('http.host eq "example.com" and http.request.uri.path eq "/partial"', "full"),
             rule('(http.host eq "example.com") or (http.host eq "www.example.com")', "strict")]
    result = audit(make_config, rules)
    assert result["ssl_coverage"]["hosts"]["example.com"]["ssl"] == "strict"
    assert result["ssl_coverage"]["status"] == "partial"
    assert result["ssl_coverage"]["source"]["rules"] == rules
    assert result["ssl_coverage"]["source"]["rule_version"] == "cf.ssl.coverage/v1"


def test_missing_configuration_phase_is_observed_absence(make_config):
    from seo_mcp.clients.errors import ApiError
    from seo_mcp.errors import ErrorCode

    result = audit(make_config, [], error=ApiError(ErrorCode.NOT_FOUND, "fixture phase absent"))
    assert result["ssl_coverage"]["status"] == "observed"
    assert result["ssl_coverage"]["source"]["rules"] == []


@pytest.mark.parametrize("malformed", [[], None, {"rules": None}, {"rules": "not a list"}])
def test_malformed_configuration_response_is_not_empty_coverage(make_config, malformed):
    client, _ = settings_client([])
    raw = client._raw_request

    def replacement(method, path, body=None):
        if "/rulesets/phases/" in path:
            return {"success": True, "result": malformed}
        return raw(method, path, body)

    client._raw_request = replacement
    result = cf_tools.cf_settings_audit({}, make_config(CF_ZONE="example.com"), {"cf": client})["data"]
    assert result["ssl_coverage"]["status"] == "unavailable"
    assert result["ssl_coverage"]["error_code"] == "UPSTREAM_ERROR"


def test_disabled_and_non_ssl_rules_do_not_create_false_unknown(make_config):
    rules = [rule('unknown()', "off", enabled=False),
             {"action": "set_config", "expression": "unknown()", "action_parameters": {"rocket_loader": True}},
             rule("true", "strict")]
    result = audit(make_config, rules)
    assert result["ssl_coverage"]["status"] == "observed"
    assert result["ssl_coverage"]["hosts"]["example.com"]["ssl"] == "strict"


def test_short_unsegmented_page_does_not_receive_a_confident_render_score(make_config):
    body = "<body><p>中文内容</p>" + "<script>ignored</script>" * 3 + "</body>"
    result = ai_readiness_tools.ai_citation_readiness(
        {"url": "https://example.com/page"}, make_config(), {"http": SavedHttp(body)},
    )["data"]
    assert result["render_blind_assessment"] == "unavailable"
    assert "extractable" not in result["readiness"]["components"]
    assert result["readiness"]["applied_weight"] < 1


def test_english_contractions_and_script_exclusion_remain_compatible():
    parser = parse_content("<p>Blue widgets don't hide well-known content.</p><script>secret words</script>")
    assert parser.word_count == 6
    assert "don't" in content_terms(parser.text)
    assert "secret" not in content_terms(parser.text)


def test_mixed_default_still_surfaces_weaker_explicit_hostname(make_config):
    rules = [rule('http.host in {"example.com" "www.example.com"}', "strict"),
             rule('http.host eq "legacy.example.com"', "flexible")]
    result = audit(make_config, rules)
    scoped = [f for f in result["findings"] if f.get("affected_hostnames") == ["legacy.example.com"]]
    assert len(scoped) == 1
    assert scoped[0]["scope"] == "hostname_configuration_rule"
    assert scoped[0]["severity"] == "high"
    assert "fix" not in scoped[0]


def test_cloudflare_resource_denial_retains_remediation_and_scope_error(make_config):
    error = CfClient._error_from_http(403, '{"errors":[{"code":9109,"message":"fixture resource denial"}]}')
    result = audit(make_config, [], error=error)
    assert result["ssl_coverage"]["error_code"] == "SCOPE_INSUFFICIENT"
    assert result["ssl_coverage"]["error"]["remediation"] == error.remediation
    assert "configuration-rule read permission" in result["ssl_coverage"]["error"]["audit_guidance"]
