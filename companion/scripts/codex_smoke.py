"""Opt-in real Codex host smoke. Only synthetic local MCP data; no user config edits.

Requires the companion MCP extra and an authenticated Codex CLI. Model inference
uses the user's account. Raw host logs stay in a new private temporary directory.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from seomonster_workbench.engine import Workbench


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Authorize the opt-in authenticated host smoke")
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--installed", action="store_true", help="Use the installed wheel, not the adjacent source tree")
    args = parser.parse_args()
    if not args.run:
        parser.error("use --run to opt into authenticated Codex inference")
    if importlib.util.find_spec("mcp") is None:
        parser.error("the selected Python interpreter lacks the companion MCP extra; run with an environment that installed companion[mcp]")
    directory = Path(tempfile.mkdtemp(prefix="sm-codex-host-"))
    database = directory / "workbench.db"
    with Workbench(database) as wb:
        wb.create_project("host-smoke", "Synthetic host smoke", {"claim": "Approved synthetic claim"},
                          ["https://example.test", "https://docs.example.test"])
        baseline_snapshot = wb.ingest("host-smoke", args.fixtures / "gsc_baseline.json")["snapshot_id"]
        rec = wb.board("host-smoke")[0]["recommendation"]["id"]
        baseline = wb.inspect_recommendation(rec)["exact_inputs"][0]["id"]
        plan = wb.draft_plan(
            "host-smoke", rec, rollback_notes="Restore the prior synthetic revision.",
            created_at="2026-08-02T00:00:00Z",
        )
        observation = json.loads((args.fixtures / "deployment_observation.json").read_text())
        observation_id = wb.record_deployment_observation(
            "host-smoke", plan["id"], observation, "2026-08-04T00:00:00Z"
        )["id"]
        wb.ingest("host-smoke", args.fixtures / "gsc_outcome.json")
        outcome = wb.find_matching_outcome("host-smoke", baseline)["id"]
        wb.ingest("host-smoke", args.fixtures / "gsc_portfolio_site_b.json")
        conflict = wb.ownership_board("host-smoke")[0]["conflict"]["id"]
    config = 'mcp_servers.workbench = ' + '{ command = ' + json.dumps(sys.executable) + ', args = ' + json.dumps([
        "-m", "seomonster_workbench.mcp_server", "--db", str(database), "--project", "host-smoke"
    ])
    if not args.installed:
        config += ', env = { PYTHONPATH = ' + json.dumps(str(Path(__file__).resolve().parents[1] / "src")) + ' }'
    config += ' }'
    prompt = (
        "This is an offline synthetic MCP host acceptance test. Use only the workbench MCP tools, "
        "not shell, browsing, other servers or live providers. Read wb_project_get and wb_board. "
        f"Preview installed-rule replay for saved snapshot {baseline_snapshot}; do not migrate or rewrite it. "
        f"Inspect recommendation {rec}, draft its local action plan with dependencies [] and the exact rollback notes "
        "'Restore the prior synthetic revision.', then start an experiment for it "
        f"using baseline evidence {baseline}, hypothesis 'Synthetic improvement observation', "
        "observation window 2026-08-03 through 2026-08-31. "
        f"Compare that experiment using outcome evidence {outcome}. Export the project using the MCP tool. "
        f"Read the ownership board and inspect conflict {conflict}. Draft an accept proposal for the current exact "
        "participant set with note 'Pending operator review of the synthetic overlap'. Draft its local ownership "
        "plan with rollback notes 'Restore independently applied routing or content'. Export ownership using its "
        "separate MCP tool. Do not claim that observed visibility proves ownership or cannibalization. "
        f"Inspect deployment observation {observation_id} and report whether an approval timestamp precedes it. "
        "Do not approve or execute any plan. Finally report the observed clicks delta, whether causation "
        "is established, whether any external write occurred, and whether approved context stayed unchanged."
    )
    command = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
               "-s", "read-only", "-C", str(directory), "-c", config, "--json", prompt]
    result = subprocess.run(command, capture_output=True, text=True, timeout=240,
                            env={**os.environ, "NO_COLOR": "1"})
    (directory / "host-events.jsonl").write_text(result.stdout)
    (directory / "host-stderr.txt").write_text(result.stderr)
    required = ["wb_project_get", "wb_board", "wb_recommendation_inspect", "wb_plan_draft",
                "wb_rule_replay_preview", "wb_experiment_start", "wb_experiment_compare", "wb_project_export", "wb_ownership_board",
                "wb_ownership_inspect", "wb_ownership_decision_draft", "wb_ownership_plan_draft",
                "wb_ownership_export", "wb_deployment_observation_inspect"]
    # Events establish host tool selection; persisted state establishes successful effects.
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    calls = [event["item"] for event in events if event.get("item", {}).get("type") == "mcp_tool_call"
             and event["item"].get("status") == "completed"]
    selected = {name: any(call.get("tool") == name and call.get("server") == "workbench"
                         and call.get("result", {}).get("structured_content", {}).get("ok") is True
                         for call in calls) for name in required}
    off_scope = [call for call in calls if call.get("server") != "workbench"]
    with Workbench(database) as wb:
        records = [wb.store.decode(row) for row in wb.store.rows("experiments", "host-smoke")]
        plans = wb.store.rows("action_plans", "host-smoke")
        ownership_decisions = [wb.store.decode(row) for row in wb.store.rows("ownership_decisions", "host-smoke")]
        ownership_plans = [wb.store.decode(row) for row in wb.store.rows("ownership_action_plans", "host-smoke")]
        passed = (result.returncode == 0 and all(selected.values()) and not off_scope and len(records) == 1
                  and records[0]["comparison"] is not None
                  and records[0]["comparison"]["deltas"]["clicks"] == 19
                  and len(plans) == 1 and plans[0]["state"] == "draft"
                  and len(ownership_decisions) == 1 and ownership_decisions[0]["state"] == "pending"
                  and ownership_decisions[0]["actor_assertion"] == "agent_draft"
                  and len(ownership_plans) == 1 and ownership_plans[0]["state"] == "draft"
                  and ownership_plans[0]["external_execution_available"] is False)
    print(json.dumps({"status": "passed" if passed else "failed", "tool_selection": selected,
                      "exit_code": result.returncode, "external_executor": "absent",
                      "host_version": subprocess.check_output(["codex", "--version"], text=True).strip(),
                      "local_logs": str(directory)}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
