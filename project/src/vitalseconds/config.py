"""Configuration for VitalSeconds Lead Engine v1.1."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

DATABASE_URL: str = (os.getenv("DATABASE_URL") or "").strip()
APP_PASSWORD: str = (os.getenv("APP_PASSWORD") or "").strip()

# SQLite is NEVER a silent production fallback.
# Tests/local-only: VITALSECONDS_ALLOW_SQLITE_TEST=1
ALLOW_SQLITE_TEST: bool = (os.getenv("VITALSECONDS_ALLOW_SQLITE_TEST") or "").strip() == "1"

DEFAULT_EXPORT_DIR = "data/exports"
EXPORT_DIR: Path = Path(os.getenv("VITALSECONDS_EXPORT_DIR", DEFAULT_EXPORT_DIR))
if not EXPORT_DIR.is_absolute():
    EXPORT_DIR = _PROJECT_ROOT / EXPORT_DIR
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

_SQLITE_FALLBACK = _PROJECT_ROOT / "data" / "vitalseconds_test.db"

CANONICAL_FIELDS = {
    "first_name",
    "last_name",
    "company",
    "domain",
    "email",
    "public_email",
    "title",
    "state",
    "buying_group",
    "historical_domain",
    "notes",
}

# Pattern rank (not attempt order):
# 1 flast jsmith  2 first  3 first.last
PASS_ORDER = [
    ("UNVERIFIED_FLAST_PATTERN", 1),
    ("UNVERIFIED_FIRST_PATTERN", 2),
    ("UNVERIFIED_FIRST_LAST_PATTERN", 3),
]

CLASSIFICATION_PRECEDENCE = [
    "PREVIOUSLY_VALID",
    "DOMAIN_QUARANTINED",
    "UNKNOWN_PENDING_RETRY",
    "EXHAUSTED",
    "EXHAUSTED_INVALID",
    "EXECUTIVE_DUPLICATE",
    "BUYING_GROUP_DUPLICATE",
    "EXACT_DUPLICATE",
    "COMPANY_DUPLICATE",
    "HOLD",
    "IMPORT_REVIEW",
    "DATA_ERROR",
    "NET_NEW",
]

MAX_AUTOMATIC_ATTEMPTS = 3
USING_POSTGRES = bool(DATABASE_URL)

# Production UI requires both secrets.
PRODUCTION_READY = bool(DATABASE_URL) and bool(APP_PASSWORD)

REQUIRED_BACKUP_TABLES = [
    "schema_version",
    "companies",
    "company_aliases",
    "buying_groups",
    "company_buying_groups",
    "domains",
    "company_domain_relationships",
    "executives",
    "executive_company_roles",
    "email_candidates",
    "batches",
    "column_mappings",
    "import_files",
    "import_source_rows",
    "verification_events",
    "audit_log",
    "manual_overrides",
    "grisha_exports",
    "grisha_export_events",
]
