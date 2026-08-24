"""Importers for Master, Raw Leads, NeverBounce, and Master Workbook."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pandas as pd

from vitalseconds.services.batch import BatchService
from vitalseconds.services.candidate_generator import CandidateGenerator
from vitalseconds.services.deduplicator import Deduplicator
from vitalseconds.services.normalizer import Normalizer
from vitalseconds.services.waterfall import WaterfallEngine
from vitalseconds.utils.fingerprint import make_fingerprint, make_header_fingerprint
from vitalseconds.utils.normalize import normalize_domain, normalize_email, normalize_name


HEADER_ALIASES = {
    "first_name": ["first_name", "firstname", "first", "first name", "fname"],
    "last_name": ["last_name", "lastname", "last", "last name", "lname", "surname"],
    "company": ["company", "company_name", "company name", "organization", "org"],
    "domain": ["domain", "website", "web", "url", "company_domain"],
    "email": ["email", "e-mail", "email_address", "email address", "best_verified_email"],
    "public_email": ["public_email", "public email", "known_email", "verified_email", "best_verified_email"],
    "title": ["title", "job_title", "job title", "position", "role"],
    "state": ["state", "st", "province", "region"],
    "buying_group": ["buying_group", "buying group", "parent", "parent_company"],
    "historical_domain": ["historical_domain", "old_domain", "previous_domain"],
    "notes": ["notes", "note", "comments", "comment", "reason_or_status", "gemini_instruction"],
    "neverbounce_status": ["neverbounce_status", "status", "result", "verification_status", "email_status"],
    "master_status": ["master_status", "technical_status", "reason_or_status"],
    "send_disposition": ["send_disposition", "campaign_disposition", "disposition"],
    "blacklist_type": ["blacklist_type"],
    "invalid_emails": ["invalid_emails"],
    "risky_or_unknown_emails": ["risky_or_unknown_emails"],
    "unverified_candidate_emails": ["unverified_candidate_emails"],
    "next_verification_email": ["next_verification_email"],
    "technical_statuses_seen": ["technical_statuses_seen"],
}


def suggest_mapping(headers: List[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    used: set = set()
    lower_map = {h: h.strip().lower().lstrip("\ufeff") for h in headers}
    for canonical, aliases in HEADER_ALIASES.items():
        for orig, low in lower_map.items():
            if low in aliases and canonical not in used:
                mapping[orig] = canonical
                used.add(canonical)
                break
    return mapping


def _split_emails(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    parts = [normalize_email(p) for p in str(raw).replace(";", "|").split("|")]
    return [p for p in parts if p and "@" in p]


class BaseImporter:
    def __init__(self, conn):
        self.conn = conn
        self.norm = Normalizer()
        self.batch_svc = BatchService(conn)

    def save_mapping(self, source_type: str, headers: List[str], mapping: Dict[str, str]) -> None:
        fp = make_header_fingerprint(headers)
        self.conn.execute(
            """
            INSERT INTO column_mappings (source_type, header_fingerprint, mapping_json, last_used_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(source_type, header_fingerprint)
            DO UPDATE SET mapping_json = excluded.mapping_json, last_used_at = datetime('now')
            """,
            (source_type, fp, json.dumps(mapping)),
        )

    def load_mapping(self, source_type: str, headers: List[str]) -> Optional[Dict[str, str]]:
        fp = make_header_fingerprint(headers)
        row = self.conn.execute(
            """
            SELECT mapping_json FROM column_mappings
            WHERE source_type = ? AND header_fingerprint = ?
            """,
            (source_type, fp),
        ).fetchone()
        if row:
            return json.loads(row["mapping_json"])
        return None

    def apply_mapping(self, df: pd.DataFrame, mapping: Dict[str, str]) -> List[Dict[str, Any]]:
        rows = []
        for _, series in df.iterrows():
            item: Dict[str, Any] = {}
            for orig, canon in mapping.items():
                if orig in series:
                    val = series[orig]
                    item[canon] = None if pd.isna(val) else str(val).strip()
            rows.append(item)
        return rows

    def _link_buying_group(self, company_id: int, buying_group: Optional[str], source: str = "MASTER") -> None:
        name = (buying_group or "").strip()
        if not name:
            return
        self.conn.execute(
            "UPDATE companies SET buying_group = COALESCE(NULLIF(buying_group,''), ?) WHERE company_id = ?",
            (name, company_id),
        )
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
            VALUES (?, ?, ?)
            """,
            (company_id, bgid, source),
        )

    def _upsert_company(self, name: str, buying_group: Optional[str] = None) -> int:
        norm = self.norm.company(name)
        row = self.conn.execute(
            "SELECT company_id, current_status, merged_into_company_id FROM companies WHERE normalized_name = ?",
            (norm,),
        ).fetchone()
        if row:
            cid = int(row["company_id"])
            if row["current_status"] in ("MERGED", "INACTIVE_ALIAS") and row["merged_into_company_id"]:
                cid = int(row["merged_into_company_id"])
            self._link_buying_group(cid, buying_group)
            return cid
        alias = self.conn.execute(
            """
            SELECT c.company_id, c.current_status, c.merged_into_company_id
            FROM company_aliases a JOIN companies c ON c.company_id = a.company_id
            WHERE a.normalized_alias = ?
            """,
            (norm,),
        ).fetchone()
        if alias:
            cid = int(alias["company_id"])
            if alias["current_status"] in ("MERGED", "INACTIVE_ALIAS") and alias["merged_into_company_id"]:
                cid = int(alias["merged_into_company_id"])
            self._link_buying_group(cid, buying_group)
            return cid
        cur = self.conn.execute(
            "INSERT INTO companies (canonical_name, normalized_name, buying_group) VALUES (?, ?, ?)",
            (name.strip(), norm, buying_group),
        )
        cid = int(cur.lastrowid)
        self._link_buying_group(cid, buying_group)
        return cid

    def _upsert_domain(self, domain: str, company_id: int, is_historical: bool = False) -> int:
        norm = normalize_domain(domain)
        row = self.conn.execute(
            "SELECT domain_id FROM domains WHERE normalized_domain = ?", (norm,)
        ).fetchone()
        if row:
            did = int(row["domain_id"])
            self.conn.execute(
                "UPDATE domains SET company_id = COALESCE(company_id, ?), updated_at = datetime('now') WHERE domain_id = ?",
                (company_id, did),
            )
        else:
            cur = self.conn.execute(
                """
                INSERT INTO domains (domain, normalized_domain, company_id, is_historical)
                VALUES (?, ?, ?, ?)
                """,
                (norm, norm, company_id, 1 if is_historical else 0),
            )
            did = int(cur.lastrowid)
        self.conn.execute(
            """
            INSERT OR IGNORE INTO company_domain_relationships
                (company_id, domain_id, is_historical, is_current, source)
            VALUES (?, ?, ?, ?, 'MASTER')
            """,
            (company_id, did, 1 if is_historical else 0, 0 if is_historical else 1),
        )
        return did

    def _upsert_executive(
        self,
        company_id: int,
        first: str,
        last: str,
        title: Optional[str],
        state: Optional[str],
        technical: str = "NET_NEW",
        campaign: str = "ACTIVE",
    ) -> int:
        full = normalize_name(f"{first} {last}")
        row = self.conn.execute(
            "SELECT executive_id FROM executives WHERE company_id = ? AND normalized_full_name = ?",
            (company_id, full),
        ).fetchone()
        if row:
            eid = int(row["executive_id"])
        else:
            cur = self.conn.execute(
                """
                INSERT INTO executives (
                    company_id, first_name, last_name, normalized_full_name, title, state,
                    technical_status, campaign_disposition, current_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (company_id, first, last, full, title, state, technical, campaign, technical),
            )
            eid = int(cur.lastrowid)
        self.conn.execute(
            """
            INSERT OR IGNORE INTO executive_company_roles (executive_id, company_id, title, is_current, source)
            VALUES (?, ?, ?, 1, 'MASTER')
            """,
            (eid, company_id, title),
        )
        return eid

    def _upsert_candidate(
        self,
        executive_id: int,
        email: str,
        domain_id: Optional[int],
        basis: str,
        pass_order: int,
        status: str,
        attempt_order: int = 0,
        activate: bool = False,
    ) -> Optional[int]:
        email = normalize_email(email)
        existing = self.conn.execute(
            "SELECT candidate_id, status FROM email_candidates WHERE normalized_email = ?",
            (email,),
        ).fetchone()
        if existing:
            return int(existing["candidate_id"])
        cur = self.conn.execute(
            """
            INSERT INTO email_candidates (
                executive_id, email, normalized_email, domain_id,
                candidate_basis, pass_order, attempt_order, is_active, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                executive_id,
                email,
                email,
                domain_id,
                basis,
                pass_order,
                attempt_order,
                1 if activate else 0,
                status,
            ),
        )
        return int(cur.lastrowid)


