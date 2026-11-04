import json

import pytest

from seomonster_workbench.adapter import AdapterError, load_saved_result


def test_saved_result_preserves_public_envelope(fixtures_dir):
    adapted = load_saved_result(fixtures_dir / "gsc_baseline.json", "project")
    assert set(adapted.envelope) >= {"ok", "data", "error"}
    assert adapted.source_locator == "gsc_baseline.json"
    assert "/Users/" not in adapted.source_locator


def test_invalid_envelope_fails_closed(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"adapter_version": "seomonster-result/v1", "result": {"ok": True}}))
    with pytest.raises(AdapterError, match="missing fields"):
        load_saved_result(invalid, "project")


def test_malicious_fetched_text_is_marked_inert(fixtures_dir):
    adapted = load_saved_result(fixtures_dir / "serp_malicious.json", "project")
    assert any("inert data" in caveat for caveat in adapted.caveats)
    assert "<script>" in json.dumps(adapted.raw)

