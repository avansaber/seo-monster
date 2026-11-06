import asyncio
import json
import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
from jsonschema import validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient

import seomonster_workbench
from seomonster_workbench.engine import Workbench
from seomonster_workbench.mcp_server import LocalHTTPGuard, create_server


TOKEN = "offline-fixture-token-32-characters-only"
ROOT = Path(__file__).resolve().parents[1]
WRITE_RECORD_TOOLS = {"wb_project_create", "wb_capture_ingest", "wb_plan_draft", "wb_experiment_start", "wb_experiment_compare"}
WRITE_RECORD_TOOLS |= {"wb_ownership_decision_draft", "wb_ownership_plan_draft"}
TOOLS = WRITE_RECORD_TOOLS | {
    "wb_project_get", "wb_board", "wb_records_list", "wb_recommendation_inspect", "wb_project_export",
    "wb_rule_replay_preview", "wb_ownership_board", "wb_ownership_inspect", "wb_ownership_export",
    "wb_deployment_observation_inspect",
}


@asynccontextmanager
async def connection(transport, database):
    args = ["-m", "seomonster_workbench.mcp_server", "--db", str(database), "--project", "test"]
    env = {"PYTHONPATH": str(Path(seomonster_workbench.__file__).resolve().parents[1]),
           "SM_WORKBENCH_HTTP_TOKEN": TOKEN}
    if transport == "stdio":
        async with stdio_client(StdioServerParameters(command=sys.executable, args=args, env=env)) as streams:
            async with ClientSession(*streams) as session:
                yield session
        return
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, *args, "--transport", "streamable-http", "--port", str(port)],
        env={**os.environ, **env}, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + TOKEN}, trust_env=False) as client:
            url = f"http://127.0.0.1:{port}/mcp"
            for _ in range(100):
                try:
                    async with client.stream("GET", url) as response:
                        assert response.status_code in {200, 405, 406}
                    break
                except httpx2.ConnectError:
                    if process.poll() is not None:
                        raise AssertionError(process.stderr.read().decode())
                    await asyncio.sleep(0.03)
            else:
                raise AssertionError("local HTTP server did not start")
            async with streamable_http_client(url, http_client=client) as (read, write):
                async with ClientSession(read, write) as session:
                    yield session
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()


async def workflow(session, fixtures):
    initialized = await session.initialize()
    assert "never truth-score" in initialized.instructions
    tools = {t.name: t for t in (await session.list_tools()).tools}
    assert set(tools) == TOOLS
    for name, tool in tools.items():
        assert tool.output_schema
        assert tool.annotations.read_only_hint == (name not in WRITE_RECORD_TOOLS)
        assert tool.annotations.open_world_hint is False

    async def call(name, args=None):
        result = await session.call_tool(name, args or {})
        assert not result.is_error, result
        payload = result.structured_content
        validate(payload, tools[name].output_schema)
        assert payload == json.loads(result.content[0].text)
        assert payload["ok"], payload
        return payload["data"]

    assert (await call("wb_project_get"))["projects"] == []
    context = json.loads((fixtures / "business_context.json").read_text())
    await call("wb_project_create", {"name": "Fixture project", "business_context": context,
                                     "sites": ["https://example.test", "https://docs.example.test"]})
    baseline = json.loads((fixtures / "gsc_baseline.json").read_text())
    ingested = await call("wb_capture_ingest", {"capture": baseline})
    replay = await call("wb_rule_replay_preview", {"snapshot_id": ingested["snapshot_id"]})
    assert replay["state"] == "identical" and replay["semantics"]["mutation"] == "not_available"
    board = await call("wb_board")
    assert len(board["items"]) == 3
    rec = next(i["recommendation"] for i in board["items"] if i["finding"]["kind"] == "striking_distance"
               and i["finding"]["affected_pages"] == ["https://example.test/evidence"])
    inspection = await call("wb_recommendation_inspect", {"recommendation_id": rec["id"]})
    assert inspection["source_snapshots"][0]["raw"] == baseline
    plan = await call("wb_plan_draft", {"recommendation_id": rec["id"], "rollback_notes": "Restore saved revision."})
    assert plan["external_execution_available"] is False
    experiment = await call("wb_experiment_start", {
        "recommendation_id": rec["id"], "baseline_evidence_id": inspection["exact_inputs"][0]["id"],
        "hypothesis": "Performance may improve", "window_start": "2026-08-03", "window_end": "2026-08-31",
    })
    outcome = json.loads((fixtures / "gsc_outcome.json").read_text())
    outcome_id = (await call("wb_capture_ingest", {"capture": outcome}))["snapshot_id"]
    evidence = (await call("wb_records_list", {"kind": "evidence"}))["items"]
    outcome_ev = next(e for e in evidence if e["snapshot_id"] == outcome_id)
    compared = await call("wb_experiment_compare", {"experiment_id": experiment["id"], "outcome_evidence_id": outcome_ev["id"]})
    assert compared["comparison"]["deltas"]["clicks"] == 19
    assert any("windows differ" in c for c in compared["comparison"]["caveats"])
    portfolio = json.loads((fixtures / "gsc_portfolio_site_b.json").read_text())
    await call("wb_capture_ingest", {"capture": portfolio})
    ownership = await call("wb_ownership_board")
    assert ownership["total"] == 1 and ownership["external_execution_available"] is False
    conflict = ownership["items"][0]["conflict"]
    ownership_inspection = await call("wb_ownership_inspect", {"conflict_id": conflict["id"]})
    assert len(ownership_inspection["source_snapshots"]) == 2
    roles = {item["url"]: f"intent {index}" for index, item in enumerate(conflict["observed_pages"])}
    pending = await call("wb_ownership_decision_draft", {
        "conflict_id": conflict["id"], "decision": "differentiate", "page_roles": roles,
        "note": "Pending operator review of distinct intents.",
    })
    assert pending["state"] == "pending" and pending["actor_assertion"] == "agent_draft"
    ownership_plan = await call("wb_ownership_plan_draft", {
        "decision_id": pending["id"], "rollback_notes": "Restore prior independently applied routing/content."
    })
    assert ownership_plan["state"] == "draft" and ownership_plan["external_execution_available"] is False
    ownership_export = await call("wb_ownership_export")
    assert ownership_export["format_version"] == "seomonster-workbench-ownership/v1"
    assert ownership_export["external_execution_available"] is False
    exported = await call("wb_project_export")
    assert exported["project"]["business_context"] == context
    assert not any(key.startswith("ownership_") for key in exported)
    for denied in ("wb_approve", "wb_execute", "wb_ownership_decide", "wb_ownership_assign",
                   "wb_ownership_execute", "wb_backup_export", "wb_restore_preview", "wb_restore_apply",
                   "wb_deployment_record", "gsc_submit_sitemap", "indexnow_submit", "cf_purge_cache"):
        result = await session.call_tool(denied, {})
        assert result.is_error
    return ingested, [i["recommendation"]["id"] for i in board["items"]]


