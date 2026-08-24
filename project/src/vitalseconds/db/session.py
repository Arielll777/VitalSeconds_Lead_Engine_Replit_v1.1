"""Database session. Production requires DATABASE_URL. SQLite only with explicit test flag."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Generator, Optional, Sequence, Union

from vitalseconds.config import (
    ALLOW_SQLITE_TEST,
    DATABASE_URL,
    USING_POSTGRES,
    _SQLITE_FALLBACK,
)
from vitalseconds.db.schema import POSTGRES_SCHEMA_SQL, SQLITE_SCHEMA_SQL

_Param = Union[Sequence[Any], None]


class DatabaseConfigError(RuntimeError):
    """Raised when production database configuration is missing."""


def _to_pg_sql(sql: str) -> str:
    out = sql.replace("datetime('now')", "NOW()")
    out = out.replace("excluded.", "EXCLUDED.")
    out = re.sub(r"ON CONFLICT\s*\(", "ON CONFLICT (", out, flags=re.IGNORECASE)
    if "INSERT OR IGNORE INTO" in out.upper() or "insert or ignore into" in out:
        out = re.sub(r"INSERT OR IGNORE INTO", "INSERT INTO", out, flags=re.IGNORECASE)
        if "ON CONFLICT" not in out.upper():
            out = out.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    out = re.sub(r"\?", "%s", out)
    return out


class _CursorResult:
    def __init__(self, rows: list, lastrowid: Optional[int] = None, description=None):
        self._rows = rows
        self._idx = 0
        self.lastrowid = lastrowid
        self.description = description

    def fetchone(self):
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        return row

    def fetchall(self):
        rows = self._rows[self._idx :]
        self._idx = len(self._rows)
        return rows

    def __iter__(self):
        return iter(self._rows)


class _Row:
    def __init__(self, data: dict):
        self._data = data
        self._keys = list(data.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._data[self._keys[key]]
        return self._data[key]

    def keys(self):
        return self._keys

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)


class DbConnection:
    def __init__(self, backend: str, raw_conn: Any):
        self.backend = backend
        self._raw = raw_conn
        self._lastrowid: Optional[int] = None

    @property
    def lastrowid(self) -> Optional[int]:
        return self._lastrowid

    def execute(self, sql: str, params: _Param = None) -> _CursorResult:
        params = tuple(params) if params is not None else ()
        if self.backend == "postgres":
            return self._exec_pg(sql, params)
        return self._exec_sqlite(sql, params)

    def _exec_sqlite(self, sql: str, params: tuple) -> _CursorResult:
        cur = self._raw.execute(sql, params)
        self._lastrowid = cur.lastrowid
        rows = cur.fetchall()
        return _CursorResult(list(rows), lastrowid=self._lastrowid, description=cur.description)

    def _exec_pg(self, sql: str, params: tuple) -> _CursorResult:
        import psycopg2.extras

        sql_pg = _to_pg_sql(sql)
        is_insert = sql_pg.lstrip().upper().startswith("INSERT")
        returning_id = None
        if is_insert and "RETURNING" not in sql_pg.upper():
            m = re.search(r"INSERT\s+INTO\s+(\w+)", sql_pg, re.IGNORECASE)
            table = m.group(1).lower() if m else ""
            pk_map = {
                "companies": "company_id",
                "company_aliases": "alias_id",
                "buying_groups": "buying_group_id",
                "domains": "domain_id",
                "company_domain_relationships": "rel_id",
                "executives": "executive_id",
                "executive_company_roles": "role_id",
                "email_candidates": "candidate_id",
                "column_mappings": "mapping_id",
                "import_files": "file_id",
                "import_source_rows": "row_id",
                "verification_events": "event_id",
                "audit_log": "audit_id",
                "manual_overrides": "override_id",
                "grisha_exports": "export_id",
                "grisha_export_events": "event_id",
            }
            pk = pk_map.get(table)
            if pk:
                if "ON CONFLICT DO NOTHING" in sql_pg.upper():
                    sql_pg = sql_pg.rstrip().rstrip(";")
                    if "RETURNING" not in sql_pg.upper():
                        sql_pg = sql_pg + f" RETURNING {pk}"
                else:
                    sql_pg = sql_pg.rstrip().rstrip(";") + f" RETURNING {pk}"
                returning_id = pk

        cur = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql_pg, params)
        self._lastrowid = None
        rows: list = []
        if cur.description:
            raw_rows = cur.fetchall()
            rows = [_Row(dict(r)) for r in raw_rows]
            if returning_id and rows:
                self._lastrowid = rows[0][returning_id]
        cur.close()
        return _CursorResult(rows, lastrowid=self._lastrowid)

    def executescript(self, script: str) -> None:
        if self.backend == "sqlite":
            self._raw.executescript(script)
            return
        cur = self._raw.cursor()
        for stmt in _split_sql(script):
            if stmt.strip():
                cur.execute(stmt)
        cur.close()

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


def _split_sql(script: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
        if ";" in line:
            chunk = "\n".join(buf)
            for piece in chunk.split(";"):
                if piece.strip():
                    parts.append(piece.strip())
            buf = []
    if buf and "".join(buf).strip():
        parts.append("\n".join(buf).strip())
    return parts


def get_connection(url: Optional[str] = None) -> DbConnection:
    """Live operational connection. Production MUST have DATABASE_URL."""
    target = (url or DATABASE_URL or "").strip()
    if target:
        import psycopg2

        raw = psycopg2.connect(target)
        return DbConnection("postgres", raw)

    if not ALLOW_SQLITE_TEST:
        raise DatabaseConfigError(
            "DATABASE CONFIGURATION ERROR\nDATABASE_URL is required."
        )

    path = _SQLITE_FALLBACK
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(path), check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return DbConnection("sqlite", raw)


def init_db(url: Optional[str] = None) -> None:
    """Safe migrations. Never drops existing operational data."""
    conn = get_connection(url)
    try:
        from vitalseconds.db.migrations import run_migrations

        run_migrations(conn)
        conn.commit()
    finally:
        conn.close()


def check_db_health() -> dict:
    try:
        if not DATABASE_URL and not ALLOW_SQLITE_TEST:
            return {
                "ok": False,
                "backend": None,
                "message": "DATABASE ERROR",
                "error": "DATABASE CONFIGURATION ERROR: DATABASE_URL is required.",
                "using_postgres": False,
            }
        conn = get_connection()
        try:
            row = conn.execute("SELECT 1 AS ok").fetchone()
            ok = row is not None
            return {
                "ok": bool(ok),
                "backend": conn.backend,
                "message": "DATABASE CONNECTED" if ok else "DATABASE ERROR",
                "using_postgres": conn.backend == "postgres",
            }
        finally:
            conn.close()
    except DatabaseConfigError as exc:
        return {
            "ok": False,
            "backend": None,
            "message": "DATABASE ERROR",
            "error": str(exc),
            "using_postgres": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "backend": "postgres" if USING_POSTGRES else "sqlite",
            "message": "DATABASE ERROR",
            "error": str(exc),
            "using_postgres": USING_POSTGRES,
        }


def scalar(conn: DbConnection, sql: str, params: _Param = None):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def query_df(conn: DbConnection, sql: str, params: _Param = None):
    import pandas as pd

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return pd.DataFrame()
    keys = list(rows[0].keys())
    return pd.DataFrame([{k: r[k] for k in keys} for r in rows])


@contextmanager
def transaction(url: Optional[str] = None) -> Generator[DbConnection, None, None]:
    conn = get_connection(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
