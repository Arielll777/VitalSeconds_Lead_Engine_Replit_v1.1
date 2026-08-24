"""Safe sequential migrations. Never DROP operational data. Never swallow Postgres errors."""

from __future__ import annotations

import logging
from typing import Callable, List, Tuple

logger = logging.getLogger("vitalseconds.migrations")

MigrationFn = Callable[[object, str], None]


def _ensure_schema_version_table(conn, backend: str) -> None:
    if backend == "postgres":
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )


def _applied_versions(conn) -> set:
    rows = conn.execute("SELECT version FROM schema_version").fetchall()
    out = set()
    for r in rows:
        out.add(int(r["version"] if hasattr(r, "keys") else r[0]))
    return out


def _add_column(conn, backend: str, table: str, name: str, typ: str) -> None:
    """Additive column. Postgres errors are NEVER swallowed. SQLite duplicate-column is local-only."""
    if backend == "postgres":
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {typ}")
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
    except Exception as exc:
        msg = str(exc).lower()
        if "duplicate column" in msg or "already exists" in msg:
            return
        raise


def migration_001_base_tables(conn, backend: str) -> None:
    from vitalseconds.db.schema import POSTGRES_SCHEMA_SQL, SQLITE_SCHEMA_SQL

    if backend == "postgres":
        conn.executescript(POSTGRES_SCHEMA_SQL)
    else:
        conn.executescript(SQLITE_SCHEMA_SQL)


def migration_002_append_only_triggers(conn, backend: str) -> None:
    tables = ("verification_events", "audit_log", "import_source_rows")
    if backend == "postgres":
        for t in tables:
            fn = f"prevent_mutate_{t}"
            conn.execute(
                f"""
                CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger AS $$
                BEGIN
                  RAISE EXCEPTION 'Append-only table %: % not allowed', TG_TABLE_NAME, TG_OP;
                END;
                $$ LANGUAGE plpgsql
                """
            )
            conn.execute(f"DROP TRIGGER IF EXISTS trg_no_update_{t} ON {t}")
            conn.execute(f"DROP TRIGGER IF EXISTS trg_no_delete_{t} ON {t}")
            conn.execute(
                f"""
                CREATE TRIGGER trg_no_update_{t}
                BEFORE UPDATE ON {t}
                FOR EACH ROW EXECUTE FUNCTION {fn}()
                """
            )
            conn.execute(
                f"""
                CREATE TRIGGER trg_no_delete_{t}
                BEFORE DELETE ON {t}
                FOR EACH ROW EXECUTE FUNCTION {fn}()
                """
            )
    else:
        for t in tables:
            conn.execute(f"DROP TRIGGER IF EXISTS trg_no_update_{t}")
            conn.execute(f"DROP TRIGGER IF EXISTS trg_no_delete_{t}")
            conn.execute(
                f"""
                CREATE TRIGGER trg_no_update_{t}
                BEFORE UPDATE ON {t}
                BEGIN
                  SELECT RAISE(ABORT, 'Append-only: UPDATE not allowed on {t}');
                END
                """
            )
            conn.execute(
                f"""
                CREATE TRIGGER trg_no_delete_{t}
                BEFORE DELETE ON {t}
                BEGIN
                  SELECT RAISE(ABORT, 'Append-only: DELETE not allowed on {t}');
                END
                """
            )