def test_stdio_http_full_workflow_and_replay_parity(tmp_path, fixtures_dir):
    async def exercise():
        async with connection("stdio", tmp_path / "stdio.db") as session:
            stdio = await workflow(session, fixtures_dir)
        async with connection("http", tmp_path / "http.db") as session:
            http = await workflow(session, fixtures_dir)
        assert http == stdio
    asyncio.run(exercise())


def test_http_auth_origin_chunk_limit_and_project_scope(tmp_path, fixtures_dir):
    database = tmp_path / "scope.db"
    with Workbench(database) as wb:
        wb.create_project("other", "Other", {}, ["https://example.test"])
        wb.ingest("other", fixtures_dir / "gsc_baseline.json")
        other = wb.board("other")[0]["recommendation"]["id"]
        other_snapshot = wb.store.rows("snapshots", "other")[0]["id"]
    mcp = create_server(database, "test")
    with TestClient(LocalHTTPGuard(mcp.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=True), TOKEN), base_url="http://127.0.0.1") as client:
        assert client.post("/mcp", json={}).status_code == 401
        headers = {"Authorization": "Bearer " + TOKEN}
        assert client.post("/mcp", headers={**headers, "Origin": "https://evil.test"}, json={}).status_code == 403
        assert client.post("/mcp", headers=headers, content=b"x" * 2_000_001).status_code == 413

    async def check():
        result = await mcp.call_tool("wb_recommendation_inspect", {"recommendation_id": other})
        assert json.loads(result.content[0].text)["ok"] is False
        result = await mcp.call_tool("wb_rule_replay_preview", {"snapshot_id": other_snapshot})
        assert json.loads(result.content[0].text)["ok"] is False
    asyncio.run(check())


def test_invalid_capture_rolls_back_without_leaking_values(tmp_path):
    mcp = create_server(tmp_path / "db", "a")

    async def check():
        result = await mcp.call_tool("wb_project_create", {
            "name": "A", "business_context": {"api_key": "secret-fixture-value"}, "sites": ["https://example.test"]
        })
        serialized = result.content[0].text
        assert "secret-fixture-value" not in serialized
        assert json.loads(serialized)["ok"] is False
    asyncio.run(check())
    with Workbench(tmp_path / "db") as wb:
        assert not wb.store.has_project("a")