def interpret_historical_status(row: Dict[str, Any]) -> Dict[str, str]:
    """Map Master workbook fields to technical + campaign. Never guess."""
    blob = " ".join(
        str(row.get(k) or "")
        for k in (
            "master_status",
            "send_disposition",
            "blacklist_type",
            "technical_statuses_seen",
            "notes",
            "neverbounce_status",
        )
    ).upper()
    campaign = "ACTIVE"
    technical = "NET_NEW"
    review = False

    if any(x in blob for x in ("DO_NOT_CONTACT", "DNC", "BLACKLIST", "UNSUBSCRIBE")):
        campaign = "DO_NOT_CONTACT"
    elif "EXCLUDED" in blob:
        campaign = "EXCLUDED"
    elif any(x in blob for x in ("ALREADY_CONTACTED", "CONTACTED")):
        campaign = "ALREADY_CONTACTED"
    elif any(x in blob for x in ("EXPORTED", "GRISHA")):
        campaign = "EXPORTED_TO_GRISHA"
    elif "HOLD" in blob:
        campaign = "HOLD"

    if any(x in blob for x in ("ACCEPT_ALL", "CATCH_ALL", "CATCHALL")):
        technical = "DOMAIN_QUARANTINED"
    elif any(x in blob for x in ("VALID", "VERIFIED")) and "INVALID" not in blob.replace("VALID", ""):
        # "VALID" in blob — careful with INVALID containing VALID substring
        if "INVALID" not in blob and "UNVERIFIED" not in blob:
            technical = "VERIFIED_VALID"
        elif "VERIFIED_VALID" in blob or blob.strip() in ("VALID", "VERIFIED"):
            technical = "VERIFIED_VALID"
    if "UNKNOWN" in blob or "UNKNOWN_RETRY" in blob:
        technical = "UNKNOWN_PENDING_RETRY"
    if "EXHAUSTED" in blob:
        technical = "EXHAUSTED_INVALID"
    if blob and technical == "NET_NEW" and campaign == "ACTIVE":
        # known data but unmapped → HOLD/IMPORT_REVIEW rather than guess
        if row.get("master_status") or row.get("send_disposition"):
            review = True
            campaign = "HOLD"
            technical = "IMPORT_REVIEW"

    # more precise VALID
    ms = (row.get("master_status") or "").strip().upper()
    if ms in ("VALID", "VERIFIED", "VERIFIED_VALID", "PREVIOUSLY_VALID"):
        technical = "VERIFIED_VALID"
        review = False
        if campaign == "HOLD":
            campaign = "ACTIVE"
    if ms in ("UNKNOWN", "UNKNOWN_RETRY", "UNKNOWN_PENDING_RETRY"):
        technical = "UNKNOWN_PENDING_RETRY"
        review = False
        if campaign == "HOLD":
            campaign = "ACTIVE"
    if ms in ("INVALID", "EXHAUSTED", "EXHAUSTED_INVALID"):
        technical = "EXHAUSTED_INVALID"
        review = False
    if ms in ("ACCEPT_ALL", "CATCH_ALL", "ACCEPT_ALL_QUARANTINE"):
        technical = "DOMAIN_QUARANTINED"
        review = False

    return {
        "technical_status": technical,
        "campaign_disposition": campaign,
        "import_review": "1" if review else "0",
    }


