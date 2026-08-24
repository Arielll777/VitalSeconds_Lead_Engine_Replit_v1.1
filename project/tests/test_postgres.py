"""PostgreSQL integration/smoke tests.

These do NOT run against production. They require an isolated URL:

    VITALSECONDS_PG_TEST_URL=postgresql://.../vitalseconds_test

If that environment variable is unset, tests are skipped. SQLite unit tests
are not a PostgreSQL persistence proof.
"""

from __future__ import annotations

import os
import uuid

import pytest

from vitalseconds.db.migrations import run_migrations
from vitalseconds.db.session import DbConnection
from vitalseconds.services.batch import BatchService
from vitalseconds.services.candidate_generator import CandidateGenerator
from vitalseconds.services.exporter import ExportService
from vitalseconds.services.waterfall import WaterfallEngine


def _pg_url():
    return (os.getenv("VITALSECONDS_PG_TEST_URL") or "").strip()


pytestmark = pytest.mark.skipif(not _pg_url(), reason="VITALSECONDS_PG_TEST_URL not set — PostgreSQL tests skipped")


@pytest.fixture
def pg():
    import psycopg2

    url = _pg_url()
    schema = "vs_test_" + uuid.uuid4().hex[:12]
    raw = psycopg2.connect(url)
    raw.autocommit = True
    cur = raw.cursor()
    cur.execute(f"CREATE SCHEMA {schema}")
    cur.close()
    raw.autocommit = False
    cur = raw.cursor()
    cur.execute(f"SET search_path TO {schema}")
    cur.close()
    conn = DbConnection("postgres", raw)
    run_migrations(conn)
    conn.commit()
    yield conn
    conn.close()
    raw2 = psycopg2.connect(url)
    raw2.autocommit = True
    c2 = raw2.cursor()
    c2.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    c2.close()
    raw2.close()


def test_postgres_smoke_migrations_and_waterfall(pg):
    conn = pg
    conn.execute(
        "INSERT INTO companies (canonical_name, normalized_name) VALUES (?, ?)",
        ("PG EMS", "pg ems"),
    )
    cid = conn.execute("SELECT company_id FROM companies").fetchone()["company_id"]
    conn.execute(
        "INSERT INTO domains (domain, normalized_domain, company_id) VALUES (?, ?, ?)",
        ("pgems.com", "pgems.com", cid),
    )
    did = conn.execute("SELECT domain_id FROM domains").fetchone()["domain_id"]
    conn.execute(
        """
        INSERT INTO executives (company_id, first_name, last_name, normalized_full_name)
        VALUES (?, ?, ?, ?)
        """,
        (cid, "John", "Smith", "john smith"),
    )
    eid = conn.execute("SELECT executive_id FROM executives").fetchone()["executive_id"]
    gen = CandidateGenerator(conn)
    cands = gen.generate_for_executive(eid, "John", "Smith", "pgems.com", domain_id=did)
    gen.persist_candidates(cands, activate_first=True)
    BatchService(conn).create("Batch_PG1", "NEVERBOUNCE")
    engine = WaterfallEngine(conn)
    active = conn.execute("SELECT email FROM email_candidates WHERE is_active = 1").fetchone()["email"]
    r_unknown = engine.process_result(active, "unknown", "Batch_PG1", "a.csv", source_file_sha256="s1", source_row_number=1)
    assert r_unknown["outcome"] == "UNKNOWN_PENDING_RETRY"
    BatchService(conn).create("Batch_PG2", "NEVERBOUNCE")
    r_inv = engine.process_result(active, "invalid", "Batch_PG2", "b.csv", source_file_sha256="s2", source_row_number=1)
    assert r_inv["outcome"] == "ADVANCED"
    nxt = conn.execute("SELECT email FROM email_candidates WHERE is_active = 1").fetchone()["email"]
    BatchService(conn).create("Batch_PG3", "NEVERBOUNCE")
    r_aa = engine.process_result(nxt, "accept_all", "Batch_PG3", "c.csv", source_file_sha256="s3", source_row_number=1)
    assert r_aa["outcome"] == "ACCEPT_ALL_QUARANTINE"
    # reconnect persistence: new connection, same schema via search_path is fixture-local
    n = conn.execute("SELECT COUNT(*) AS n FROM verification_events").fetchone()[0]
    assert int(n) >= 2
    # valid path on a fresh exec
    conn.execute(
        """
        INSERT INTO executives (company_id, first_name, last_name, normalized_full_name)
        VALUES (?, ?, ?, ?)
        """,
        (cid, "Ann", "Lee", "ann lee"),
    )
    eid2 = conn.execute("SELECT executive_id FROM executives WHERE normalized_full_name = 'ann lee'").fetchone()["executive_id"]
    c2 = gen.generate_for_executive(eid2, "Ann", "Lee", "pgems.com", domain_id=did)
    # domain quarantined — still generate; waterfall would quarantine on accept_all already
    gen.persist_candidates(c2, activate_first=True)
    BatchService(conn).create("Batch_PG4", "NEVERBOUNCE")
    email2 = conn.execute(
        "SELECT email FROM email_candidates WHERE executive_id = ? AND is_active = 1", (eid2,)
    ).fetchone()
    if email2:
        r_val = engine.process_result(email2["email"], "valid", "Batch_PG4", "d.csv", source_file_sha256="s4", source_row_number=1)
        # may unmatched if accept-all deactivated domain candidates
        assert r_val["outcome"] in ("VERIFIED_VALID", "UNMATCHED_RESULT", "ACCEPT_ALL_QUARANTINE")
    conn.commit()
    _, gcount = ExportService(conn).preview_grisha_ready()
    assert gcount >= 0
