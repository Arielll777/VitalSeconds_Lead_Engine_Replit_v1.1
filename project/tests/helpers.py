"""Shared test helpers."""

from __future__ import annotations

import sqlite3

from vitalseconds.db.session import DbConnection
from vitalseconds.utils.normalize import normalize_company, normalize_name


def open_sqlite(path) -> DbConnection:
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return DbConnection("sqlite", raw)


def seed_company_domain_exec(
    conn,
    company="Test EMS",
    domain="testems.com",
    first="John",
    last="Smith",
    buying_group=None,
):
    conn.execute(
        "INSERT INTO companies (canonical_name, normalized_name, buying_group) VALUES (?, ?, ?)",
        (company, normalize_company(company), buying_group),
    )
    cid = conn.execute(
        "SELECT company_id FROM companies WHERE canonical_name = ?", (company,)
    ).fetchone()["company_id"]
    if buying_group:
        existing = conn.execute(
            "SELECT buying_group_id FROM buying_groups WHERE normalized_name = ?",
            (buying_group.lower(),),
        ).fetchone()
        if existing:
            bgid = existing["buying_group_id"]
        else:
            conn.execute(
                "INSERT INTO buying_groups (name, normalized_name) VALUES (?, ?)",
                (buying_group, buying_group.lower()),
            )
            bgid = conn.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO company_buying_groups (company_id, buying_group_id, source) VALUES (?, ?, 'TEST')",
            (cid, bgid),
        )
    conn.execute(
        "INSERT INTO domains (domain, normalized_domain, company_id) VALUES (?, ?, ?)",
        (domain, domain, cid),
    )
    did = conn.execute(
        "SELECT domain_id FROM domains WHERE normalized_domain = ?", (domain,)
    ).fetchone()["domain_id"]
    conn.execute(
        """
        INSERT INTO executives (company_id, first_name, last_name, normalized_full_name)
        VALUES (?, ?, ?, ?)
        """,
        (cid, first, last, normalize_name(f"{first} {last}")),
    )
    eid = conn.execute(
        "SELECT executive_id FROM executives WHERE company_id = ? AND normalized_full_name = ?",
        (cid, normalize_name(f"{first} {last}")),
    ).fetchone()["executive_id"]
    conn.execute(
        "INSERT OR IGNORE INTO executive_company_roles (executive_id, company_id, is_current, source) VALUES (?, ?, 1, 'TEST')",
        (eid, cid),
    )
    conn.commit()
    return cid, did, eid


def seed_exec_with_candidates(db, first="John", last="Smith", domain="testems.com", public=None):
    from vitalseconds.services.candidate_generator import CandidateGenerator

    cid, did, eid = seed_company_domain_exec(db, domain=domain, first=first, last=last)
    gen = CandidateGenerator(db)
    cands = gen.generate_for_executive(eid, first, last, domain, public_email=public, domain_id=did)
    gen.persist_candidates(cands, activate_first=True)
    db.commit()
    return eid, did
