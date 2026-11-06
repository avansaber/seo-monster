"""Fail-closed checks for generated analytical lineage, never website-claim truth."""

import json

from .util import digest


def validate_derivation(result, evidence, findings, recommendations):
    by_evidence = {item["id"]: item for item in evidence}
    by_finding = {item["id"]: item for item in findings}
    for item in evidence:
        provenance = json.loads(item["provenance_json"])
        expected = {
            "source_snapshot_id": result.snapshot_id,
            "captured_at": result.captured_at,
            "source_tool": result.tool,
            "arguments_hash": digest(result.arguments),
            "envelope_hash": digest(result.envelope),
        }
        if any(provenance.get(key) != value for key, value in expected.items()):
            raise ValueError("evidence provenance does not match its source")
        if not provenance.get("transform_version") or not isinstance(provenance.get("caveats"), list):
            raise ValueError("evidence transformation or caveats missing")
    for rec in recommendations:
        finding = by_finding.get(rec["finding_id"])
        if not finding:
            raise ValueError("recommendation finding missing")
        provenance = json.loads(rec["provenance_json"])
        ids = json.loads(finding["evidence_ids_json"])
        if not ids or any(item not in by_evidence for item in ids):
            raise ValueError("recommendation input evidence missing")
        expected = {
            "input_evidence_ids": ids, "source_snapshot_id": result.snapshot_id,
            "timestamp": result.captured_at, "rule_id": finding["rule_id"],
            "rule_version": finding["rule_version"], "calculation": finding["calculation"],
            "caveats": json.loads(finding["caveats_json"]),
        }
        if any(provenance.get(key) != value for key, value in expected.items()):
            raise ValueError("recommendation provenance is incomplete or inconsistent")
        if not finding["rule_id"] or not finding["rule_version"] or not finding["calculation"]:
            raise ValueError("recommendation calculation or rule missing")
