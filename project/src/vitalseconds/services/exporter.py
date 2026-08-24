"""Export services. Pass exports use attempt_order. Unknown never mixed unless requested."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, List, Optional, Tuple

from vitalseconds.config import EXPORT_DIR, REQUIRED_BACKUP_TABLES
from vitalseconds.services.batch import BatchService


class BackupError(RuntimeError):
    pass


class ExportService:
    def __init__(self, conn, export_dir: Optional[Path] = None):
        self.conn = conn
        self.export_dir = export_dir or EXPORT_DIR
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def _write_csv(self, filename: str, headers: List[str], rows: List[List[Any]]) -> Optional[Path]:
        if not rows:
            return None
        path = self.export_dir / filename
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
        return path

    def _grisha_eligible_rows(self):
        return self.conn.execute(
            """
            SELECT cand.candidate_id, e.executive_id, e.company_id, e.first_name, e.last_name,
                   e.state, e.campaign_disposition, e.technical_status,
                   c.canonical_name, cand.email, cand.normalized_email
            FROM email_candidates cand
            JOIN executives e ON e.executive_id = cand.executive_id
            JOIN companies c ON c.company_id = e.company_id
            WHERE cand.status = 'VERIFIED_VALID'
              AND e.technical_status IN ('VERIFIED_VALID', 'PREVIOUSLY_VALID')
              AND e.campaign_disposition = 'ACTIVE'
              AND cand.normalized_email NOT IN (SELECT normalized_email FROM grisha_exports)
              AND cand.normalized_email NOT IN (SELECT normalized_email FROM grisha_export_events)
            ORDER BY c.canonical_name, e.last_name
            """
        ).fetchall()

    def preview_grisha_ready(self) -> Tuple[Optional[Path], int]:
        rows = self._grisha_eligible_rows()
        structured = [
            [r["first_name"], r["last_name"], r["state"] or "", r["canonical_name"], r["email"]]
            for r in rows
        ]
        path = self._write_csv(
            "Grisha_Ready_PREVIEW.csv",
            ["First", "Last", "State", "Company", "Email"],
            structured,
        )
        if structured:
            pipe_path = self.export_dir / "Grisha_Ready_PREVIEW_pipe.txt"
            with open(pipe_path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(
                        f"{r['first_name']} {r['last_name']} | {r['state'] or ''} | "
                        f"{r['canonical_name']} | {r['email']}\n"
                    )
        return path, len(structured)

    def confirm_grisha_export(self, batch_id: Optional[str] = None) -> Tuple[Optional[Path], int]:
        if not batch_id:
            batch_id = BatchService(self.conn).grisha_export_id()
        if not BatchService(self.conn).exists(batch_id):
            BatchService(self.conn).create(batch_id, "GRISHA_EXPORT", "Confirmed Grisha export")
        rows = self._grisha_eligible_rows()
        structured = [
            [r["first_name"], r["last_name"], r["state"] or "", r["canonical_name"], r["email"]]
            for r in rows
        ]
        path = self._write_csv(
            "Grisha_Ready.csv",
            ["First", "Last", "State", "Company", "Email"],
            structured,
        )
        if not structured:
            return path, 0
        pipe_path = self.export_dir / "Grisha_Ready_pipe.txt"
        with open(pipe_path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(
                    f"{r['first_name']} {r['last_name']} | {r['state'] or ''} | "
                    f"{r['canonical_name']} | {r['email']}\n"
                )
        for r in rows:
            self.conn.execute(
                """
                INSERT INTO grisha_export_events
                    (candidate_id, executive_id, company_id, normalized_email, email,
                     export_type, batch_id)
                VALUES (?, ?, ?, ?, ?, 'GRISHA_READY', ?)
                """,
                (
                    r["candidate_id"],
                    r["executive_id"],
                    r["company_id"],
                    r["normalized_email"],
                    r["email"],
                    batch_id,
                ),
            )
            self.conn.execute(
                """
                INSERT OR IGNORE INTO grisha_exports
                    (candidate_id, normalized_email, executive_id, batch_id)
                VALUES (?, ?, ?, ?)
                """,
                (r["candidate_id"], r["normalized_email"], r["executive_id"], batch_id),
            )
            self.conn.execute(
                """
                UPDATE executives
                SET campaign_disposition = 'EXPORTED_TO_GRISHA', updated_at = datetime('now')
                WHERE executive_id = ?
                """,
                (r["executive_id"],),
            )
        self.conn.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, new_value, reason, batch_id)
            VALUES ('EXPORT', ?, 'GRISHA_EXPORT_CONFIRMED', ?, 'Explicit Grisha export', ?)
            """,
            (batch_id, str(len(structured)), batch_id),
        )
        return path, len(structured)

    def grisha_ready(self, mark_exported: bool = False) -> Tuple[Optional[Path], int]:
        if mark_exported:
            return self.confirm_grisha_export()
        return self.preview_grisha_ready()

    def active_pass(
        self,
        pass_number: Optional[int] = None,
        include_unknown: bool = False,
    ) -> Tuple[Optional[Path], int]:
        """Normal pass = first-time verification for that attempt. Unknown excluded unless requested."""
        sql = """
            SELECT cand.email, e.first_name, e.last_name, c.canonical_name,
                   d.normalized_domain, cand.attempt_order, cand.pass_order, cand.candidate_basis,
                   e.technical_status
            FROM email_candidates cand
            JOIN executives e ON e.executive_id = cand.executive_id
            JOIN companies c ON c.company_id = e.company_id
            LEFT JOIN domains d ON d.domain_id = cand.domain_id
            WHERE cand.is_active = 1
              AND e.campaign_disposition = 'ACTIVE'
              AND e.technical_status NOT IN (
                  'EXHAUSTED_INVALID', 'VERIFIED_VALID', 'PREVIOUSLY_VALID', 'DOMAIN_QUARANTINED'
              )
        """
        params: list = []
        if not include_unknown:
            sql += " AND e.technical_status != 'UNKNOWN_PENDING_RETRY'"
            sql += " AND cand.status != 'UNKNOWN'"
        if pass_number is not None:
            sql += " AND cand.attempt_order = ?"
            params.append(pass_number)
        sql += " ORDER BY cand.attempt_order, c.canonical_name"
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        data = [
            [
                r["email"],
                r["first_name"],
                r["last_name"],
                r["canonical_name"],
                r["normalized_domain"] or "",
                r["attempt_order"],
                r["candidate_basis"],
            ]
            for r in rows
        ]
        label = f"Pass{pass_number}" if pass_number is not None else "Active"
        path = self._write_csv(
            f"NeverBounce_{label}.csv",
            ["email", "first_name", "last_name", "company", "domain", "attempt_order", "candidate_basis"],
            data,
        )
        return path, len(data)

    def unknown_retry(self) -> Tuple[Optional[Path], int]:
        rows = self.conn.execute(
            """
            SELECT cand.email, e.first_name, e.last_name, c.canonical_name
            FROM email_candidates cand
            JOIN executives e ON e.executive_id = cand.executive_id
            JOIN companies c ON c.company_id = e.company_id
            WHERE e.technical_status = 'UNKNOWN_PENDING_RETRY'
              AND cand.is_active = 1
              AND e.campaign_disposition = 'ACTIVE'
            """
        ).fetchall()
        data = [[r["email"], r["first_name"], r["last_name"], r["canonical_name"]] for r in rows]
        path = self._write_csv(
            "Unknown_Retry.csv",
            ["email", "first_name", "last_name", "company"],
            data,
        )
        return path, len(data)

    def accept_all_quarantine(self) -> Tuple[Optional[Path], int]:
        rows = self.conn.execute(
            """
            SELECT d.normalized_domain, d.quarantine_reason, d.quarantined_at, c.canonical_name
            FROM domains d
            LEFT JOIN companies c ON c.company_id = d.company_id
            WHERE d.technical_status = 'ACCEPT_ALL_QUARANTINE'
            """
        ).fetchall()
        data = [
            [r["normalized_domain"], r["canonical_name"] or "", r["quarantine_reason"] or "", r["quarantined_at"] or ""]
            for r in rows
        ]
        path = self._write_csv(
            "AcceptAll_Quarantine.csv",
            ["domain", "company", "reason", "quarantined_at"],
            data,
        )
        return path, len(data)

    def exhausted_invalid(self) -> Tuple[Optional[Path], int]:
        rows = self.conn.execute(
            """
            SELECT e.first_name, e.last_name, c.canonical_name, e.technical_status
            FROM executives e
            JOIN companies c ON c.company_id = e.company_id
            WHERE e.technical_status IN ('EXHAUSTED', 'EXHAUSTED_INVALID')
            """
        ).fetchall()
        data = [[r["first_name"], r["last_name"], r["canonical_name"], r["technical_status"]] for r in rows]
        path = self._write_csv(
            "Exhausted_Invalid.csv",
            ["first_name", "last_name", "company", "status"],
            data,
        )
        return path, len(data)

    def updated_master_hub(self) -> Tuple[Optional[Path], int]:
        rows = self.conn.execute(
            """
            SELECT
                e.first_name, e.last_name, e.title, e.state,
                c.canonical_name AS company, c.buying_group,
                d.normalized_domain AS domain,
                d.is_historical,
                e.technical_status, e.campaign_disposition, e.primary_classification,
                cand.email, cand.candidate_basis, cand.status AS email_status,
                cand.attempt_order
            FROM executives e
            JOIN companies c ON c.company_id = e.company_id
            LEFT JOIN email_candidates cand ON cand.executive_id = e.executive_id
            LEFT JOIN domains d ON d.domain_id = cand.domain_id
            ORDER BY c.canonical_name, e.last_name, cand.attempt_order
            """
        ).fetchall()
        headers = [
            "first_name", "last_name", "title", "state",
            "company", "buying_group", "domain", "is_historical",
            "technical_status", "campaign_disposition", "primary_classification",
            "email", "candidate_basis", "email_status", "attempt_order",
        ]
        data = [[r[h] if r[h] is not None else "" for h in headers] for r in rows]
        path = self._write_csv("Updated_Master_Hub.csv", headers, data)
        return path, len(data)

    def dedupe_audit(self) -> Tuple[Optional[Path], int]:
        rows = self.conn.execute(
            """
            SELECT created_at, entity_type, entity_id, action, old_value, new_value, reason, batch_id
            FROM audit_log
            ORDER BY audit_id DESC
            LIMIT 5000
            """
        ).fetchall()
        headers = ["created_at", "entity_type", "entity_id", "action", "old_value", "new_value", "reason", "batch_id"]
        data = [[r[h] or "" for h in headers] for r in rows]
        path = self._write_csv("Dedupe_Audit.csv", headers, data)
        return path, len(data)

    def full_state_backup(self) -> Tuple[Path, int]:
        """Multi-sheet XLSX. Fails if any required table cannot be exported. Never a flattened CSV."""
        from openpyxl import Workbook

        missing: List[str] = []
        wb = Workbook()
        wb.remove(wb.active)
        total = 0
        for table in REQUIRED_BACKUP_TABLES:
            try:
                rows = self.conn.execute(f"SELECT * FROM {table}").fetchall()
            except Exception as exc:
                missing.append(f"{table}: {exc}")
                continue
            ws = wb.create_sheet(title=table[:31])
            if not rows:
                ws.append(["(empty)"])
                continue
            keys = list(rows[0].keys()) if hasattr(rows[0], "keys") else []
            if keys and isinstance(keys[0], str):
                ws.append(keys)
                for r in rows:
                    ws.append([r[k] for k in keys])
            else:
                for r in rows:
                    ws.append(list(r))
            total += len(rows)
        if missing:
            raise BackupError(
                "FULL STATE BACKUP FAILED. Required table(s) could not be exported:\n"
                + "\n".join(missing)
            )
        path = self.export_dir / "VitalSeconds_Full_State_Backup.xlsx"
        wb.save(path)
        return path, total
