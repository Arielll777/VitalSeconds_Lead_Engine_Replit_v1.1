"""Batch ID management — sequential Batch_N preferred."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional


class BatchService:
    def __init__(self, conn):
        self.conn = conn

    def suggest_next_id(self, prefix: str = "Batch") -> str:
        rows = self.conn.execute(
            "SELECT batch_id FROM batches WHERE batch_id LIKE ?",
            (f"{prefix}_%",),
        ).fetchall()
        max_n = 0
        for row in rows:
            bid = row["batch_id"] if hasattr(row, "keys") else row[0]
            m = re.match(rf"^{re.escape(prefix)}_(\d+)$", bid or "")
            if m:
                max_n = max(max_n, int(m.group(1)))
        if max_n > 0 or rows:
            return f"{prefix}_{max_n + 1}"
        return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    def create(
        self,
        batch_id: str,
        source_type: str,
        description: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> str:
        existing = self.conn.execute(
            "SELECT 1 FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if existing:
            raise ValueError(f"Batch ID already exists: {batch_id}")
        self.conn.execute(
            """
            INSERT INTO batches (batch_id, description, source_type, created_by)
            VALUES (?, ?, ?, ?)
            """,
            (batch_id, description, source_type, created_by),
        )
        return batch_id

    def exists(self, batch_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM batches WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        return row is not None

    def grisha_export_id(self) -> str:
        return f"Grisha_Export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
