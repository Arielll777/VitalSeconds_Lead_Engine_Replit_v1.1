"""Gatekeeper deduplication against full historical Master.

Buying-group match is independent of company-name match.
Merged companies resolve to the canonical target.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from vitalseconds.config import CLASSIFICATION_PRECEDENCE
from vitalseconds.services.normalizer import Normalizer


class Deduplicator:
    def __init__(self, conn):
        self.conn = conn
        self.norm = Normalizer()

    def classify_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        n = self.norm.row_for_matching(row)
        reasons: List[str] = []
        details: Dict[str, Any] = {}

        if not n["first_name"] or not n["last_name"] or not n["company"] or not n["domain"]:
            return {
                "primary_classification": "DATA_ERROR",
                "all_matched_reasons": ["DATA_ERROR"],
                "details": {"missing": "first_name/last_name/company/domain"},
            }

        domain_row = self.conn.execute(
            "SELECT domain_id, technical_status, company_id FROM domains WHERE normalized_domain = ?",
            (n["domain"],),
        ).fetchone()
        if domain_row and domain_row["technical_status"] == "ACCEPT_ALL_QUARANTINE":
            reasons.append("DOMAIN_QUARANTINED")
            details["domain_id"] = domain_row["domain_id"]

        if n["email"]:
            email_row = self.conn.execute(
                """
                SELECT c.candidate_id, c.status, c.executive_id,
                       e.technical_status, e.campaign_disposition
                FROM email_candidates c
                JOIN executives e ON e.executive_id = c.executive_id
                WHERE c.normalized_email = ?
                """,
                (n["email"],),
            ).fetchone()
            if email_row:
                reasons.append("EXACT_DUPLICATE")
                details["existing_candidate_id"] = email_row["candidate_id"]
                if email_row["status"] == "VERIFIED_VALID" or email_row["technical_status"] in (
                    "VERIFIED_VALID",
                    "PREVIOUSLY_VALID",
                ):
                    reasons.append("PREVIOUSLY_VALID")
                if email_row["technical_status"] == "UNKNOWN_PENDING_RETRY":
                    reasons.append("UNKNOWN_PENDING_RETRY")
                if email_row["technical_status"] in ("EXHAUSTED", "EXHAUSTED_INVALID"):
                    reasons.append("EXHAUSTED")

        # Buying group is independent of company-name match. Never infer.
        bg = (n.get("buying_group") or "").strip()
        if bg:
            bg_row = self.conn.execute(
                """
                SELECT bg.buying_group_id, cbg.company_id
                FROM buying_groups bg
                JOIN company_buying_groups cbg ON cbg.buying_group_id = bg.buying_group_id
                WHERE bg.normalized_name = ?
                LIMIT 1
                """,
                (bg.strip().lower(),),
            ).fetchone()
            if not bg_row:
                bg_row = self.conn.execute(
                    """
                    SELECT company_id FROM companies
                    WHERE lower(buying_group) = ? AND current_status = 'ACTIVE'
                    LIMIT 1
                    """,
                    (bg.strip().lower(),),
                ).fetchone()
            if bg_row:
                reasons.append("BUYING_GROUP_DUPLICATE")
                details["buying_group"] = bg
                details["buying_group_company_id"] = (
                    bg_row["company_id"] if "company_id" in bg_row.keys() else bg_row[0]
                )

        company_id = self._find_canonical_company_id(n["company"])
        if company_id:
            reasons.append("COMPANY_DUPLICATE")
            details["company_id"] = company_id
            exec_row = self.conn.execute(
                """
                SELECT executive_id, technical_status, campaign_disposition, primary_classification
                FROM executives
                WHERE company_id = ? AND normalized_full_name = ?
                """,
                (company_id, n["full_name"]),
            ).fetchone()
            if not exec_row:
                exec_row = self.conn.execute(
                    """
                    SELECT e.executive_id, e.technical_status, e.campaign_disposition
                    FROM executives e
                    JOIN executive_company_roles r ON r.executive_id = e.executive_id
                    WHERE r.company_id = ? AND e.normalized_full_name = ?
                    """,
                    (company_id, n["full_name"]),
                ).fetchone()
            if exec_row:
                reasons.append("EXECUTIVE_DUPLICATE")
                details["executive_id"] = exec_row["executive_id"]
                if exec_row["technical_status"] in ("VERIFIED_VALID", "PREVIOUSLY_VALID"):
                    reasons.append("PREVIOUSLY_VALID")
                if exec_row["technical_status"] == "UNKNOWN_PENDING_RETRY":
                    reasons.append("UNKNOWN_PENDING_RETRY")
                if exec_row["technical_status"] in ("EXHAUSTED", "EXHAUSTED_INVALID"):
                    reasons.append("EXHAUSTED")

        if "EXECUTIVE_DUPLICATE" not in reasons:
            cross = self.conn.execute(
                """
                SELECT e.executive_id, e.company_id, e.technical_status
                FROM executives e
                WHERE e.normalized_full_name = ?
                LIMIT 1
                """,
                (n["full_name"],),
            ).fetchone()
            if cross:
                reasons.append("EXECUTIVE_DUPLICATE")
                details["cross_company_executive_id"] = cross["executive_id"]

        if not reasons:
            primary = "NET_NEW"
            reasons = ["NET_NEW"]
        else:
            primary = self._highest_precedence(reasons)

        return {
            "primary_classification": primary,
            "all_matched_reasons": sorted(
                set(reasons),
                key=lambda r: CLASSIFICATION_PRECEDENCE.index(r)
                if r in CLASSIFICATION_PRECEDENCE
                else 999,
            ),
            "details": details,
            "normalized": n,
        }

    def _find_canonical_company_id(self, normalized_company: str) -> Optional[int]:
        row = self.conn.execute(
            """
            SELECT company_id, current_status, merged_into_company_id
            FROM companies WHERE normalized_name = ?
            """,
            (normalized_company,),
        ).fetchone()
        if row:
            return self._follow_merge(row)
        alias = self.conn.execute(
            """
            SELECT c.company_id, c.current_status, c.merged_into_company_id
            FROM company_aliases a
            JOIN companies c ON c.company_id = a.company_id
            WHERE a.normalized_alias = ?
            """,
            (normalized_company,),
        ).fetchone()
        if alias:
            return self._follow_merge(alias)
        return None

    def _follow_merge(self, row) -> int:
        cid = int(row["company_id"])
        seen = set()
        status = row["current_status"]
        merged = row["merged_into_company_id"]
        while status in ("MERGED", "INACTIVE_ALIAS") and merged and cid not in seen:
            seen.add(cid)
            nxt = self.conn.execute(
                """
                SELECT company_id, current_status, merged_into_company_id
                FROM companies WHERE company_id = ?
                """,
                (merged,),
            ).fetchone()
            if not nxt:
                break
            cid = int(nxt["company_id"])
            status = nxt["current_status"]
            merged = nxt["merged_into_company_id"]
        return cid

    def _highest_precedence(self, reasons: List[str]) -> str:
        best = "NET_NEW"
        best_idx = len(CLASSIFICATION_PRECEDENCE)
        for r in reasons:
            if r in CLASSIFICATION_PRECEDENCE:
                idx = CLASSIFICATION_PRECEDENCE.index(r)
                if idx < best_idx:
                    best_idx = idx
                    best = r
        return best
