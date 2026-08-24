"""Generate at most three automatic email candidates per executive.

attempt_order 1..3 is the operational Pass number (what we actually verify).
candidate_basis / pass_order (pattern rank) are independent.

PUBLIC_EXACT is Attempt 1 when present.
If PUBLIC_EXACT equals flast, that email is Attempt 1 only (no duplicate),
then first and first.last fill Attempts 2 and 3.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from vitalseconds.config import MAX_AUTOMATIC_ATTEMPTS
from vitalseconds.utils.normalize import (
    build_first,
    build_first_last,
    build_flast,
    normalize_domain,
    normalize_email,
)


class CandidateGenerator:
    def __init__(self, conn):
        self.conn = conn

    def generate_for_executive(
        self,
        executive_id: int,
        first_name: str,
        last_name: str,
        domain: str,
        public_email: Optional[str] = None,
        domain_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        domain = normalize_domain(domain)
        if not domain:
            return []

        queued: List[Tuple[str, str, int]] = []  # email, basis, pattern_rank
        seen: set[str] = set()

        if public_email:
            pub = normalize_email(public_email)
            if pub and pub.endswith("@" + domain) and pub not in seen:
                queued.append((pub, "PUBLIC_EXACT", 0))
                seen.add(pub)

        for local, basis, rank in (
            (build_flast(first_name, last_name), "UNVERIFIED_FLAST_PATTERN", 1),
            (build_first(first_name), "UNVERIFIED_FIRST_PATTERN", 2),
            (build_first_last(first_name, last_name), "UNVERIFIED_FIRST_LAST_PATTERN", 3),
        ):
            if not local:
                continue
            email = normalize_email(f"{local}@{domain}")
            if email in seen:
                continue
            queued.append((email, basis, rank))
            seen.add(email)

        result: List[Dict[str, Any]] = []
        for attempt, (email, basis, rank) in enumerate(queued[:MAX_AUTOMATIC_ATTEMPTS], start=1):
            result.append(
                {
                    "executive_id": executive_id,
                    "email": email,
                    "normalized_email": email,
                    "domain_id": domain_id,
                    "candidate_basis": basis,
                    "pass_order": rank,
                    "attempt_order": attempt,
                    "is_active": 0,
                    "status": "PENDING",
                }
            )
        return result

    def persist_candidates(
        self, candidates: List[Dict[str, Any]], activate_first: bool = True
    ) -> Optional[int]:
        if not candidates:
            return None
        active_id: Optional[int] = None
        for c in candidates:
            existing = self.conn.execute(
                "SELECT candidate_id FROM email_candidates WHERE normalized_email = ?",
                (c["normalized_email"],),
            ).fetchone()
            if existing:
                continue
            cur = self.conn.execute(
                """
                INSERT INTO email_candidates (
                    executive_id, email, normalized_email, domain_id,
                    candidate_basis, pass_order, attempt_order, is_active, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    c["executive_id"],
                    c["email"],
                    c["normalized_email"],
                    c.get("domain_id"),
                    c["candidate_basis"],
                    c.get("pass_order", 0),
                    c.get("attempt_order", 0),
                    c.get("status", "PENDING"),
                ),
            )
            cid = cur.lastrowid
            if activate_first and active_id is None:
                self.conn.execute(
                    "UPDATE email_candidates SET is_active = 0 WHERE executive_id = ? AND is_active = 1",
                    (c["executive_id"],),
                )
                self.conn.execute(
                    "UPDATE email_candidates SET is_active = 1, status = 'ACTIVE_VERIFICATION' WHERE candidate_id = ?",
                    (cid,),
                )
                self.conn.execute(
                    """
                    UPDATE executives
                    SET active_candidate_id = ?, updated_at = datetime('now')
                    WHERE executive_id = ?
                    """,
                    (cid, c["executive_id"]),
                )
                active_id = cid
        return active_id

    def count_attempts(self, executive_id: int) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(DISTINCT normalized_email) AS n
            FROM verification_events
            WHERE executive_id = ?
            """,
            (executive_id,),
        ).fetchone()
        return int(row[0]) if row else 0
