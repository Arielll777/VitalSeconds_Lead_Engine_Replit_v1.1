"""Manual overrides — audited. Campaign disposition never overwrites technical truth."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from vitalseconds.services.normalizer import Normalizer
from vitalseconds.utils.normalize import normalize_domain, normalize_name


class OverrideService:
    def __init__(self, conn):
        self.conn = conn
        self.norm = Normalizer()

    def _log(
        self,
        entity_type: str,
        entity_id: str,
        override_type: str,
        old_value: Optional[str],
        new_value: Optional[str],
        reason: str,
        batch_id: Optional[str] = None,
        user_context: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO manual_overrides (
                entity_type, entity_id, override_type, old_value, new_value,
                reason, batch_id, user_context
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_type, entity_id, override_type, old_value, new_value, reason, batch_id, user_context),
        )
        self.conn.execute(
            """
            INSERT INTO audit_log (
                entity_type, entity_id, action, old_value, new_value, reason, batch_id, user_context
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (entity_type, entity_id, f"OVERRIDE_{override_type}", old_value, new_value, reason, batch_id, user_context),
        )

    def _set_campaign(
        self, executive_id: int, disposition: str, reason: str, user: str, override_type: str
    ) -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT campaign_disposition, technical_status FROM executives WHERE executive_id = ?",
            (executive_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Executive not found"}
        old = row["campaign_disposition"]
        self.conn.execute(
            """
            UPDATE executives
            SET campaign_disposition = ?, updated_at = datetime('now')
            WHERE executive_id = ?
            """,
            (disposition, executive_id),
        )
        self._log(
            "EXECUTIVE",
            str(executive_id),
            override_type,
            old,
            disposition,
            reason + f" | technical_status unchanged={row['technical_status']}",
            user_context=user,
        )
        return {"ok": True, "technical_status": row["technical_status"], "campaign_disposition": disposition}

    def mark_excluded(self, executive_id: int, reason: str, user: str = "operator") -> Dict[str, Any]:
        return self._set_campaign(executive_id, "EXCLUDED", reason, user, "MARK_EXCLUDED")

    def mark_do_not_contact(self, executive_id: int, reason: str, user: str = "operator") -> Dict[str, Any]:
        return self._set_campaign(executive_id, "DO_NOT_CONTACT", reason, user, "MARK_DO_NOT_CONTACT")

    def mark_already_contacted(self, executive_id: int, reason: str, user: str = "operator") -> Dict[str, Any]:
        return self._set_campaign(executive_id, "ALREADY_CONTACTED", reason, user, "MARK_ALREADY_CONTACTED")

    def hold_to_net_new(self, executive_id: int, reason: str, user: str = "operator") -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT campaign_disposition FROM executives WHERE executive_id = ?", (executive_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Executive not found"}
        self.conn.execute(
            """
            UPDATE executives
            SET campaign_disposition = 'ACTIVE', updated_at = datetime('now')
            WHERE executive_id = ?
            """,
            (executive_id,),
        )
        self._log("EXECUTIVE", str(executive_id), "HOLD_TO_ACTIVE", row["campaign_disposition"], "ACTIVE", reason, user_context=user)
        return {"ok": True}

    def mark_company_duplicate(self, executive_id: int, reason: str, user: str = "operator") -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT campaign_disposition, technical_status, primary_classification FROM executives WHERE executive_id = ?",
            (executive_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Executive not found"}
        self.conn.execute(
            """
            UPDATE executives
            SET campaign_disposition = 'HOLD',
                primary_classification = 'COMPANY_DUPLICATE',
                updated_at = datetime('now')
            WHERE executive_id = ?
            """,
            (executive_id,),
        )
        self._log(
            "EXECUTIVE",
            str(executive_id),
            "MARK_COMPANY_DUPLICATE",
            row["campaign_disposition"],
            "HOLD",
            reason + f" | technical_status unchanged={row['technical_status']}",
            user_context=user,
        )
        return {"ok": True, "technical_status": row["technical_status"], "campaign_disposition": "HOLD"}

    def mark_executive_duplicate(self, executive_id: int, reason: str, user: str = "operator") -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT campaign_disposition, technical_status FROM executives WHERE executive_id = ?",
            (executive_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Executive not found"}
        self.conn.execute(
            """
            UPDATE executives
            SET campaign_disposition = 'HOLD',
                primary_classification = 'EXECUTIVE_DUPLICATE',
                updated_at = datetime('now')
            WHERE executive_id = ?
            """,
            (executive_id,),
        )
        self._log(
            "EXECUTIVE",
            str(executive_id),
            "MARK_EXECUTIVE_DUPLICATE",
            row["campaign_disposition"],
            "HOLD",
            reason + f" | technical_status unchanged={row['technical_status']}",
            user_context=user,
        )
        return {"ok": True, "technical_status": row["technical_status"], "campaign_disposition": "HOLD"}

    def release_quarantined_domain(self, domain: str, reason: str, user: str = "operator") -> Dict[str, Any]:
        """Release exact domain technically. Affected executives go to HOLD. No auto-resume."""
        norm = normalize_domain(domain)
        row = self.conn.execute(
            "SELECT domain_id, technical_status FROM domains WHERE normalized_domain = ?",
            (norm,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Domain not found"}
        if row["technical_status"] != "ACCEPT_ALL_QUARANTINE":
            return {"ok": False, "error": f"Domain status is {row['technical_status']}, not quarantined"}
        self.conn.execute(
            """
            UPDATE domains
            SET technical_status = 'RELEASED',
                released_at = datetime('now'),
                updated_at = datetime('now')
            WHERE domain_id = ?
            """,
            (row["domain_id"],),
        )
        execs = self.conn.execute(
            """
            SELECT DISTINCT e.executive_id
            FROM executives e
            JOIN email_candidates c ON c.executive_id = e.executive_id
            WHERE c.domain_id = ?
              AND (e.campaign_disposition = 'DOMAIN_QUARANTINED'
                   OR e.technical_status = 'DOMAIN_QUARANTINED')
            """,
            (row["domain_id"],),
        ).fetchall()
        moved = []
        for ex in execs:
            eid = ex["executive_id"]
            self.conn.execute(
                """
                UPDATE executives
                SET campaign_disposition = 'HOLD',
                    updated_at = datetime('now')
                WHERE executive_id = ?
                """,
                (eid,),
            )
            moved.append(eid)
        self._log(
            "DOMAIN",
            str(row["domain_id"]),
            "RELEASE_QUARANTINE",
            "ACCEPT_ALL_QUARANTINE",
            "RELEASED",
            reason + f" | executives→HOLD {moved}",
            user_context=user,
        )
        return {"ok": True, "domain_id": row["domain_id"], "executives_held": moved}

    def resume_verification(self, executive_id: int, reason: str, user: str = "operator") -> Dict[str, Any]:
        """Explicit resume after HOLD. Does not spend a credit; reactivates next PENDING/UNKNOWN candidate."""
        row = self.conn.execute(
            "SELECT campaign_disposition, technical_status FROM executives WHERE executive_id = ?",
            (executive_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Executive not found"}
        cand = self.conn.execute(
            """
            SELECT candidate_id, status FROM email_candidates
            WHERE executive_id = ?
              AND status IN ('PENDING', 'UNKNOWN', 'ACTIVE_VERIFICATION')
            ORDER BY attempt_order ASC
            LIMIT 1
            """,
            (executive_id,),
        ).fetchone()
        if not cand:
            return {"ok": False, "error": "No resumable candidate"}
        self.conn.execute(
            "UPDATE email_candidates SET is_active = 0 WHERE executive_id = ? AND is_active = 1",
            (executive_id,),
        )
        self.conn.execute(
            "UPDATE email_candidates SET is_active = 1, status = 'ACTIVE_VERIFICATION' WHERE candidate_id = ?",
            (cand["candidate_id"],),
        )
        tech = "UNKNOWN_PENDING_RETRY" if row["technical_status"] == "UNKNOWN_PENDING_RETRY" and cand["status"] == "UNKNOWN" else "NET_NEW"
        if cand["status"] == "UNKNOWN":
            tech = "UNKNOWN_PENDING_RETRY"
        self.conn.execute(
            """
            UPDATE executives
            SET campaign_disposition = 'ACTIVE',
                technical_status = ?,
                current_status = ?,
                active_candidate_id = ?,
                updated_at = datetime('now')
            WHERE executive_id = ?
            """,
            (tech, tech, cand["candidate_id"], executive_id),
        )
        self._log(
            "EXECUTIVE",
            str(executive_id),
            "RESUME_VERIFICATION",
            row["campaign_disposition"],
            "ACTIVE",
            reason,
            user_context=user,
        )
        return {"ok": True, "active_candidate_id": cand["candidate_id"]}

    def merge_company_aliases(
        self, source_company_id: int, target_company_id: int, reason: str, user: str = "operator"
    ) -> Dict[str, Any]:
        if source_company_id == target_company_id:
            return {"ok": False, "error": "Source and target are the same"}
        src = self.conn.execute(
            "SELECT canonical_name, normalized_name, current_status FROM companies WHERE company_id = ?",
            (source_company_id,),
        ).fetchone()
        tgt = self.conn.execute(
            "SELECT canonical_name FROM companies WHERE company_id = ?", (target_company_id,)
        ).fetchone()
        if not src or not tgt:
            return {"ok": False, "error": "Company not found"}

        self.conn.execute(
            """
            INSERT OR IGNORE INTO company_aliases (company_id, alias_name, normalized_alias, source)
            VALUES (?, ?, ?, 'MANUAL_MERGE')
            """,
            (target_company_id, src["canonical_name"], src["normalized_name"]),
        )
        # Move existing aliases
        self.conn.execute(
            """
            INSERT OR IGNORE INTO company_aliases (company_id, alias_name, normalized_alias, source)
            SELECT ?, alias_name, normalized_alias, 'MANUAL_MERGE'
            FROM company_aliases WHERE company_id = ?
            """,
            (target_company_id, source_company_id),
        )
        # Roles: record historical affiliation, then re-point current company when unique
        self.conn.execute(
            """
            INSERT OR IGNORE INTO executive_company_roles (executive_id, company_id, is_current, source)
            SELECT executive_id, company_id, 0, 'MERGE'
            FROM executives WHERE company_id = ?
            """,
            (source_company_id,),
        )
        self.conn.execute(
            """
            UPDATE executives SET company_id = ?
            WHERE company_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM executives e2
                  WHERE e2.company_id = ? AND e2.normalized_full_name = executives.normalized_full_name
              )
            """,
            (target_company_id, source_company_id, target_company_id),
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO company_domain_relationships (company_id, domain_id, is_historical, is_current, source)
            SELECT ?, domain_id, 1, 0, 'MERGE' FROM domains WHERE company_id = ?
            """,
            (target_company_id, source_company_id),
        )
        self.conn.execute(
            """
            INSERT OR IGNORE INTO company_buying_groups (company_id, buying_group_id, source)
            SELECT ?, buying_group_id, 'MERGE' FROM company_buying_groups WHERE company_id = ?
            """,
            (target_company_id, source_company_id),
        )
        self.conn.execute(
            """
            UPDATE companies
            SET current_status = 'MERGED',
                merged_into_company_id = ?,
                updated_at = datetime('now')
            WHERE company_id = ?
            """,
            (target_company_id, source_company_id),
        )
        self._log(
            "COMPANY",
            str(source_company_id),
            "MERGE_ALIASES",
            src["canonical_name"],
            tgt["canonical_name"],
            reason,
            user_context=user,
        )
        return {"ok": True, "source_status": "MERGED", "canonical_company_id": target_company_id}

    def correct_canonical_company_name(
        self, company_id: int, new_name: str, reason: str, user: str = "operator"
    ) -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT canonical_name, normalized_name FROM companies WHERE company_id = ?",
            (company_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Company not found"}
        old = row["canonical_name"]
        new_norm = self.norm.company(new_name)
        self.conn.execute(
            """
            INSERT OR IGNORE INTO company_aliases (company_id, alias_name, normalized_alias, source)
            VALUES (?, ?, ?, 'RENAME')
            """,
            (company_id, old, row["normalized_name"]),
        )
        self.conn.execute(
            """
            UPDATE companies
            SET canonical_name = ?, normalized_name = ?, updated_at = datetime('now')
            WHERE company_id = ?
            """,
            (new_name.strip(), new_norm, company_id),
        )
        self._log("COMPANY", str(company_id), "CORRECT_NAME", old, new_name, reason, user_context=user)
        return {"ok": True}

    def correct_canonical_domain(
        self, domain_id: int, new_domain: str, reason: str, user: str = "operator"
    ) -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT domain, normalized_domain FROM domains WHERE domain_id = ?", (domain_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Domain not found"}
        old = row["domain"]
        norm = normalize_domain(new_domain)
        self.conn.execute(
            """
            UPDATE domains
            SET domain = ?, normalized_domain = ?, updated_at = datetime('now')
            WHERE domain_id = ?
            """,
            (norm, norm, domain_id),
        )
        self._log("DOMAIN", str(domain_id), "CORRECT_DOMAIN", old, norm, reason, user_context=user)
        return {"ok": True}

    def add_historical_domain(
        self, company_id: int, domain: str, reason: str, user: str = "operator"
    ) -> Dict[str, Any]:
        norm = normalize_domain(domain)
        existing = self.conn.execute(
            "SELECT domain_id FROM domains WHERE normalized_domain = ?", (norm,)
        ).fetchone()
        if existing:
            did = existing["domain_id"]
        else:
            cur = self.conn.execute(
                """
                INSERT INTO domains (domain, normalized_domain, company_id, is_historical)
                VALUES (?, ?, ?, 1)
                """,
                (norm, norm, company_id),
            )
            did = cur.lastrowid
        self.conn.execute(
            """
            INSERT OR IGNORE INTO company_domain_relationships
                (company_id, domain_id, is_historical, is_current, source)
            VALUES (?, ?, 1, 0, 'MANUAL')
            """,
            (company_id, did),
        )
        self._log("DOMAIN", str(did), "ADD_HISTORICAL_DOMAIN", None, norm, reason, user_context=user)
        return {"ok": True, "domain_id": did}

    def assign_buying_group(
        self, company_id: int, buying_group: str, reason: str, user: str = "operator"
    ) -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT buying_group FROM companies WHERE company_id = ?", (company_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Company not found"}
        name = (buying_group or "").strip()
        old = row["buying_group"]
        self.conn.execute(
            "UPDATE companies SET buying_group = ?, updated_at = datetime('now') WHERE company_id = ?",
            (name or None, company_id),
        )
        if name:
            bg = self.conn.execute(
                "SELECT buying_group_id FROM buying_groups WHERE normalized_name = ?",
                (name.lower(),),
            ).fetchone()
            if bg:
                bgid = bg["buying_group_id"]
            else:
                cur = self.conn.execute(
                    "INSERT INTO buying_groups (name, normalized_name) VALUES (?, ?)",
                    (name, name.lower()),
                )
                bgid = cur.lastrowid
            self.conn.execute(
                """
                INSERT OR IGNORE INTO company_buying_groups (company_id, buying_group_id, source)
                VALUES (?, ?, 'MANUAL')
                """,
                (company_id, bgid),
            )
        self._log("COMPANY", str(company_id), "ASSIGN_BUYING_GROUP", old, name, reason, user_context=user)
        return {"ok": True}

    def correct_executive_metadata(
        self,
        executive_id: int,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        title: Optional[str] = None,
        state: Optional[str] = None,
        reason: str = "",
        user: str = "operator",
    ) -> Dict[str, Any]:
        row = self.conn.execute(
            "SELECT first_name, last_name, title, state FROM executives WHERE executive_id = ?",
            (executive_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "Executive not found"}
        old = dict(row)
        updates = {}
        if first_name is not None:
            updates["first_name"] = first_name.strip()
        if last_name is not None:
            updates["last_name"] = last_name.strip()
        if title is not None:
            updates["title"] = title.strip()
        if state is not None:
            updates["state"] = state.strip()
        if not updates:
            return {"ok": False, "error": "No fields to update"}
        if "first_name" in updates or "last_name" in updates:
            fn = updates.get("first_name", old["first_name"])
            ln = updates.get("last_name", old["last_name"])
            updates["normalized_full_name"] = normalize_name(f"{fn} {ln}")
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [executive_id]
        self.conn.execute(
            f"UPDATE executives SET {sets}, updated_at = datetime('now') WHERE executive_id = ?",
            vals,
        )
        self._log(
            "EXECUTIVE",
            str(executive_id),
            "CORRECT_METADATA",
            json.dumps(old),
            json.dumps(updates),
            reason,
            user_context=user,
        )
        return {"ok": True}
