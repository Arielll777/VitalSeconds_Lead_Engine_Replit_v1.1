"""Deduplication precedence tests, including buying-group independence and merge follow."""

from vitalseconds.services.deduplicator import Deduplicator
from vitalseconds.services.importer import MasterImporter
from vitalseconds.services.override import OverrideService
from tests.helpers import seed_company_domain_exec


def test_net_new(db):
    deduper = Deduplicator(db)
    result = deduper.classify_row(
        {
            "first_name": "Jane",
            "last_name": "Doe",
            "company": "Fresh Ambulance",
            "domain": "freshamb.com",
        }
    )
    assert result["primary_classification"] == "NET_NEW"


def test_data_error(db):
    deduper = Deduplicator(db)
    result = deduper.classify_row({"first_name": "Jane", "company": "X"})
    assert result["primary_classification"] == "DATA_ERROR"


def test_company_and_executive_duplicate(db):
    importer = MasterImporter(db)
    importer.import_rows(
        [
            {
                "first_name": "John",
                "last_name": "Smith",
                "company": "Life Star EMS",
                "domain": "lifestarems.com",
            }
        ],
        "Batch_M1",
    )
    db.commit()

    deduper = Deduplicator(db)
    result = deduper.classify_row(
        {
            "first_name": "John",
            "last_name": "Smith",
            "company": "Life Star EMS",
            "domain": "lifestarems.com",
        }
    )
    assert "COMPANY_DUPLICATE" in result["all_matched_reasons"]
    assert "EXECUTIVE_DUPLICATE" in result["all_matched_reasons"]
    assert result["primary_classification"] in (
        "EXECUTIVE_DUPLICATE",
        "COMPANY_DUPLICATE",
        "EXACT_DUPLICATE",
    )


def test_buying_group_duplicate_independent_of_company_name(db):
    """Incoming company name is new, but explicit buying_group matches stored group → BUYING_GROUP_DUPLICATE."""
    seed_company_domain_exec(db, company="Alpha EMS", domain="alphaems.com", buying_group="GROUPX")
    deduper = Deduplicator(db)
    result = deduper.classify_row(
        {
            "first_name": "Pat",
            "last_name": "Lee",
            "company": "Other EMS",
            "domain": "otherems.com",
            "buying_group": "GROUPX",
        }
    )
    assert "BUYING_GROUP_DUPLICATE" in result["all_matched_reasons"]
    assert result["primary_classification"] == "BUYING_GROUP_DUPLICATE"


def test_buying_group_never_inferred(db):
    seed_company_domain_exec(db, company="Alpha EMS", domain="alphaems.com", buying_group="GROUPX")
    deduper = Deduplicator(db)
    result = deduper.classify_row(
        {
            "first_name": "Pat",
            "last_name": "Lee",
            "company": "Other EMS",
            "domain": "otherems.com",
        }
    )
    assert "BUYING_GROUP_DUPLICATE" not in result["all_matched_reasons"]
    assert result["primary_classification"] == "NET_NEW"


def test_merge_alias_resolves_to_canonical(db):
    db.execute("INSERT INTO companies (canonical_name, normalized_name) VALUES ('Old EMS', 'old ems')")
    src = db.execute("SELECT company_id FROM companies WHERE normalized_name = 'old ems'").fetchone()["company_id"]
    db.execute("INSERT INTO companies (canonical_name, normalized_name) VALUES ('New EMS', 'new ems')")
    tgt = db.execute("SELECT company_id FROM companies WHERE normalized_name = 'new ems'").fetchone()["company_id"]
    db.commit()
    res = OverrideService(db).merge_company_aliases(src, tgt, "canonical rename")
    assert res["ok"] is True
    src_row = db.execute(
        "SELECT current_status, merged_into_company_id, normalized_name FROM companies WHERE company_id = ?",
        (src,),
    ).fetchone()
    assert src_row["current_status"] == "MERGED"
    assert src_row["merged_into_company_id"] == tgt
    assert src_row["normalized_name"] == "old ems"  # history preserved, not deleted

    deduper = Deduplicator(db)
    found = deduper._find_canonical_company_id("old ems")
    assert found == tgt
    # searching Old EMS must not return the inactive source as canonical
    assert found != src
