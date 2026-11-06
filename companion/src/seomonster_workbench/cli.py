"""Command-line inspection and decision surface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .engine import Workbench
from .evaluation import run_harness


def _json_file(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sm-workbench", description="Evidence-first local companion for SEOmonster")
    parser.add_argument("--db", default=".seomonster-workbench.db", help="Local SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create or load a local SEO project")
    init.add_argument("--project", required=True)
    init.add_argument("--name", required=True)
    init.add_argument("--business-context", required=True)
    init.add_argument("--site", action="append", required=True)
    init.add_argument("--portfolio", default="Default portfolio")
    init.add_argument("--at")

    ingest = sub.add_parser("ingest", help="Ingest a saved SEOmonster result envelope")
    ingest.add_argument("--project", required=True)
    ingest.add_argument("--file", required=True)

    board = sub.add_parser("board", help="Show prioritized recommendations")
    board.add_argument("--project", required=True)

    inspect = sub.add_parser("inspect", help="Show exact evidence and calculation behind a recommendation")
    inspect.add_argument("--recommendation", required=True)

    replay = sub.add_parser("replay-preview", help="Compare installed rules with one stored snapshot without changes")
    replay.add_argument("--project", required=True)
    replay.add_argument("--snapshot", required=True)

    plan = sub.add_parser("plan", help="Draft a non-executing action plan")
    plan.add_argument("--project", required=True)
    plan.add_argument("--recommendation", required=True)
    plan.add_argument("--depends-on", action="append")
    plan.add_argument("--rollback-notes", default="Restore the prior content or routing configuration after verification.")
    plan.add_argument("--at")

    approve = sub.add_parser("approve", help="Record approval; does not execute the plan")
    approve.add_argument("--plan", required=True)
    approve.add_argument("--note", required=True)
    approve.add_argument("--at")

    experiment = sub.add_parser("experiment-start", help="Record an observational baseline")
    experiment.add_argument("--project", required=True)
    experiment.add_argument("--recommendation", required=True)
    experiment.add_argument("--baseline-evidence", required=True)
    experiment.add_argument("--hypothesis", required=True)
    experiment.add_argument("--window-start", required=True)
    experiment.add_argument("--window-end", required=True)
    experiment.add_argument("--at")

    compare = sub.add_parser("experiment-compare", help="Compare an outcome without claiming causation")
    compare.add_argument("--experiment", required=True)
    compare.add_argument("--outcome-evidence", required=True)

    deployment = sub.add_parser(
        "deployment-record", help="Import a local observation of a change performed outside the workbench"
    )
    deployment.add_argument("--project", required=True)
    deployment.add_argument("--plan", required=True)
    deployment.add_argument("--file", required=True)
    deployment.add_argument("--at", help="Local ledger recording timestamp")

    deployment_inspect = sub.add_parser("deployment-inspect", help="Inspect one frozen deployment observation")
    deployment_inspect.add_argument("--project", required=True)
    deployment_inspect.add_argument("--observation", required=True)

    change_export = sub.add_parser("change-export", help="Export the portable append-only change ledger")
    change_export.add_argument("--project", required=True)
    change_export.add_argument("--output", required=True)
    change_export.add_argument("--at")

    ownership_board = sub.add_parser("ownership-board", help="Show deterministic cross-site visibility conflicts")
    ownership_board.add_argument("--project", required=True)

    ownership_refresh = sub.add_parser("ownership-refresh", help="Rebuild current conflicts from saved evidence")
    ownership_refresh.add_argument("--project", required=True)

    ownership_inspect = sub.add_parser("ownership-inspect", help="Show exact evidence behind an ownership conflict")
    ownership_inspect.add_argument("--conflict", required=True)

    ownership_assign = sub.add_parser("ownership-assign", help="Record operator-declared query/topic page ownership")
    ownership_assign.add_argument("--project", required=True)
    ownership_assign.add_argument("--key-type", choices=["query", "topic"], required=True)
    ownership_assign.add_argument("--key", required=True)
    ownership_assign.add_argument("--owner-url", required=True)
    ownership_assign.add_argument("--role", choices=["primary", "supporting", "intent_variant", "accepted_overlap"], required=True)
    ownership_assign.add_argument("--intent", required=True)
    ownership_assign.add_argument("--rationale", required=True)
    ownership_assign.add_argument("--basis-evidence", action="append")
    ownership_assign.add_argument("--supersedes")
    ownership_assign.add_argument("--at")

    ownership_decide = sub.add_parser("ownership-decide", help="Record an operator ownership decision; executes nothing")
    ownership_decide.add_argument("--project", required=True)
    ownership_decide.add_argument("--conflict", required=True)
    ownership_decide.add_argument("--decision", choices=["accept", "differentiate", "consolidate", "route"], required=True)
    ownership_decide.add_argument("--primary-url")
    ownership_decide.add_argument("--page-roles", help="JSON object file mapping observed page URLs to roles or intents")
    ownership_decide.add_argument("--note", required=True)
    ownership_decide.add_argument("--supersedes")
    ownership_decide.add_argument("--at")

    ownership_plan = sub.add_parser("ownership-plan", help="Draft a non-executing plan from an ownership decision")
    ownership_plan.add_argument("--project", required=True)
    ownership_plan.add_argument("--decision", required=True)
    ownership_plan.add_argument("--rollback-notes", required=True)
    ownership_plan.add_argument("--depends-on", action="append")
    ownership_plan.add_argument("--at")

    ownership_export = sub.add_parser("ownership-export", help="Export the separate ownership/v1 portable bundle")
    ownership_export.add_argument("--project", required=True)
    ownership_export.add_argument("--output", required=True)
    ownership_export.add_argument("--at")

    export = sub.add_parser("export", help="Export a documented portable project bundle")
    export.add_argument("--project", required=True)
    export.add_argument("--output", required=True)
    export.add_argument("--at")

    backup = sub.add_parser("backup", help="Create a self-contained project and ownership backup")
    backup.add_argument("--project", required=True)
    backup.add_argument("--output", required=True)
    backup.add_argument("--at")

    restore_preview = sub.add_parser("restore-preview", help="Validate a backup and report local conflicts without changes")
    restore_preview.add_argument("--file", required=True)
    restore_preview.add_argument("--project", help="Require the preserved backup project identity")

    restore = sub.add_parser("restore", help="Transactionally create or exactly replay a validated local backup")
    restore.add_argument("--file", required=True)
    restore.add_argument("--project", help="Require the preserved backup project identity")

    evaluate = sub.add_parser("evaluate", help="Run the offline evaluation harness")
    evaluate.add_argument("--fixtures", required=True)

    demo = sub.add_parser("demo", help="Run the complete fixture-backed vertical slice")
    demo.add_argument("--fixtures", required=True)
    demo.add_argument("--output", required=True)
    demo.add_argument("--project", default="demo")
    return parser


def run(args: argparse.Namespace) -> Any:
    if args.command == "evaluate":
        return run_harness(args.fixtures)
    if args.command in {"replay-preview", "deployment-record", "deployment-inspect", "change-export"} \
            and not Path(args.db).is_file():
        raise ValueError(f"{args.command} requires an existing database file")

    with Workbench(args.db) as workbench:
        if args.command == "init":
            workbench.create_project(
                args.project,
                args.name,
                _json_file(args.business_context),
                args.site,
                args.portfolio,
                args.at,
            )
            return {"project": args.project, "state": "ready", "external_execution_available": False}
        if args.command == "ingest":
            return workbench.ingest(args.project, args.file)
        if args.command == "board":
            return workbench.board(args.project)
        if args.command == "inspect":
            return workbench.inspect_recommendation(args.recommendation)
        if args.command == "replay-preview":
            return workbench.preview_rule_replay(args.project, args.snapshot)
        if args.command == "plan":
            return workbench.draft_plan(
                args.project,
                args.recommendation,
                args.depends_on,
                args.rollback_notes,
                args.at,
            )
        if args.command == "approve":
            return workbench.approve_plan(args.plan, args.note, args.at)
        if args.command == "experiment-start":
            return workbench.start_experiment(
                args.project,
                args.recommendation,
                args.baseline_evidence,
                args.hypothesis,
                {"start": args.window_start, "end": args.window_end},
                args.at,
            )
        if args.command == "experiment-compare":
            return workbench.compare_experiment(args.experiment, args.outcome_evidence)
        if args.command == "deployment-record":
            from .change_ledger import load_observation

            observation = load_observation(args.file)
            return workbench.record_deployment_observation(
                args.project, args.plan, observation, args.at,
            )
        if args.command == "deployment-inspect":
            return workbench.inspect_deployment_observation(args.project, args.observation)
        if args.command == "change-export":
            return workbench.export_change_ledger(args.project, args.output, args.at)
        if args.command == "ownership-board":
            return workbench.ownership_board(args.project)
        if args.command == "ownership-refresh":
            return {"project": args.project, "active_conflicts": workbench.refresh_ownership_conflicts(args.project)}
        if args.command == "ownership-inspect":
            return workbench.inspect_ownership_conflict(args.conflict)
        if args.command == "ownership-assign":
            return workbench.record_ownership_assignment(
                args.project, args.key_type, args.key, args.owner_url, args.role, args.intent, args.rationale,
                args.basis_evidence, args.supersedes, args.at,
            )
        if args.command == "ownership-decide":
            page_roles = _json_file(args.page_roles) if args.page_roles else {}
            if not isinstance(page_roles, dict):
                raise ValueError("page roles file must contain a JSON object")
            return workbench.record_ownership_decision(
                args.project, args.conflict, args.decision, args.primary_url, page_roles,
                args.note, args.at, args.supersedes,
            )
        if args.command == "ownership-plan":
            return workbench.draft_ownership_plan(
                args.project, args.decision, args.rollback_notes, args.depends_on, args.at,
            )
        if args.command == "ownership-export":
            return workbench.export_ownership(args.project, args.output, args.at)
        if args.command == "export":
            return workbench.export_project(args.project, args.output, args.at)
        if args.command == "backup":
            return workbench.backup_project(args.project, args.output, args.at)
        if args.command in {"restore-preview", "restore"}:
            from .backup import load_backup

            payload = load_backup(args.file)
            if args.command == "restore-preview":
                return workbench.preview_restore(payload, args.project)
            return workbench.restore_backup(payload, args.project)
        if args.command == "demo":
            fixtures = Path(args.fixtures)
            workbench.create_project(
                args.project,
                "SEOmonster Workbench demo",
                _json_file(str(fixtures / "business_context.json")),
                ["https://example.test", "https://docs.example.test"],
                "Example portfolio",
                "2026-08-01T00:00:00Z",
            )
            baseline_ingest = workbench.ingest(args.project, fixtures / "gsc_baseline.json")
            board = workbench.board(args.project)
            target = next(
                item
                for item in board
                if item["finding"]["kind"] == "striking_distance"
                and "https://example.test/evidence" in item["finding"]["affected_pages"]
            )
            recommendation_id = target["recommendation"]["id"]
            inspection = workbench.inspect_recommendation(recommendation_id)
            baseline_evidence_id = inspection["exact_inputs"][0]["id"]
            plan = workbench.draft_plan(
                args.project,
                recommendation_id,
                rollback_notes="Restore the previous page revision if the separately executed change is rolled back.",
                created_at="2026-08-02T00:00:00Z",
            )
            experiment = workbench.start_experiment(
                args.project,
                recommendation_id,
                baseline_evidence_id,
                "The approved page improvement may be followed by stronger search performance.",
                {"start": "2026-08-03", "end": "2026-08-31"},
                "2026-08-02T00:00:00Z",
            )
            outcome_ingest = workbench.ingest(args.project, fixtures / "gsc_outcome.json")
            outcome = workbench.find_matching_outcome(args.project, baseline_evidence_id)
            if not outcome:
                raise ValueError("demo outcome did not match the baseline subject")
            comparison = workbench.compare_experiment(experiment["id"], outcome["id"])
            export = workbench.export_project(args.project, args.output, "2026-09-09T00:00:00Z")
            return {
                "project": args.project,
                "baseline_ingest": baseline_ingest,
                "opportunity_count": len(board),
                "inspected_recommendation": recommendation_id,
                "plan": plan,
                "experiment": comparison,
                "outcome_ingest": outcome_ingest,
                "export": export,
                "external_writes": 0,
            }
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    try:
        result = run(parser.parse_args(argv))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        _print({"ok": False, "error": {"code": "INVALID_INPUT", "message": str(exc)}})
        raise SystemExit(2) from exc
    _print({"ok": True, "data": result, "error": None})


if __name__ == "__main__":
    main(sys.argv[1:])
