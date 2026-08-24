"""Strict email verification waterfall.

One active candidate per executive.
valid → VERIFIED_VALID (technical) — campaign unchanged.
invalid → next attempt_order (max 3).
unknown → stay; never advance.
accept_all → quarantine EXACT domain only.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from vitalseconds.config import MAX_AUTOMATIC_ATTEMPTS
from vitalseconds.utils.fingerprint import make_verification_fingerprint
from vitalseconds.utils.normalize import normalize_email


class WaterfallEngine:
    def __init__(self, conn):
        self.conn = conn

    def process_result(
        self,
        email: str,
        neverbounce_status: str,
        batch_id: str,
        source_identifier: str,
        raw_result: Optional[Dict[str, Any]] = None,
        *,
        source_file_sha256: Optional[str] = None,
        source_row_number: int = 0,
        source_type: str = "NEVERBOUNCE",
        verifier: str = "NeverBounce",
    ) -> Dict[str, Any]:
        norm_email = normalize_email(email)
        status = (neverbounce_status or "").strip().lower()
        if status in ("accept_all", "accept-all", "catchall", "catch_all", "catch-all"):
            status = "accept_all"

        file_sha = source_file_sha256 or source_identifier
        fingerprint = make_verification_fingerprint(
            norm_email,
            status,
            batch_id,
            source_identifier,
            source_file_sha256=file_sha,
            source_row_number=source_row_number,
            source_type=source_type,
            verifier=verifier,
        )

        existing = self.conn.execute(
            "SELECT event_id FROM verification_events WHERE idempotency_fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if existing:
            return {
                "outcome": "ALREADY_PROCESSED",
                "email": norm_email,
                "event_id": existing["event_id"],
            }

        cand = self.conn.execute(
            """
            SELECT c.*, e.executive_id, e.technical_status AS exec_status
            FROM email_candidates c
            JOIN executives e ON e.executive_id = c.executive_id
            WHERE c.normalized_email = ? AND c.is_active = 1
            """,
            (norm_email,),
        ).fetchone()
        if not cand:
            return {
                "outcome": "UNMATCHED_RESULT",
                "email": norm_email,
                "neverbounce_status": status,
            }

        candidate_id = cand["candidate_id"]
        executive_id = cand["executive_id"]
        domain_id = cand["domain_id"]

        self.conn.execute(
            """
            INSERT INTO verification_events (
                candidate_id, executive_id, email, normalized_email,
                neverbounce_status, raw_result_json, batch_id, idempotency_fingerprint,
                source_file_sha256, source_row_number, source_type, verifier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                executive_id,
                email,
                norm_email,
                status,
                json.dumps(raw_result) if raw_result else None,
                batch_id,
                fingerprint,
                file_sha,
                source_row_number,
                source_type,
                verifier,
            ),
        )

        if status == "valid":
            return self._handle_valid(candidate_id, executive_id, norm_email)
        if status == "invalid":
            return self._handle_invalid(candidate_id, executive_id, domain_id, norm_email)
        if status == "unknown":
            return self._handle_unknown(candidate_id, executive_id, norm_email)
        if status == "accept_all":
            return self._handle_accept_all(candidate_id, executive_id, domain_id, norm_email)
        return self._handle_unknown(candidate_id, executive_id, norm_email)

    def _set_tech(self, executive_id: int, technical: str, classification: Optional[str] = None) -> None:
        self.conn.execute(
            """
            UPDATE executives
            SET technical_status = ?,
                current_status = ?,
                primary_classification = COALESCE(?, primary_classification),
                updated_at = datetime('now')
            WHERE executive_id = ?
            """,
            (technical, technical, classification or technical, executive_id),
        )

    def _handle_valid(self, candidate_id: int, executive_id: int, email: str) -> Dict[str, Any]:
        self.conn.execute(
            "UPDATE email_candidates SET is_active = 0, status = 'VERIFIED_VALID' WHERE candidate_id = ?",
            (candidate_id,),
        )
        self.conn.execute(
            """
            UPDATE executives
            SET technical_status = 'VERIFIED_VALID',
                current_status = 'VERIFIED_VALID',
                primary_classification = 'PREVIOUSLY_VALID',
                active_candidate_id = NULL,
                updated_at = datetime('now')
            WHERE executive_id = ?
            """,
            (executive_id,),
        )
        self._audit("EXECUTIVE", str(executive_id), "VERIFY_VALID", None, email, "valid result")
        camp = self.conn.execute(
            "SELECT campaign_disposition FROM executives WHERE executive_id = ?",
            (executive_id,),
        ).fetchone()["campaign_disposition"]
        return {
            "outcome": "VERIFIED_VALID",
            "email": email,
            "executive_id": executive_id,
            "grisha_ready": camp == "ACTIVE",
        }

    def _handle_invalid(
        self, candidate_id: int, executive_id: int, domain_id: Optional[int], email: str
    ) -> Dict[str, Any]:
        self.conn.execute(
            "UPDATE email_candidates SET is_active = 0, status = 'INVALID' WHERE candidate_id = ?",
            (candidate_id,),
        )
        attempt_count = self.conn.execute(
            """
            SELECT COUNT(DISTINCT normalized_email) AS n
            FROM verification_events
            WHERE executive_id = ?
            """,
            (executive_id,),
        ).fetchone()[0]

        next_cand = self.conn.execute(
            """
            SELECT candidate_id, email, attempt_order, pass_order
            FROM email_candidates
            WHERE executive_id = ?
              AND status = 'PENDING'
              AND attempt_order > (
                  SELECT attempt_order FROM email_candidates WHERE candidate_id = ?
              )
            ORDER BY attempt_order ASC
            LIMIT 1
            """,
            (executive_id, candidate_id),
        ).fetchone()

        if next_cand and attempt_count < MAX_AUTOMATIC_ATTEMPTS:
            self.conn.execute(
                "UPDATE email_candidates SET is_active = 1, status = 'ACTIVE_VERIFICATION' WHERE candidate_id = ?",
                (next_cand["candidate_id"],),
            )
            self.conn.execute(
                """
                UPDATE executives
                SET active_candidate_id = ?,
                    technical_status = 'NET_NEW',
                    current_status = 'NET_NEW',
                    updated_at = datetime('now')
                WHERE executive_id = ?
                """,
                (next_cand["candidate_id"], executive_id),
            )
            self._audit(
                "EXECUTIVE",
                str(executive_id),
                "ADVANCE_PASS",
                email,
                next_cand["email"],
                f"invalid → next attempt {next_cand['attempt_order']}",
            )
            return {
                "outcome": "ADVANCED",
                "email": email,
                "next_email": next_cand["email"],
                "next_pass": next_cand["attempt_order"],
                "executive_id": executive_id,
            }

        self.conn.execute(
            """
            UPDATE executives
            SET technical_status = 'EXHAUSTED_INVALID',
                current_status = 'EXHAUSTED_INVALID',
                primary_classification = 'EXHAUSTED_INVALID',
                active_candidate_id = NULL,
                updated_at = datetime('now')
            WHERE executive_id = ?
            """,
            (executive_id,),
        )
        self._audit("EXECUTIVE", str(executive_id), "EXHAUSTED", email, None, "max attempts reached")
        return {"outcome": "EXHAUSTED_INVALID", "email": email, "executive_id": executive_id}

    def _handle_unknown(self, candidate_id: int, executive_id: int, email: str) -> Dict[str, Any]:
        self.conn.execute(
            "UPDATE email_candidates SET status = 'UNKNOWN' WHERE candidate_id = ?",
            (candidate_id,),
        )
        self.conn.execute(
            """
            UPDATE executives
            SET technical_status = 'UNKNOWN_PENDING_RETRY',
                current_status = 'UNKNOWN_PENDING_RETRY',
                primary_classification = 'UNKNOWN_PENDING_RETRY',
                updated_at = datetime('now')
            WHERE executive_id = ?
            """,
            (executive_id,),
        )
        self._audit("EXECUTIVE", str(executive_id), "UNKNOWN", email, None, "stay on same email")
        return {
            "outcome": "UNKNOWN_PENDING_RETRY",
            "email": email,
            "executive_id": executive_id,
            "retry_same": True,
        }

    def _handle_accept_all(
        self,
        candidate_id: int,
        executive_id: int,
        domain_id: Optional[int],
        email: str,
    ) -> Dict[str, Any]:
        self.conn.execute(
            "UPDATE email_candidates SET is_active = 0, status = 'ACCEPT_ALL' WHERE candidate_id = ?",
            (candidate_id,),
        )
        if domain_id:
            self.conn.execute(
                """
                UPDATE domains
                SET technical_status = 'ACCEPT_ALL_QUARANTINE',
                    quarantine_reason = 'accept_all from NeverBounce',
                    quarantined_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE domain_id = ?
                """,
                (domain_id,),
            )
            # Deactivate active candidates on THIS exact domain only
            self.conn.execute(
                """
                UPDATE email_candidates
                SET is_active = 0
                WHERE domain_id = ? AND is_active = 1
                """,
                (domain_id,),
            )
            self.conn.execute(
                """
                UPDATE executives
                SET campaign_disposition = 'DOMAIN_QUARANTINED',
                    technical_status = 'DOMAIN_QUARANTINED',
                    current_status = 'DOMAIN_QUARANTINED',
                    primary_classification = 'DOMAIN_QUARANTINED',
                    active_candidate_id = NULL,
                    updated_at = datetime('now')
                WHERE executive_id IN (
                    SELECT c.executive_id FROM email_candidates c WHERE c.domain_id = ?
                )
                   OR executive_id = ?
                """,
                (domain_id, executive_id),
            )
        self._audit(
            "DOMAIN",
            str(domain_id) if domain_id else email.split("@")[-1],
            "QUARANTINE",
            None,
            "ACCEPT_ALL_QUARANTINE",
            "accept_all result — exact domain only",
        )
        return {
            "outcome": "ACCEPT_ALL_QUARANTINE",
            "email": email,
            "executive_id": executive_id,
            "domain_id": domain_id,
        }

    def _audit(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        old_value: Optional[str],
        new_value: Optional[str],
        reason: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, old_value, new_value, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity_type, entity_id, action, old_value, new_value, reason),
        )
