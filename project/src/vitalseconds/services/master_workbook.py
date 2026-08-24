"""VitalSeconds Master Workbook (multi-sheet XLSX) import.

Never silently reads only sheet 1. Preview lists every sheet. COMMIT required.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from vitalseconds.services.importer import MasterImporter, suggest_mapping


KNOWN_SHEETS = {
    "1_Sparke_Raw_Input": "RAW_MASTER",
    "2_Verified_Ready_For_Grisha": "GRISHA_READY",
    "3_Live_Blacklist": "BLACKLIST",
    "4_State_Control": "STATE_CONTROL",
    "5_Gemini_Domain_Master": "DOMAIN_MASTER",
    "6_All_Verified_Valid": "VERIFIED_VALID",
    "7_Unresolved_History": "UNRESOLVED",
    "8_Gemini_Instructions": "INSTRUCTIONS",
}

SKIP_SHEETS = {"INSTRUCTIONS"}


def _normalize_sheet_name(name: str) -> str:
    return (name or "").strip()


def inspect_workbook(content: bytes, filename: str = "master.xlsx") -> Dict[str, Any]:
    """Detect all sheets, row counts, recognition, suggested mappings. No DB writes."""
    xl = pd.ExcelFile(BytesIO(content))
    sheets: List[Dict[str, Any]] = []
    for raw_name in xl.sheet_names:
        name = _normalize_sheet_name(raw_name)
        kind = KNOWN_SHEETS.get(name)
        if kind is None:
            # fuzzy: prefix match
            for known, k in KNOWN_SHEETS.items():
                if name.lower().startswith(known.lower()[:8]) or known.lower() in name.lower():
                    kind = k
                    break
        df = xl.parse(raw_name, dtype=str)
        df = df.fillna("")
        headers = [str(c) for c in df.columns]
        recognized = kind is not None
        skip = kind in SKIP_SHEETS
        mapping = suggest_mapping(headers) if recognized and not skip else {}
        sheets.append(
            {
                "sheet_name": name,
                "recognized": recognized,
                "kind": kind or "UNRECOGNIZED",
                "skip": skip,
                "will_import": recognized and not skip,
                "row_count": int(len(df)),
                "headers": headers,
                "suggested_mapping": mapping,
                "warning": None if recognized else "Unrecognized sheet — will NOT be imported unless mapped as data.",
            }
        )
    return {
        "filename": filename,
        "sheet_count": len(sheets),
        "sheets": sheets,
        "importable_rows": sum(s["row_count"] for s in sheets if s["will_import"]),
        "unrecognized": [s["sheet_name"] for s in sheets if not s["recognized"]],
    }


def preview_sheet_rows(content: bytes, sheet_name: str, limit: int = 20) -> pd.DataFrame:
    xl = pd.ExcelFile(BytesIO(content))
    match = None
    for n in xl.sheet_names:
        if _normalize_sheet_name(n) == sheet_name:
            match = n
            break
    if match is None:
        return pd.DataFrame()
    df = xl.parse(match, dtype=str).fillna("")
    return df.head(limit)


def commit_workbook(
    conn,
    content: bytes,
    filename: str,
    batch_id: str,
    file_sha256: str,
) -> Dict[str, Any]:
    """COMMIT multi-sheet Master. Transaction is caller's responsibility."""
    inspection = inspect_workbook(content, filename)
    importer = MasterImporter(conn)
    sheet_stats: List[Dict[str, Any]] = []
    xl = pd.ExcelFile(BytesIO(content))
    name_map = {_normalize_sheet_name(n): n for n in xl.sheet_names}

    for spec in inspection["sheets"]:
        if not spec["will_import"]:
            sheet_stats.append({"sheet": spec["sheet_name"], "imported": False, "reason": spec["kind"]})
            continue
        raw_name = name_map[spec["sheet_name"]]
        df = xl.parse(raw_name, dtype=str).fillna("")
        mapping = spec["suggested_mapping"] or suggest_mapping(list(df.columns))
        # Force campaign/technical from known sheet kind when mapping lacks status
        rows = importer.apply_mapping(df, mapping)
        kind = spec["kind"]
        for r in rows:
            if kind == "GRISHA_READY":
                r.setdefault("master_status", "VERIFIED_VALID")
                r.setdefault("send_disposition", "EXPORTED_TO_GRISHA")
            elif kind == "BLACKLIST":
                r.setdefault("master_status", r.get("master_status") or "")
                r.setdefault("send_disposition", "DO_NOT_CONTACT")
            elif kind == "VERIFIED_VALID":
                r.setdefault("master_status", "VERIFIED_VALID")
            elif kind == "UNRESOLVED":
                r.setdefault("master_status", r.get("master_status") or "UNKNOWN_RETRY")
        stats = importer.import_rows(
            rows,
            batch_id,
            description=f"Master workbook sheet {spec['sheet_name']}",
            sheet_name=spec["sheet_name"],
            source_type="MASTER_WORKBOOK",
        )
        sheet_stats.append({"sheet": spec["sheet_name"], "imported": True, "stats": stats, "kind": kind})

    conn.execute(
        """
        INSERT INTO import_files
            (source_type, original_filename, file_sha256, batch_id, row_count, status)
        VALUES (?, ?, ?, ?, ?, 'COMMITTED')
        """,
        (
            "MASTER_WORKBOOK",
            filename,
            file_sha256,
            batch_id,
            inspection["importable_rows"],
        ),
    )
    return {"inspection": inspection, "sheet_stats": sheet_stats}
