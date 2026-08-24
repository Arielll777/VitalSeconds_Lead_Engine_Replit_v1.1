"""Waterfall state machine tests."""

from vitalseconds.services.batch import BatchService
from vitalseconds.services.waterfall import WaterfallEngine
from tests.helpers import seed_exec_with_candidates


def _seed_exec_with_candidates(db, first="John", last="Smith", domain="testems.com", public=None):
    return seed_exec_with_candidates(db, first=first, last=last, domain=domain, public=public)


def test_valid_stops(db):
    BatchService(db).create("Batch_1", "NEVERBOUNCE")
    eid, _ = _seed_exec_with_candidates(db)
    engine = WaterfallEngine(db)

    active = db.execute(
        "SELECT email FROM email_candidates WHERE is_active = 1"
    ).fetchone()["email"]
    result = engine.process_result(active, "valid", "Batch_1", "nb.csv")
    assert result["outcome"] == "VERIFIED_VALID"
    assert result["grisha_ready"] is True

    exec_row = db.execute(
        "SELECT technical_status, campaign_disposition, active_candidate_id FROM executives WHERE executive_id = ?",
        (eid,),
    ).fetchone()
    assert exec_row["technical_status"] == "VERIFIED_VALID"
    assert exec_row["campaign_disposition"] == "ACTIVE"
    assert exec_row["active_candidate_id"] is None


def test_invalid_advances_by_attempt_order(db):
    BatchService(db).create("Batch_2", "NEVERBOUNCE")
    eid, _ = _seed_exec_with_candidates(db)
    engine = WaterfallEngine(db)

    active = db.execute(
        "SELECT candidate_id, email, attempt_order FROM email_candidates WHERE is_active = 1"
    ).fetchone()
    assert active["attempt_order"] == 1  # flast without public exact

    result = engine.process_result(active["email"], "invalid", "Batch_2", "nb.csv")
    assert result["outcome"] == "ADVANCED"
    assert result["next_pass"] == 2

    new_active = db.execute(
        "SELECT attempt_order, email FROM email_candidates WHERE is_active = 1"
    ).fetchone()
    assert new_active["attempt_order"] == 2
    assert new_active["email"].startswith("john@")


def test_unknown_does_not_advance(db):
    BatchService(db).create("Batch_3", "NEVERBOUNCE")
    eid, _ = _seed_exec_with_candidates(db)
    engine = WaterfallEngine(db)

    active = db.execute(
        "SELECT email, attempt_order FROM email_candidates WHERE is_active = 1"
    ).fetchone()
    result = engine.process_result(active["email"], "unknown", "Batch_3", "nb.csv")
    assert result["outcome"] == "UNKNOWN_PENDING_RETRY"
    assert result["retry_same"] is True

    still = db.execute(
        "SELECT email, attempt_order, is_active FROM email_candidates WHERE is_active = 1"
    ).fetchone()
    assert still["email"] == active["email"]
    assert still["attempt_order"] == active["attempt_order"]

    exec_row = db.execute(
        "SELECT technical_status FROM executives WHERE executive_id = ?", (eid,)
    ).fetchone()
    assert exec_row["technical_status"] == "UNKNOWN_PENDING_RETRY"


def test_accept_all_quarantines_exact_domain_only(db):
    BatchService(db).create("Batch_4", "NEVERBOUNCE")
    eid, did = _seed_exec_with_candidates(db)
    # second historical domain on same company must NOT be quarantined
    db.execute(
        "INSERT INTO domains (domain, normalized_domain, company_id, is_historical) VALUES ('oldexample.com', 'oldexample.com', 1, 1)"
    )
    db.commit()
    engine = WaterfallEngine(db)

    active = db.execute(
        "SELECT email FROM email_candidates WHERE is_active = 1"
    ).fetchone()["email"]
    result = engine.process_result(active, "accept_all", "Batch_4", "nb.csv")
    assert result["outcome"] == "ACCEPT_ALL_QUARANTINE"

    dom = db.execute(
        "SELECT technical_status FROM domains WHERE domain_id = ?", (did,)
    ).fetchone()
    assert dom["technical_status"] == "ACCEPT_ALL_QUARANTINE"

    other = db.execute(
        "SELECT technical_status FROM domains WHERE normalized_domain = 'oldexample.com'"
    ).fetchone()
    assert other["technical_status"] != "ACCEPT_ALL_QUARANTINE"

    exec_row = db.execute(
        "SELECT technical_status, campaign_disposition FROM executives WHERE executive_id = ?", (eid,)
    ).fetchone()
    assert exec_row["technical_status"] == "DOMAIN_QUARANTINED"
    assert exec_row["campaign_disposition"] == "DOMAIN_QUARANTINED"


def test_idempotency_same_file_row(db):
    BatchService(db).create("Batch_5", "NEVERBOUNCE")
    _seed_exec_with_candidates(db)
    engine = WaterfallEngine(db)
    active = db.execute(
        "SELECT email FROM email_candidates WHERE is_active = 1"
    ).fetchone()["email"]

    r1 = engine.process_result(
        active, "invalid", "Batch_5", "nb.csv", source_file_sha256="aaa", source_row_number=1
    )
    assert r1["outcome"] in ("ADVANCED", "EXHAUSTED_INVALID")
    r2 = engine.process_result(
        active, "invalid", "Batch_5", "nb.csv", source_file_sha256="aaa", source_row_number=1
    )
    assert r2["outcome"] == "ALREADY_PROCESSED"


def test_later_reverify_different_file_is_new_event(db):
    BatchService(db).create("Batch_6", "NEVERBOUNCE")
    BatchService(db).create("Batch_7", "NEVERBOUNCE")
    eid, _ = _seed_exec_with_candidates(db)
    engine = WaterfallEngine(db)
    active = db.execute(
        "SELECT email FROM email_candidates WHERE is_active = 1"
    ).fetchone()["email"]
    r1 = engine.process_result(
        active, "unknown", "Batch_6", "file-a.csv", source_file_sha256="sha-a", source_row_number=1
    )
    assert r1["outcome"] == "UNKNOWN_PENDING_RETRY"
    r2 = engine.process_result(
        active, "unknown", "Batch_7", "file-b.csv", source_file_sha256="sha-b", source_row_number=1
    )
    assert r2["outcome"] != "ALREADY_PROCESSED"
    n = db.execute(
        "SELECT COUNT(*) AS n FROM verification_events WHERE executive_id = ?", (eid,)
    ).fetchone()[0]
    assert n == 2
