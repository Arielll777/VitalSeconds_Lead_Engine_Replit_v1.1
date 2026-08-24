"""Batch ID sequential suggestion."""

from vitalseconds.services.batch import BatchService


def test_suggest_sequential(db):
    svc = BatchService(db)
    first = svc.suggest_next_id()
    assert first.startswith("Batch_")
    svc.create("Batch_10", "RAW_LEADS")
    svc.create("Batch_11", "RAW_LEADS")
    assert svc.suggest_next_id() == "Batch_12"


def test_unique_enforced(db):
    svc = BatchService(db)
    svc.create("Batch_1", "MASTER_IMPORT")
    try:
        svc.create("Batch_1", "RAW_LEADS")
        assert False, "Should have raised"
    except ValueError:
        pass


def test_grisha_export_id_format(db):
    gid = BatchService(db).grisha_export_id()
    assert gid.startswith("Grisha_Export_")
    assert len(gid) > len("Grisha_Export_")
