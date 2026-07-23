import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from backend.app.config import get_settings
from backend.tests.integration.isolation import (
    migration_database_urls,
    quote_postgres_identifier,
)

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_REVISION = "3884ec28fea9"
WP1_REVISION = "b57c2f9e1a6d"
WP7_HEAD_REVISION = "7d3c9b1a4e62"
WP7_TABLES = {
    "llm_usage_attempts",
    "operations_reconciliation_state",
    "score_cross_checks",
    "golden_set_snapshots",
    "golden_set_snapshot_entries",
    "quality_releases",
    "quality_release_jds",
}
ColumnSpec = tuple[str, bool, str | None]
WP7_COLUMN_SPECS: dict[str, dict[str, ColumnSpec]] = {
    "operations_reconciliation_state": {
        "key": ("character varying(32)", False, None),
        "next_period_start": ("timestamp with time zone", False, None),
        "updated_at": ("timestamp with time zone", False, None),
    },
    "llm_usage_attempts": {
        "id": ("bigint", False, "sequence"),
        "call_group_id": ("uuid", False, None),
        "trace_id": ("character varying(64)", True, None),
        "ingestion_job_id": ("bigint", True, None),
        "score_id": ("bigint", True, None),
        "jd_id": ("bigint", True, None),
        "rule_version_id": ("bigint", True, None),
        "operation": ("character varying(32)", False, None),
        "attempt_role": ("character varying(16)", False, None),
        "requested_model": ("character varying(128)", False, None),
        "actual_model": ("character varying(128)", True, None),
        "prompt_version": ("character varying(64)", False, None),
        "status": ("character varying(32)", False, None),
        "input_tokens": ("integer", True, None),
        "output_tokens": ("integer", True, None),
        "input_price_cny_per_million": ("numeric(18,6)", False, None),
        "output_price_cny_per_million": ("numeric(18,6)", False, None),
        "estimated_cost_cny": ("numeric(24,12)", True, None),
        "latency_ms": ("integer", True, None),
        "error_code": ("character varying(64)", True, None),
        "started_at": ("timestamp with time zone", False, None),
        "finished_at": ("timestamp with time zone", True, None),
    },
    "score_cross_checks": {
        "id": ("bigint", False, "sequence"),
        "score_id": ("bigint", False, None),
        "secondary_model": ("character varying(128)", False, None),
        "prompt_version": ("character varying(64)", False, None),
        "sample_reasons": ("jsonb", False, None),
        "state": ("character varying(32)", False, None),
        "attempts": ("integer", False, "0"),
        "lease_expires_at": ("timestamp with time zone", True, None),
        "lease_token": ("uuid", True, None),
        "last_error_code": ("character varying(64)", True, None),
        "secondary_total_score": ("numeric(6,2)", True, None),
        "secondary_dimensions": ("jsonb", True, None),
        "absolute_diff": ("numeric(6,2)", True, None),
        "threshold_snapshot": ("numeric(6,2)", False, None),
        "completed_at": ("timestamp with time zone", True, None),
        "created_at": ("timestamp with time zone", False, "now()"),
        "updated_at": ("timestamp with time zone", True, "now()"),
    },
    "golden_set_snapshots": {
        "id": ("bigint", False, "sequence"),
        "content_sha256": ("character varying(64)", False, None),
        "item_count": ("integer", False, None),
        "created_by_user_id": ("bigint", False, None),
        "created_at": ("timestamp with time zone", False, None),
    },
    "golden_set_snapshot_entries": {
        "id": ("bigint", False, "sequence"),
        "snapshot_id": ("bigint", False, None),
        "candidate_id": ("bigint", False, None),
        "jd_id": ("bigint", False, None),
        "label": ("character varying(32)", False, None),
    },
    "quality_releases": {
        "id": ("bigint", False, "sequence"),
        "golden_snapshot_id": ("bigint", False, None),
        "window_start": ("timestamp with time zone", False, None),
        "window_end": ("timestamp with time zone", False, None),
        "status": ("character varying(32)", False, None),
        "metrics_json": ("jsonb", False, None),
        "targets_json": ("jsonb", False, None),
        "created_by_user_id": ("bigint", False, None),
        "created_at": ("timestamp with time zone", False, None),
    },
    "quality_release_jds": {
        "id": ("bigint", False, "sequence"),
        "quality_release_id": ("bigint", False, None),
        "jd_id": ("bigint", False, None),
        "rule_version_id": ("bigint", False, None),
        "metrics_json": ("jsonb", False, None),
    },
}
WP7_SCORE_COLUMN_SPECS = {
    "llm_judge_call_group_id": ("uuid", True, None),
}
WP7_PRIMARY_KEY_SPECS = {
    "llm_usage_attempts": "PRIMARY KEY (id)",
    "operations_reconciliation_state": "PRIMARY KEY (key)",
    "score_cross_checks": "PRIMARY KEY (id)",
    "golden_set_snapshots": "PRIMARY KEY (id)",
    "golden_set_snapshot_entries": "PRIMARY KEY (id)",
    "quality_releases": "PRIMARY KEY (id)",
    "quality_release_jds": "PRIMARY KEY (id)",
}
WP7_NAMED_CONSTRAINT_SPECS = {
    ("llm_usage_attempts", "ck_llm_usage_operation"): (
        "CHECK (operation::text = ANY (ARRAY['extract'::character varying, "
        "'judge'::character varying, 'cross_check'::character varying, "
        "'lightweight'::character varying]::text[]))"
    ),
    ("llm_usage_attempts", "ck_llm_usage_attempt_role"): (
        "CHECK (attempt_role::text = ANY (ARRAY['primary'::character varying, "
        "'fallback'::character varying, 'secondary'::character varying]::text[]))"
    ),
    ("llm_usage_attempts", "ck_llm_usage_status"): (
        "CHECK (status::text = ANY (ARRAY['pending'::character varying, "
        "'succeeded'::character varying, 'unavailable'::character varying, "
        "'invalid_response'::character varying, 'configuration_error'::character varying, "
        "'abandoned'::character varying]::text[]))"
    ),
    ("llm_usage_attempts", "ck_llm_usage_terminal_time"): (
        "CHECK ((status::text = 'pending'::text) = (finished_at IS NULL))"
    ),
    ("llm_usage_attempts", "ck_llm_usage_input_tokens"): (
        "CHECK (input_tokens IS NULL OR input_tokens >= 0)"
    ),
    ("llm_usage_attempts", "ck_llm_usage_output_tokens"): (
        "CHECK (output_tokens IS NULL OR output_tokens >= 0)"
    ),
    ("llm_usage_attempts", "ck_llm_usage_prices"): (
        "CHECK (input_price_cny_per_million >= 0::numeric AND "
        "output_price_cny_per_million >= 0::numeric)"
    ),
    ("llm_usage_attempts", "ck_llm_usage_cost"): (
        "CHECK (estimated_cost_cny IS NULL OR estimated_cost_cny >= 0::numeric)"
    ),
    ("llm_usage_attempts", "ck_llm_usage_latency"): (
        "CHECK (latency_ms IS NULL OR latency_ms >= 0)"
    ),
    ("score_cross_checks", "uq_cross_checks_score_model_prompt"): (
        "UNIQUE (score_id, secondary_model, prompt_version)"
    ),
    ("score_cross_checks", "ck_cross_checks_state"): (
        "CHECK (state::text = ANY (ARRAY['queued'::character varying, "
        "'running'::character varying, 'completed'::character varying, "
        "'retryable_failed'::character varying, "
        "'terminal_failed'::character varying]::text[]))"
    ),
    ("score_cross_checks", "ck_cross_checks_attempts"): "CHECK (attempts >= 0)",
    ("golden_set_snapshots", "ck_golden_snapshot_item_count"): (
        "CHECK (item_count >= 0)"
    ),
    ("golden_set_snapshots", "uq_golden_snapshots_content_sha256"): (
        "UNIQUE (content_sha256)"
    ),
    ("golden_set_snapshot_entries", "uq_golden_snapshot_candidate_jd"): (
        "UNIQUE (snapshot_id, candidate_id, jd_id)"
    ),
    ("golden_set_snapshot_entries", "ck_golden_snapshot_label"): (
        "CHECK (label::text = ANY (ARRAY['advance'::character varying, "
        "'reject'::character varying, 'borderline'::character varying]::text[]))"
    ),
    ("quality_releases", "ck_quality_release_status"): (
        "CHECK (status::text = ANY (ARRAY['meets_target'::character varying, "
        "'below_target'::character varying]::text[]))"
    ),
    ("quality_release_jds", "uq_quality_release_jd"): (
        "UNIQUE (quality_release_id, jd_id)"
    ),
}
WP7_FOREIGN_KEY_DEFINITIONS = {
    ("llm_usage_attempts", "FOREIGN KEY (ingestion_job_id) REFERENCES ingestion_jobs(id)"),
    ("llm_usage_attempts", "FOREIGN KEY (score_id) REFERENCES scores(id)"),
    ("llm_usage_attempts", "FOREIGN KEY (jd_id) REFERENCES jds(id)"),
    ("llm_usage_attempts", "FOREIGN KEY (rule_version_id) REFERENCES rule_versions(id)"),
    ("score_cross_checks", "FOREIGN KEY (score_id) REFERENCES scores(id)"),
    ("golden_set_snapshots", "FOREIGN KEY (created_by_user_id) REFERENCES users(id)"),
    (
        "golden_set_snapshot_entries",
        "FOREIGN KEY (snapshot_id) REFERENCES golden_set_snapshots(id) ON DELETE CASCADE",
    ),
    (
        "golden_set_snapshot_entries",
        "FOREIGN KEY (candidate_id) REFERENCES candidates(id)",
    ),
    ("golden_set_snapshot_entries", "FOREIGN KEY (jd_id) REFERENCES jds(id)"),
    (
        "quality_releases",
        "FOREIGN KEY (golden_snapshot_id) REFERENCES golden_set_snapshots(id)",
    ),
    ("quality_releases", "FOREIGN KEY (created_by_user_id) REFERENCES users(id)"),
    (
        "quality_release_jds",
        "FOREIGN KEY (quality_release_id) REFERENCES quality_releases(id) ON DELETE CASCADE",
    ),
    ("quality_release_jds", "FOREIGN KEY (jd_id) REFERENCES jds(id)"),
    ("quality_release_jds", "FOREIGN KEY (rule_version_id) REFERENCES rule_versions(id)"),
}
WP7_INDEX_SPECS = {
    "ix_llm_usage_attempts_call_group_id": (
        "llm_usage_attempts",
        ("call_group_id",),
        False,
    ),
    "ix_llm_usage_attempts_trace_id": ("llm_usage_attempts", ("trace_id",), False),
    "ix_llm_usage_attempts_ingestion_job_id": (
        "llm_usage_attempts",
        ("ingestion_job_id",),
        False,
    ),
    "ix_llm_usage_attempts_score_id": ("llm_usage_attempts", ("score_id",), False),
    "ix_llm_usage_attempts_jd_id": ("llm_usage_attempts", ("jd_id",), False),
    "ix_llm_usage_attempts_rule_version_id": (
        "llm_usage_attempts",
        ("rule_version_id",),
        False,
    ),
    "ix_llm_usage_started_id": ("llm_usage_attempts", ("started_at", "id"), False),
    "ix_llm_usage_status_started": (
        "llm_usage_attempts",
        ("status", "started_at"),
        False,
    ),
    "ix_llm_usage_jd_rule_started": (
        "llm_usage_attempts",
        ("jd_id", "rule_version_id", "started_at"),
        False,
    ),
    "ix_llm_usage_operation_started": (
        "llm_usage_attempts",
        ("operation", "started_at"),
        False,
    ),
    "ix_cross_checks_state_lease": (
        "score_cross_checks",
        ("state", "lease_expires_at"),
        False,
    ),
    "ix_cross_checks_score_id_id": (
        "score_cross_checks",
        ("score_id", "id"),
        False,
    ),
    "ix_cross_checks_completed_diff": (
        "score_cross_checks",
        ("completed_at", "absolute_diff"),
        False,
    ),
    "ix_quality_release_created_id": (
        "quality_releases",
        ("created_at", "id"),
        False,
    ),
    "ix_quality_release_jds_jd_release": (
        "quality_release_jds",
        ("jd_id", "quality_release_id"),
        False,
    ),
    "ix_scores_llm_judge_call_group_id": (
        "scores",
        ("llm_judge_call_group_id",),
        False,
    ),
}