def migration_003_v11_columns_and_review(conn, backend: str) -> None:
    """Add v1.1 columns on existing v1 tables, backfill, then flag duplicate actives.

    Never delete history. Extra active candidates go to IMPORT_REVIEW.
    """
    _add_column(conn, backend, "companies", "merged_into_company_id", "INTEGER")
    _add_column(conn, backend, "companies", "buying_group", "TEXT")
    _add_column(conn, backend, "companies", "notes", "TEXT")
    _add_column(conn, backend, "executives", "technical_status", "TEXT")
    _add_column(conn, backend, "executives", "campaign_disposition", "TEXT")
    _add_column(conn, backend, "email_candidates", "attempt_order", "INTEGER")
    _add_column(conn, backend, "email_candidates", "pass_order", "INTEGER")
    _add_column(conn, backend, "import_source_rows", "sheet_name", "TEXT")
    _add_column(conn, backend, "import_source_rows", "fingerprint", "TEXT")
    _add_column(conn, backend, "grisha_export_events", "verification_batch", "TEXT")

    # Backfill technical/campaign from legacy current_status when still at defaults.
    conn.execute(
        """
        UPDATE executives
        SET technical_status = CASE
                WHEN current_status = 'PREVIOUSLY_VALID' THEN 'VERIFIED_VALID'
                ELSE current_status
            END
        WHERE (technical_status IS NULL OR technical_status = '' OR technical_status = 'NET_NEW')
          AND current_status IS NOT NULL
          AND current_status != 'NET_NEW'
        """
    )
    conn.execute(
        """
        UPDATE executives
        SET campaign_disposition = CASE
                WHEN current_status IN ('DOMAIN_QUARANTINED') THEN 'DOMAIN_QUARANTINED'
                WHEN current_status IN ('EXCLUDED') THEN 'EXCLUDED'
                WHEN current_status IN ('DO_NOT_CONTACT') THEN 'DO_NOT_CONTACT'
                ELSE COALESCE(NULLIF(campaign_disposition, ''), 'ACTIVE')
            END
        WHERE campaign_disposition IS NULL OR campaign_disposition = ''
        """
    )
    conn.execute(
        """
        UPDATE email_candidates
        SET attempt_order = CASE
                WHEN attempt_order IS NOT NULL AND attempt_order > 0 THEN attempt_order
                WHEN pass_order = 0 THEN 1
                ELSE pass_order
            END
        WHERE attempt_order IS NULL OR attempt_order = 0
        """
    )

    rows = conn.execute(
        """
        SELECT executive_id, COUNT(*) AS cnt
        FROM email_candidates
        WHERE is_active = 1
        GROUP BY executive_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for r in rows:
        eid = r["executive_id"] if hasattr(r, "keys") else r[0]
        extras = conn.execute(
            """
            SELECT candidate_id FROM email_candidates
            WHERE executive_id = ? AND is_active = 1
            ORDER BY candidate_id ASC
            """,
            (eid,),
        ).fetchall()
        for extra in extras[1:]:
            cid = extra["candidate_id"] if hasattr(extra, "keys") else extra[0]
            conn.execute(
                "UPDATE email_candidates SET is_active = 0, status = 'IMPORT_REVIEW' WHERE candidate_id = ?",
                (cid,),
            )
            conn.execute(
                """
                INSERT INTO audit_log (entity_type, entity_id, action, old_value, new_value, reason)
                VALUES ('CANDIDATE', ?, 'DUPLICATE_ACTIVE_DETECTED', 'is_active=1', 'IMPORT_REVIEW',
                        'Migration: extra active candidate placed in review; not deleted')
                """,
                (str(cid),),
            )
        conn.execute(
            """
            UPDATE executives SET campaign_disposition = 'HOLD', updated_at = datetime('now')
            WHERE executive_id = ?
            """,
            (eid,),
        )


def migration_004_verification_source_columns(conn, backend: str) -> None:
    """Additive source-identity columns. Postgres errors are NOT swallowed."""
    cols = [
        ("source_file_sha256", "TEXT"),
        ("source_row_number", "INTEGER"),
        ("source_type", "TEXT"),
        ("verifier", "TEXT"),
    ]
    if backend == "postgres":
        for name, typ in cols:
            conn.execute(
                f"ALTER TABLE verification_events ADD COLUMN IF NOT EXISTS {name} {typ}"
            )
        return
    # SQLite: ADD COLUMN errors if the column already exists — that is expected and local-only.
    for name, typ in cols:
        try:
            conn.execute(f"ALTER TABLE verification_events ADD COLUMN {name} {typ}")
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg or "already exists" in msg:
                continue
            raise


def migration_005_partial_unique_indexes(conn, backend: str) -> None:
    """One active candidate per executive. One ACTIVE canonical company name.

    Applied AFTER duplicate-active rows were moved to IMPORT_REVIEW.
    """
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_candidate
        ON email_candidates(executive_id) WHERE is_active = 1
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_active_norm
        ON companies(normalized_name) WHERE current_status = 'ACTIVE'
        """
    )


MIGRATIONS: List[Tuple[int, str, MigrationFn]] = [
    (1, "Base operational tables including relationship model", migration_001_base_tables),
    (2, "Append-only triggers on history tables", migration_002_append_only_triggers),
    (3, "v1.1 columns, backfill, duplicate-active → IMPORT_REVIEW", migration_003_v11_columns_and_review),
    (4, "Verification source identity columns", migration_004_verification_source_columns),
    (5, "Partial unique indexes (one active candidate; one active company name)", migration_005_partial_unique_indexes),
]


def run_migrations(conn) -> List[int]:
    backend = getattr(conn, "backend", "sqlite")
    _ensure_schema_version_table(conn, backend)
    applied = _applied_versions(conn)
    newly: List[int] = []
    for version, description, fn in MIGRATIONS:
        if version in applied:
            continue
        fn(conn, backend)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
        newly.append(version)
        logger.info("Applied migration %s: %s", version, description)
    return newly
