"""Pytest fixtures. SQLite only when VITALSECONDS_ALLOW_SQLITE_TEST=1. No silent DATABASE_URL."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ["VITALSECONDS_ALLOW_SQLITE_TEST"] = "1"
os.environ.pop("DATABASE_URL", None)

from vitalseconds.db.migrations import run_migrations  # noqa: E402
from vitalseconds.db.session import DbConnection  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("VITALSECONDS_ALLOW_SQLITE_TEST", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "test.db"
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    conn = DbConnection("sqlite", raw)
    run_migrations(conn)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """On-disk SQLite path for persistence reopen tests."""
    monkeypatch.setenv("VITALSECONDS_ALLOW_SQLITE_TEST", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "persist.db"
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    conn = DbConnection("sqlite", raw)
    run_migrations(conn)
    conn.commit()
    conn.close()
    return path