class MasterImporter(BaseImporter):
    """Seed from historical Master. Preserve technical truth. Never rewrite Valid as PENDING."""

    def import_rows(
        self,
        rows: List[Dict[str, Any]],
        batch_id: str,
        description: Optional[str] = None,
        sheet_name: str = "",
        source_type: str = "MASTER",
    ) -> Dict[str, Any]:
        if not self.batch_svc.exists(batch_id):
            self.batch_svc.create(batch_id, "MASTER_IMPORT", description)
        stats = {
            "companies": 0,
            "domains": 0,
            "executives": 0,
            "candidates": 0,
            "rows": 0,
            "import_review": 0,
        }

        for raw in rows:
            self.conn.execute(
                """
                INSERT INTO import_source_rows (batch_id, source_type, raw_json, fingerprint, sheet_name)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    source_type,
                    json.dumps(raw),
                    make_fingerprint(json.dumps(raw, sort_keys=True)),
                    sheet_name or None,
                ),
            )
            stats["rows"] += 1
            company_name = raw.get("company") or ""
            domain = normalize_domain(raw.get("domain") or "")
            first = (raw.get("first_name") or "").strip()
            last = (raw.get("last_name") or "").strip()
            if not company_name or not domain:
                continue

            interp = interpret_historical_status(raw)
            if interp["import_review"] == "1":
                stats["import_review"] += 1

            company_id = self._upsert_company(company_name, raw.get("buying_group"))
            domain_id = self._upsert_domain(domain, company_id, is_historical=False)
            if raw.get("historical_domain"):
                hist = normalize_domain(raw["historical_domain"])
                if hist and hist != domain:
                    self._upsert_domain(hist, company_id, is_historical=True)

            if interp["technical_status"] == "DOMAIN_QUARANTINED":
                self.conn.execute(
                    """
                    UPDATE domains SET technical_status = 'ACCEPT_ALL_QUARANTINE',
                        quarantine_reason = 'master import',
                        quarantined_at = datetime('now'),
                        updated_at = datetime('now')
                    WHERE domain_id = ?
                    """,
                    (domain_id,),
                )

            if first and last:
                campaign = interp["campaign_disposition"]
                tech = interp["technical_status"]
                exec_id = self._upsert_executive(
                    company_id, first, last, raw.get("title"), raw.get("state"),
                    technical=tech, campaign=campaign,
                )
                self.conn.execute(
                    """
                    UPDATE executives
                    SET technical_status = ?, current_status = ?, campaign_disposition = ?,
                        primary_classification = ?, updated_at = datetime('now')
                    WHERE executive_id = ?
                    """,
                    (tech, tech, campaign, tech, exec_id),
                )

                valid_email = normalize_email(raw.get("public_email") or raw.get("email") or "")
                next_email = normalize_email(raw.get("next_verification_email") or "")
                invalids = _split_emails(raw.get("invalid_emails"))
                unknowns = _split_emails(raw.get("risky_or_unknown_emails"))
                unverified = _split_emails(raw.get("unverified_candidate_emails"))

                attempt = 0
                if valid_email:
                    attempt += 1
                    st = "VERIFIED_VALID" if tech == "VERIFIED_VALID" else "PENDING"
                    if tech == "VERIFIED_VALID":
                        st = "VERIFIED_VALID"
                    self._upsert_candidate(
                        exec_id, valid_email, domain_id, "HISTORICAL",
                        pass_order=0, status=st, attempt_order=attempt, activate=False,
                    )
                    stats["candidates"] += 1
                    if campaign == "EXPORTED_TO_GRISHA" and st == "VERIFIED_VALID":
                        cid_row = self.conn.execute(
                            "SELECT candidate_id FROM email_candidates WHERE normalized_email = ?",
                            (valid_email,),
                        ).fetchone()
                        if cid_row:
                            self.conn.execute(
                                """
                                INSERT OR IGNORE INTO grisha_exports
                                    (candidate_id, normalized_email, executive_id, batch_id)
                                VALUES (?, ?, ?, ?)
                                """,
                                (cid_row["candidate_id"], valid_email, exec_id, batch_id),
                            )

                for em, st in (
                    *[(e, "INVALID") for e in invalids],
                    *[(e, "UNKNOWN") for e in unknowns],
                ):
                    if em == valid_email:
                        continue
                    attempt += 1
                    self._upsert_candidate(
                        exec_id, em, domain_id, "HISTORICAL",
                        pass_order=0, status=st, attempt_order=min(attempt, 3), activate=False,
                    )
                    stats["candidates"] += 1

                activate_next = tech in ("UNKNOWN_PENDING_RETRY", "NET_NEW", "IMPORT_REVIEW")
                target = next_email or (unverified[0] if unverified else "")
                if target:
                    already = self.conn.execute(
                        "SELECT candidate_id, status FROM email_candidates WHERE normalized_email = ?",
                        (target,),
                    ).fetchone()
                    st = "UNKNOWN" if tech == "UNKNOWN_PENDING_RETRY" else "PENDING"
                    if not already:
                        attempt += 1
                        cid = self._upsert_candidate(
                            exec_id, target, domain_id, "HISTORICAL",
                            pass_order=0, status=st, attempt_order=min(attempt, 3),
                            activate=activate_next and campaign == "ACTIVE",
                        )
                        stats["candidates"] += 1
                    elif activate_next and campaign == "ACTIVE":
                        self.conn.execute(
                            "UPDATE email_candidates SET is_active = 1 WHERE candidate_id = ?",
                            (already["candidate_id"],),
                        )
                        cid = already["candidate_id"]
                    else:
                        cid = already["candidate_id"]
                    if activate_next and campaign == "ACTIVE":
                        self.conn.execute(
                            "UPDATE executives SET active_candidate_id = ? WHERE executive_id = ?",
                            (cid, exec_id),
                        )

                stats["executives"] += 1

            stats["companies"] += 1
            stats["domains"] += 1

        self.conn.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, new_value, reason, batch_id)
            VALUES ('BATCH', ?, 'MASTER_IMPORT', ?, ?, ?)
            """,
            (batch_id, json.dumps(stats), sheet_name or "seed from Master", batch_id),
        )
        return stats


