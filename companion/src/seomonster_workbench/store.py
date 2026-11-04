"""SQLite persistence, migrations, and portable record access."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .adapter import AdaptedResult
from .util import canonical_json, json_value, stable_id


SCHEMA_VERSION = 3

MIGRATIONS = [
    """
    CREATE TABLE portfolios (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE sites (
        id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
        canonical_url TEXT NOT NULL, name TEXT NOT NULL,
        UNIQUE(portfolio_id, canonical_url)
    );
    CREATE TABLE projects (
        id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
        name TEXT NOT NULL, created_at TEXT NOT NULL,
        business_context_json TEXT NOT NULL,
        claim_policy TEXT NOT NULL
    );
    CREATE TABLE project_sites (
        project_id TEXT NOT NULL REFERENCES projects(id),
        site_id TEXT NOT NULL REFERENCES sites(id),
        PRIMARY KEY(project_id, site_id)
    );
    CREATE TABLE snapshots (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        tool TEXT NOT NULL, captured_at TEXT NOT NULL, provider_json TEXT NOT NULL,
        arguments_json TEXT NOT NULL, envelope_json TEXT NOT NULL, raw_json TEXT NOT NULL,
        content_hash TEXT NOT NULL, source_locator TEXT NOT NULL,
        adapter_version TEXT NOT NULL, freshness TEXT NOT NULL,
        caveats_json TEXT NOT NULL
    );
    CREATE TABLE evidence (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        snapshot_id TEXT NOT NULL REFERENCES snapshots(id), evidence_type TEXT NOT NULL,
        subject TEXT NOT NULL, observed_at TEXT NOT NULL, value_json TEXT,
        availability TEXT NOT NULL, provenance_json TEXT NOT NULL
    );
    CREATE TABLE findings (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        snapshot_id TEXT NOT NULL REFERENCES snapshots(id), kind TEXT NOT NULL,
        title TEXT NOT NULL, affected_pages_json TEXT NOT NULL,
        impact TEXT NOT NULL, confidence TEXT NOT NULL, effort TEXT NOT NULL,
        caveats_json TEXT NOT NULL, evidence_ids_json TEXT NOT NULL,
        rule_id TEXT NOT NULL, rule_version TEXT NOT NULL,
        calculation TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE
    );
    CREATE TABLE recommendations (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        finding_id TEXT NOT NULL REFERENCES findings(id), title TEXT NOT NULL,
        rationale TEXT NOT NULL, state TEXT NOT NULL,
        provenance_json TEXT NOT NULL
    );
    CREATE TABLE decisions (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        recommendation_id TEXT NOT NULL REFERENCES recommendations(id),
        state TEXT NOT NULL, decided_at TEXT NOT NULL, note TEXT
    );
    CREATE TABLE action_plans (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        recommendation_id TEXT NOT NULL REFERENCES recommendations(id),
        state TEXT NOT NULL, created_at TEXT NOT NULL,
        dependencies_json TEXT NOT NULL, steps_json TEXT NOT NULL,
        rollback_notes TEXT NOT NULL, external_execution_available INTEGER NOT NULL
    );
    CREATE TABLE experiments (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        recommendation_id TEXT REFERENCES recommendations(id),
        hypothesis TEXT NOT NULL, state TEXT NOT NULL,
        baseline_evidence_id TEXT NOT NULL REFERENCES evidence(id),
        outcome_evidence_id TEXT REFERENCES evidence(id),
        observation_window_json TEXT NOT NULL, comparison_json TEXT,
        causal_claim TEXT NOT NULL, created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE ownership_assignments (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        key_type TEXT NOT NULL CHECK(key_type IN ('query','topic')),
        key_text TEXT NOT NULL, normalized_key TEXT NOT NULL,
        owner_site_id TEXT NOT NULL REFERENCES sites(id), owner_url TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('primary','supporting','intent_variant','accepted_overlap')),
        intent TEXT NOT NULL, rationale TEXT NOT NULL, source TEXT NOT NULL,
        basis_evidence_ids_json TEXT NOT NULL, provenance_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        supersedes_id TEXT REFERENCES ownership_assignments(id)
    );
    CREATE INDEX ownership_assignments_key
        ON ownership_assignments(project_id,key_type,normalized_key,created_at,id);
    CREATE TABLE ownership_conflicts (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        key_type TEXT NOT NULL, key_text TEXT NOT NULL, normalized_key TEXT NOT NULL,
        evidence_ids_json TEXT NOT NULL, observed_pages_json TEXT NOT NULL,
        observed_sites_json TEXT NOT NULL, measurement_scope_json TEXT NOT NULL,
        impact TEXT NOT NULL, confidence TEXT NOT NULL, caveats_json TEXT NOT NULL,
        rule_id TEXT NOT NULL, rule_version TEXT NOT NULL, calculation TEXT NOT NULL,
        detected_at TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1))
    );
    CREATE INDEX ownership_conflicts_active
        ON ownership_conflicts(project_id,active,impact,normalized_key,id);
    CREATE TABLE ownership_decisions (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        conflict_id TEXT NOT NULL REFERENCES ownership_conflicts(id),
        decision TEXT NOT NULL CHECK(decision IN ('accept','differentiate','consolidate','route')),
        state TEXT NOT NULL CHECK(state IN ('pending','recorded')),
        participant_pages_json TEXT NOT NULL, primary_url TEXT, page_roles_json TEXT NOT NULL,
        note TEXT NOT NULL, decided_at TEXT NOT NULL, actor_assertion TEXT NOT NULL,
        input_evidence_ids_json TEXT NOT NULL, provenance_json TEXT NOT NULL,
        supersedes_id TEXT REFERENCES ownership_decisions(id),
        external_execution_available INTEGER NOT NULL CHECK(external_execution_available=0)
    );
    CREATE INDEX ownership_decisions_conflict
        ON ownership_decisions(project_id,conflict_id,decided_at,id);
    CREATE TABLE ownership_action_plans (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        conflict_id TEXT NOT NULL REFERENCES ownership_conflicts(id),
        decision_id TEXT NOT NULL REFERENCES ownership_decisions(id),
        state TEXT NOT NULL CHECK(state='draft'), created_at TEXT NOT NULL,
        dependencies_json TEXT NOT NULL, steps_json TEXT NOT NULL,
        rollback_notes TEXT NOT NULL,
        external_execution_available INTEGER NOT NULL CHECK(external_execution_available=0)
    );
    """,
    """
    CREATE TABLE deployment_observations (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        plan_id TEXT NOT NULL REFERENCES action_plans(id),
        observed_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
        environment TEXT NOT NULL,
        verification_state TEXT NOT NULL CHECK(verification_state IN ('reported','artifact_observed','postcondition_verified')),
        source_json TEXT NOT NULL, summary TEXT NOT NULL,
        operations_json TEXT NOT NULL, rollback_reference TEXT NOT NULL,
        caveats_json TEXT NOT NULL, plan_snapshot_json TEXT NOT NULL,
        plan_snapshot_hash TEXT NOT NULL, approval_snapshot_json TEXT NOT NULL,
        approval_snapshot_hash TEXT NOT NULL,
        observation_hash TEXT NOT NULL,
        record_hash TEXT NOT NULL,
        external_execution_available INTEGER NOT NULL CHECK(external_execution_available=0)
    );
    CREATE INDEX deployment_observations_plan
        ON deployment_observations(project_id,plan_id,observed_at,id);
    """,
]


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self):
        """Nested writes share the outer transaction; failures roll back the entire operation."""
        nested = self.connection.in_transaction
        if not nested:
            self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            if not nested:
                self.connection.commit()
        except BaseException:
            if not nested:
                self.connection.rollback()
            raise

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def migrate(self) -> None:
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in self.connection.execute("SELECT version FROM schema_migrations")}
        if applied and (max(applied) > SCHEMA_VERSION or applied != set(range(1, max(applied) + 1))):
            raise ValueError("unsupported or incomplete database migration history")
        for version, sql in enumerate(MIGRATIONS, start=1):
            if version in applied:
                continue
            try:
                self.connection.executescript(
                    "BEGIN IMMEDIATE;\n" + sql
                    + f"\nINSERT INTO schema_migrations VALUES ({version}, datetime('now'));\nCOMMIT;"
                )
            except BaseException:
                self.connection.rollback()
                raise

    def create_project(
        self,
        project_id: str,
        name: str,
        business_context: dict[str, Any],
        sites: list[str],
        portfolio_name: str,
        created_at: str,
    ) -> None:
        portfolio_id = stable_id("portfolio", portfolio_name)
        with self.transaction():
            existing = self.rows("projects", project_id)
            if existing:
                current_sites = sorted(row["canonical_url"] for row in self.rows("sites", project_id))
                if (
                    existing[0]["business_context_json"] != canonical_json(business_context)
                    or existing[0]["name"] != name or existing[0]["portfolio_id"] != portfolio_id
                    or current_sites != sorted(set(sites))
                ):
                    raise ValueError("project exists with different approved context or scope; create a new project")
                return
            self.connection.execute(
                "INSERT OR IGNORE INTO portfolios(id,name,created_at) VALUES (?,?,?)",
                (portfolio_id, portfolio_name, created_at),
            )
            self.connection.execute(
                """INSERT INTO projects
                   (id,portfolio_id,name,created_at,business_context_json,claim_policy)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(id) DO NOTHING""",
                (
                    project_id,
                    portfolio_id,
                    name,
                    created_at,
                    canonical_json(business_context),
                    "approved_source_material_no_automated_truth_score",
                ),
            )
            for url in sites:
                site_id = stable_id("site", portfolio_id, url)
                self.connection.execute(
                    "INSERT OR IGNORE INTO sites(id,portfolio_id,canonical_url,name) VALUES (?,?,?,?)",
                    (site_id, portfolio_id, url, url),
                )
                self.connection.execute(
                    "INSERT OR IGNORE INTO project_sites(project_id,site_id) VALUES (?,?)",
                    (project_id, site_id),
                )

    def has_project(self, project_id: str) -> bool:
        return self.connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is not None

    def portfolio_for_project(self, project_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT p.* FROM portfolios p JOIN projects j ON j.portfolio_id=p.id WHERE j.id=?",
            (project_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"unknown project: {project_id}")
        return dict(row)

    def record_by_id(self, table: str, row_id: str) -> dict[str, Any] | None:
        allowed = {
            "portfolios", "sites", "projects", "snapshots", "evidence", "findings", "recommendations",
            "decisions", "action_plans", "experiments", "ownership_assignments", "ownership_conflicts",
            "ownership_decisions", "ownership_action_plans", "deployment_observations",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        row = self.connection.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        return dict(row) if row else None

    def restore_records(self, table: str, records: Iterable[dict[str, Any]]) -> None:
        """Insert already validated decoded records, accepting exact shared rows only."""
        allowed = {
            "portfolios", "sites", "projects", "snapshots", "evidence", "findings", "recommendations",
            "decisions", "action_plans", "experiments", "ownership_assignments", "ownership_conflicts",
            "ownership_decisions", "ownership_action_plans", "deployment_observations",
        }
        if table not in allowed:
            raise ValueError(f"unsupported restore table: {table}")
        columns = [row[1] for row in self.connection.execute(f"PRAGMA table_info({table})")]
        decoded_columns = [column[:-5] if column.endswith("_json") else column for column in columns]
        for record in records:
            if set(record) != set(decoded_columns):
                raise ValueError(f"restore {table} record shape is invalid")
            existing = self.record_by_id(table, record["id"])
            if existing:
                if self.decode(existing) != record:
                    raise ValueError(f"restore conflicts with existing {table} record")
                continue
            encoded = []
            for column, decoded_column in zip(columns, decoded_columns):
                value = record[decoded_column]
                if column.endswith("_json"):
                    value = canonical_json(value)
                elif column in {"external_execution_available", "active"}:
                    value = int(value)
                encoded.append(value)
            self.connection.execute(
                f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                encoded,
            )

    def restore_project_sites(self, project_id: str, site_ids: Iterable[str]) -> None:
        for site_id in site_ids:
            self.connection.execute(
                "INSERT OR IGNORE INTO project_sites(project_id,site_id) VALUES (?,?)",
                (project_id, site_id),
            )

    def save_snapshot(self, result: AdaptedResult) -> None:
        with self.transaction():
            self.connection.execute(
                """INSERT OR IGNORE INTO snapshots
                   (id,project_id,tool,captured_at,provider_json,arguments_json,envelope_json,
                    raw_json,content_hash,source_locator,adapter_version,freshness,caveats_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    result.snapshot_id,
                    result.project_id,
                    result.tool,
                    result.captured_at,
                    canonical_json(result.provider),
                    canonical_json(result.arguments),
                    canonical_json(result.envelope),
                    canonical_json(result.raw),
                    result.content_hash,
                    result.source_locator,
                    result.raw["adapter_version"],
                    result.freshness,
                    canonical_json(list(result.caveats)),
                ),
            )

    def upsert_rows(self, table: str, rows: Iterable[dict[str, Any]]) -> None:
        allowed = {"evidence", "findings", "recommendations"}
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        rows = list(rows)
        if not rows:
            return
        columns = list(rows[0])
        placeholders = ",".join("?" for _ in columns)
        query = (
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
            "ON CONFLICT(id) DO NOTHING"
        )
        with self.transaction():
            self.connection.executemany(query, ([row[column] for column in columns] for row in rows))

    def rows(self, table: str, project_id: str, order_by: str = "id") -> list[dict[str, Any]]:
        allowed = {
            "projects",
            "sites",
            "project_sites",
            "snapshots",
            "evidence",
            "findings",
            "recommendations",
            "decisions",
            "action_plans",
            "experiments",
            "ownership_assignments",
            "ownership_conflicts",
            "ownership_decisions",
            "ownership_action_plans",
            "deployment_observations",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        if table == "sites":
            query = "SELECT s.* FROM sites s JOIN project_sites ps ON ps.site_id=s.id WHERE ps.project_id=? ORDER BY s.id"
        elif table == "project_sites":
            query = "SELECT * FROM project_sites WHERE project_id=? ORDER BY site_id"
        else:
            safe_orders = {"id", "fingerprint", "impact DESC, confidence DESC, id"}
            if order_by not in safe_orders:
                raise ValueError("unsafe order_by")
            query = f"SELECT * FROM {table} WHERE {'id' if table == 'projects' else 'project_id'}=? ORDER BY {order_by}"
        return [dict(row) for row in self.connection.execute(query, (project_id,))]

    def one(self, table: str, row_id: str) -> dict[str, Any] | None:
        allowed = {
            "evidence", "findings", "recommendations", "action_plans", "experiments", "snapshots",
            "ownership_assignments", "ownership_conflicts", "ownership_decisions", "ownership_action_plans",
            "deployment_observations",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        row = self.connection.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        return dict(row) if row else None

    def evidence_for_subject(self, project_id: str, subject: str, exclude_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM evidence WHERE project_id=? AND subject=?"
        args: list[Any] = [project_id, subject]
        if exclude_id:
            query += " AND id<>?"
            args.append(exclude_id)
        query += " ORDER BY observed_at, id"
        return [dict(row) for row in self.connection.execute(query, args)]

    def insert(self, table: str, row: dict[str, Any]) -> None:
        allowed = {
            "decisions", "action_plans", "experiments", "ownership_assignments",
            "ownership_decisions", "ownership_action_plans", "deployment_observations",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        columns = list(row)
        query = (
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
            "ON CONFLICT(id) DO NOTHING"
        )
        with self.transaction():
            self.connection.execute(query, [row[column] for column in columns])

    def update(self, table: str, row_id: str, values: dict[str, Any]) -> None:
        allowed = {"recommendations", "action_plans", "experiments"}
        if table not in allowed or not values:
            raise ValueError(f"unsupported update: {table}")
        columns = list(values)
        assignments = ",".join(f"{column}=?" for column in columns)
        with self.transaction():
            self.connection.execute(
                f"UPDATE {table} SET {assignments} WHERE id=?",
                [values[column] for column in columns] + [row_id],
            )

    def replace_active_ownership_conflicts(self, project_id: str, rows: Iterable[dict[str, Any]]) -> None:
        rows = list(rows)
        with self.transaction():
            self.connection.execute("UPDATE ownership_conflicts SET active=0 WHERE project_id=?", (project_id,))
            for row in rows:
                columns = list(row)
                query = (
                    f"INSERT INTO ownership_conflicts ({','.join(columns)}) "
                    f"VALUES ({','.join('?' for _ in columns)}) "
                    "ON CONFLICT(id) DO UPDATE SET active=1"
                )
                self.connection.execute(query, [row[column] for column in columns])

    @staticmethod
    def decode(row: dict[str, Any]) -> dict[str, Any]:
        decoded = dict(row)
        for key in list(decoded):
            if key.endswith("_json"):
                decoded[key[:-5]] = json_value(decoded.pop(key))
        if "external_execution_available" in decoded:
            decoded["external_execution_available"] = bool(decoded["external_execution_available"])
        if "active" in decoded:
            decoded["active"] = bool(decoded["active"])
        return decoded