def _alembic(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=REPO_ROOT,
        env=env,
    )


async def _create_database(admin_dsn: str, database_name: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(
            f"CREATE DATABASE {quote_postgres_identifier(database_name)}"
        )
    finally:
        await connection.close()


async def _drop_database(admin_dsn: str, database_name: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1 AND pid <> pg_backend_pid()
            """,
            database_name,
        )
        await connection.execute(
            f"DROP DATABASE IF EXISTS {quote_postgres_identifier(database_name)}"
        )
    finally:
        await connection.close()


async def _schema_objects(connection):
    tables = {
        row["table_name"]
        for row in await connection.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
    }
    constraints = {
        (row["table_name"], row["constraint_name"]): (
            row["constraint_type"],
            row["definition"],
        )
        for row in await connection.fetch(
            """
            SELECT cls.relname AS table_name,
                   con.conname AS constraint_name,
                   con.contype::text AS constraint_type,
                   pg_get_constraintdef(con.oid, true) AS definition
            FROM pg_constraint AS con
            JOIN pg_class AS cls ON cls.oid = con.conrelid
            JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
            WHERE ns.nspname = 'public'
            """
        )
    }
    indexes = {
        row["index_name"]: (
            row["table_name"],
            tuple(row["column_names"]),
            row["is_unique"],
        )
        for row in await connection.fetch(
            """
            SELECT tbl.relname AS table_name,
                   idx.relname AS index_name,
                   ind.indisunique AS is_unique,
                   array_agg(att.attname ORDER BY key.ordinality) AS column_names
            FROM pg_index AS ind
            JOIN pg_class AS idx ON idx.oid = ind.indexrelid
            JOIN pg_class AS tbl ON tbl.oid = ind.indrelid
            JOIN pg_namespace AS ns ON ns.oid = tbl.relnamespace
            JOIN LATERAL unnest(ind.indkey) WITH ORDINALITY AS key(attnum, ordinality)
              ON true
            JOIN pg_attribute AS att
              ON att.attrelid = tbl.oid AND att.attnum = key.attnum
            WHERE ns.nspname = 'public'
            GROUP BY tbl.relname, idx.relname, ind.indisunique
            """
        )
    }
    columns: dict[str, dict[str, ColumnSpec]] = {}
    for row in await connection.fetch(
        """
        SELECT cls.relname AS table_name,
               att.attname AS column_name,
               format_type(att.atttypid, att.atttypmod) AS formatted_type,
               NOT att.attnotnull AS is_nullable,
               pg_get_expr(def.adbin, def.adrelid) AS default_expression
        FROM pg_attribute AS att
        JOIN pg_class AS cls ON cls.oid = att.attrelid
        JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
        LEFT JOIN pg_attrdef AS def
          ON def.adrelid = att.attrelid AND def.adnum = att.attnum
        WHERE ns.nspname = 'public'
          AND att.attnum > 0
          AND NOT att.attisdropped
        ORDER BY cls.relname, att.attnum
        """
    ):
        default = row["default_expression"]
        if default is not None and default.startswith("nextval("):
            default = "sequence"
        columns.setdefault(row["table_name"], {})[row["column_name"]] = (
            row["formatted_type"],
            row["is_nullable"],
            default,
        )
    rule_columns = {
        row["column_name"]: row["is_nullable"]
        for row in await connection.fetch(
            """
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'rule_versions'
            """
        )
    }
    return tables, columns, constraints, indexes, rule_columns


@pytest.mark.asyncio
async def test_alembic_round_trip_from_base() -> None:
    database_name = f"smartscreen_migration_{uuid4().hex}"
    urls = migration_database_urls(get_settings().DATABASE_URL, database_name)
    env = os.environ.copy()
    env["DATABASE_URL"] = urls.async_url
    env["DATABASE_URL_SYNC"] = urls.sync_url

    try:
        await _create_database(urls.admin_dsn, database_name)
        downgrade = _alembic("downgrade", "base", env=env)
        assert downgrade.returncode == 0, downgrade.stderr
        upgrade = _alembic("upgrade", "head", env=env)
        assert upgrade.returncode == 0, upgrade.stderr
        current = _alembic("current", env=env)
        assert current.returncode == 0, current.stderr
        assert WP7_HEAD_REVISION in current.stdout

        connection = await asyncpg.connect(urls.sync_url)
        try:
            tables, columns, constraints, indexes, rule_columns = (
                await _schema_objects(connection)
            )
        finally:
            await connection.close()

        assert WP7_TABLES <= tables
        assert {table: columns[table] for table in WP7_TABLES} == WP7_COLUMN_SPECS
        assert {
            name: columns["scores"][name] for name in WP7_SCORE_COLUMN_SPECS
        } == WP7_SCORE_COLUMN_SPECS
        expected_named_constraints = {
            key: ("u" if key[1].startswith("uq_") else "c", definition)
            for key, definition in WP7_NAMED_CONSTRAINT_SPECS.items()
        }
        assert {
            key: constraints.get(key) for key in WP7_NAMED_CONSTRAINT_SPECS
        } == expected_named_constraints
        assert {
            table: definition
            for (table, _), (constraint_type, definition) in constraints.items()
            if table in WP7_TABLES and constraint_type == "p"
        } == WP7_PRIMARY_KEY_SPECS
        assert {
            (table, definition)
            for (table, _), (constraint_type, definition) in constraints.items()
            if table in WP7_TABLES and constraint_type == "f"
        } == WP7_FOREIGN_KEY_DEFINITIONS
        assert {
            name: indexes.get(name) for name in WP7_INDEX_SPECS
        } == WP7_INDEX_SPECS
        assert rule_columns["status"] == "NO"
        assert rule_columns["published_at"] == "YES"
        assert ("rule_versions", "ck_rule_versions_status") in constraints
        assert ("rule_versions", "uq_rule_versions_jd_version") in constraints

        downgrade = _alembic("downgrade", "25954dc70368", env=env)
        assert downgrade.returncode == 0, downgrade.stderr
        connection = await asyncpg.connect(urls.sync_url)
        try:
            (
                downgraded_tables,
                downgraded_columns,
                downgraded_constraints,
                downgraded_indexes,
                wp6c_rule_columns,
            ) = await _schema_objects(connection)
        finally:
            await connection.close()

        assert WP7_TABLES.isdisjoint(downgraded_tables)
        assert "llm_judge_call_group_id" not in downgraded_columns["scores"]
        assert not any(table in WP7_TABLES for table, _ in downgraded_constraints)
        assert not {
            table
            for (table, _), (constraint_type, _) in downgraded_constraints.items()
            if constraint_type == "p"
        }.intersection(WP7_PRIMARY_KEY_SPECS)
        assert WP7_INDEX_SPECS.keys().isdisjoint(downgraded_indexes)
        assert wp6c_rule_columns["status"] == "NO"
        assert wp6c_rule_columns["published_at"] == "YES"
        assert ("rule_versions", "ck_rule_versions_status") in downgraded_constraints
        assert ("rule_versions", "uq_rule_versions_jd_version") in downgraded_constraints

        downgrade = _alembic("downgrade", "f412481450cf", env=env)
        assert downgrade.returncode == 0, downgrade.stderr
        connection = await asyncpg.connect(urls.sync_url)
        try:
            _, _, older_constraints, _, older_rule_columns = await _schema_objects(connection)
        finally:
            await connection.close()

        assert "status" not in older_rule_columns
        assert older_rule_columns["published_at"] == "NO"
        assert ("rule_versions", "ck_rule_versions_status") not in older_constraints
        assert ("rule_versions", "uq_rule_versions_jd_version") not in older_constraints
    finally:
        await _drop_database(urls.admin_dsn, database_name)


@pytest.mark.asyncio
async def test_wp1_migration_preserves_legacy_candidate() -> None:
    database_name = f"smartscreen_migration_{uuid4().hex}"
    urls = migration_database_urls(get_settings().DATABASE_URL, database_name)
    env = os.environ.copy()
    env["DATABASE_URL"] = urls.async_url
    env["DATABASE_URL_SYNC"] = urls.sync_url

    try:
        await _create_database(urls.admin_dsn, database_name)
        baseline = _alembic("upgrade", BASELINE_REVISION, env=env)
        assert baseline.returncode == 0, baseline.stderr

        connection = await asyncpg.connect(urls.sync_url)
        try:
            await connection.execute(
                """
                INSERT INTO candidates (
                    source, name_cipher, raw_file_key, pii_hash
                ) VALUES ($1, $2, $3, $4)
                """,
                "upload",
                "encrypted-name",
                "C:/legacy/tmp/resume.pdf",
                "a" * 64,
            )
        finally:
            await connection.close()

        upgrade = _alembic("upgrade", WP1_REVISION, env=env)
        assert upgrade.returncode == 0, upgrade.stderr

        connection = await asyncpg.connect(urls.sync_url)
        try:
            row = await connection.fetchrow(
                """
                SELECT raw_file_key, raw_file_sha256, raw_file_size_bytes,
                       raw_file_content_type, raw_file_original_name_cipher
                FROM candidates
                WHERE pii_hash = $1
                """,
                "a" * 64,
            )
            assert row is not None
            assert row["raw_file_key"] == "C:/legacy/tmp/resume.pdf"
            assert row["raw_file_sha256"] is None
            assert row["raw_file_size_bytes"] is None
            assert row["raw_file_content_type"] is None
            assert row["raw_file_original_name_cipher"] is None
        finally:
            await connection.close()

        downgrade = _alembic("downgrade", BASELINE_REVISION, env=env)
        assert downgrade.returncode == 0, downgrade.stderr
    finally:
        await _drop_database(urls.admin_dsn, database_name)
