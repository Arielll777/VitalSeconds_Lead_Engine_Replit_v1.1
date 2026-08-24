"""Candidate generation — attempt_order is Pass number; pattern rank is pass_order."""

from vitalseconds.services.candidate_generator import CandidateGenerator
from tests.helpers import seed_company_domain_exec


def test_pass_order_and_public(db):
    cid, did, exec_id = seed_company_domain_exec(db)
    gen = CandidateGenerator(db)
    cands = gen.generate_for_executive(
        exec_id,
        "John",
        "Smith",
        "testems.com",
        public_email="john.smith@testems.com",
        domain_id=did,
    )
    assert len(cands) == 3  # public + flast + first  (first.last coincides with public → deduped)
    bases = [c["candidate_basis"] for c in cands]
    assert bases[0] == "PUBLIC_EXACT"
    assert "UNVERIFIED_FLAST_PATTERN" in bases
    assert "UNVERIFIED_FIRST_PATTERN" in bases
    assert [c["attempt_order"] for c in cands] == [1, 2, 3]

    flast = next(c for c in cands if c["candidate_basis"] == "UNVERIFIED_FLAST_PATTERN")
    assert flast["email"] == "jsmith@testems.com"

    active = gen.persist_candidates(cands, activate_first=True)
    assert active is not None
    row = db.execute(
        "SELECT is_active, status, attempt_order FROM email_candidates WHERE candidate_id = ?", (active,)
    ).fetchone()
    assert row["is_active"] == 1
    assert row["status"] == "ACTIVE_VERIFICATION"
    assert row["attempt_order"] == 1


def test_no_dot_in_pass1(db):
    gen = CandidateGenerator(db)
    cands = gen.generate_for_executive(1, "Alice", "Johnson", "example.com")
    first_attempt = next(c for c in cands if c["attempt_order"] == 1)
    assert first_attempt["email"] == "ajohnson@example.com"
    assert "." not in first_attempt["email"].split("@")[0]


def test_public_exact_is_attempt_1_not_pass_zero_export(db):
    """PUBLIC_EXACT ceo@ is Attempt 1. Generated jsmith is Attempt 2, john is Attempt 3."""
    cid, did, eid = seed_company_domain_exec(db)
    gen = CandidateGenerator(db)
    cands = gen.generate_for_executive(
        eid, "John", "Smith", "testems.com", public_email="ceo@testems.com", domain_id=did
    )
    assert cands[0]["candidate_basis"] == "PUBLIC_EXACT"
    assert cands[0]["email"] == "ceo@testems.com"
    assert cands[0]["attempt_order"] == 1
    assert cands[0]["pass_order"] == 0  # pattern rank remains 0
    assert cands[1]["email"] == "jsmith@testems.com"
    assert cands[1]["attempt_order"] == 2
    assert cands[2]["email"] == "john@testems.com"
    assert cands[2]["attempt_order"] == 3
    assert len(cands) == 3


def test_public_exact_equals_flast_no_duplicate(db):
    """If PUBLIC_EXACT equals flast, that email is Attempt 1 only."""
    cid, did, eid = seed_company_domain_exec(db)
    gen = CandidateGenerator(db)
    cands = gen.generate_for_executive(
        eid, "John", "Smith", "testems.com", public_email="jsmith@testems.com", domain_id=did
    )
    emails = [c["email"] for c in cands]
    assert emails == ["jsmith@testems.com", "john@testems.com", "john.smith@testems.com"]
    assert cands[0]["candidate_basis"] == "PUBLIC_EXACT"
    assert cands[0]["attempt_order"] == 1
    assert cands[1]["attempt_order"] == 2
    assert cands[2]["attempt_order"] == 3
    assert len(set(emails)) == 3