class RawLeadsImporter(MasterImporter):
    def __init__(self, conn):
        super().__init__(conn)
        self.deduper = Deduplicator(conn)
        self.cand_gen = CandidateGenerator(conn)

    def import_and_classify(
        self,
        rows: List[Dict[str, Any]],
        batch_id: str,
        description: Optional[str] = None,
        generate_candidates: bool = True,
    ) -> Dict[str, Any]:
        self.batch_svc.create(batch_id, "RAW_LEADS", description)
        results: List[Dict[str, Any]] = []
        stats = {
            "total": 0,
            "net_new": 0,
            "duplicates": 0,
            "data_error": 0,
            "candidates_created": 0,
        }
        for raw in rows:
            self.conn.execute(
                "INSERT INTO import_source_rows (batch_id, source_type, raw_json) VALUES (?, 'RAW_LEADS', ?)",
                (batch_id, json.dumps(raw)),
            )
            stats["total"] += 1
            classification = self.deduper.classify_row(raw)
            primary = classification["primary_classification"]
            entry = {"raw": raw, "classification": classification}
            if primary == "DATA_ERROR":
                stats["data_error"] += 1
                results.append(entry)
                continue
            if primary != "NET_NEW":
                stats["duplicates"] += 1
                results.append(entry)
                continue

            company_name = raw.get("company") or ""
            domain = normalize_domain(raw.get("domain") or "")
            first = (raw.get("first_name") or "").strip()
            last = (raw.get("last_name") or "").strip()
            company_id = self._upsert_company(company_name, raw.get("buying_group"))
            domain_id = self._upsert_domain(domain, company_id)
            exec_id = self._upsert_executive(company_id, first, last, raw.get("title"), raw.get("state"))
            self.conn.execute(
                """
                UPDATE executives
                SET technical_status = 'NET_NEW', current_status = 'NET_NEW',
                    campaign_disposition = 'ACTIVE', primary_classification = 'NET_NEW',
                    updated_at = datetime('now')
                WHERE executive_id = ?
                """,
                (exec_id,),
            )
            if generate_candidates:
                cands = self.cand_gen.generate_for_executive(
                    exec_id, first, last, domain,
                    public_email=raw.get("public_email") or raw.get("email"),
                    domain_id=domain_id,
                )
                active_id = self.cand_gen.persist_candidates(cands, activate_first=True)
                entry["active_candidate_id"] = active_id
                entry["candidates"] = cands
                stats["candidates_created"] += len(cands)
            stats["net_new"] += 1
            results.append(entry)

        self.conn.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, new_value, reason, batch_id)
            VALUES ('BATCH', ?, 'RAW_LEADS_IMPORT', ?, 'classified and candidate generation', ?)
            """,
            (batch_id, json.dumps(stats), batch_id),
        )
        return {"stats": stats, "results": results}


class NeverBounceImporter(BaseImporter):
    def __init__(self, conn):
        super().__init__(conn)
        self.waterfall = WaterfallEngine(conn)

    def process_results(
        self,
        rows: List[Dict[str, Any]],
        batch_id: str,
        source_filename: str = "neverbounce.csv",
        description: Optional[str] = None,
        source_file_sha256: Optional[str] = None,
        verifier: str = "NeverBounce",
    ) -> Dict[str, Any]:
        if not self.batch_svc.exists(batch_id):
            self.batch_svc.create(batch_id, "NEVERBOUNCE", description)
        file_sha = source_file_sha256 or source_filename
        outcomes: List[Dict[str, Any]] = []
        stats = {
            "total": 0, "valid": 0, "invalid": 0, "unknown": 0, "accept_all": 0,
            "unmatched": 0, "already_processed": 0, "advanced": 0, "exhausted": 0,
        }
        for row_number, raw in enumerate(rows, start=1):
            email = raw.get("email") or raw.get("Email") or ""
            status = raw.get("neverbounce_status") or raw.get("status") or raw.get("result") or ""
            if not email:
                continue
            stats["total"] += 1
            result = self.waterfall.process_result(
                email=email,
                neverbounce_status=status,
                batch_id=batch_id,
                source_identifier=source_filename,
                raw_result=raw,
                source_file_sha256=file_sha,
                source_row_number=row_number,
                source_type="NEVERBOUNCE",
                verifier=verifier,
            )
            outcomes.append(result)
            outcome = result.get("outcome", "")
            if outcome == "VERIFIED_VALID":
                stats["valid"] += 1
            elif outcome == "ADVANCED":
                stats["invalid"] += 1
                stats["advanced"] += 1
            elif outcome == "EXHAUSTED_INVALID":
                stats["invalid"] += 1
                stats["exhausted"] += 1
            elif outcome == "UNKNOWN_PENDING_RETRY":
                stats["unknown"] += 1
            elif outcome == "ACCEPT_ALL_QUARANTINE":
                stats["accept_all"] += 1
            elif outcome == "UNMATCHED_RESULT":
                stats["unmatched"] += 1
            elif outcome == "ALREADY_PROCESSED":
                stats["already_processed"] += 1

        self.conn.execute(
            """
            INSERT INTO import_files
                (source_type, original_filename, file_sha256, batch_id, row_count, status)
            VALUES (?, ?, ?, ?, ?, 'COMMITTED')
            """,
            ("NEVERBOUNCE", source_filename, file_sha, batch_id, stats["total"]),
        )
        self.conn.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, new_value, reason, batch_id)
            VALUES ('BATCH', ?, 'NEVERBOUNCE_IMPORT', ?, ?, ?)
            """,
            (batch_id, json.dumps(stats), source_filename, batch_id),
        )
        return {"stats": stats, "outcomes": outcomes}
