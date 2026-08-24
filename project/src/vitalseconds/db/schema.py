"""Dual SQLite/PostgreSQL schemas. CREATE IF NOT EXISTS only — never DROP operational data.

Partial unique indexes are applied in a later migration AFTER duplicate-active
rows are placed in IMPORT_REVIEW. They are intentionally absent here so an
upgrade of a dirty v1 database cannot fail the base CREATE.
"""

from __future__ import annotations

POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    company_id            SERIAL PRIMARY KEY,
    canonical_name        TEXT NOT NULL,
    normalized_name       TEXT NOT NULL,
    buying_group          TEXT,
    current_status        TEXT NOT NULL DEFAULT 'ACTIVE',
    merged_into_company_id INTEGER REFERENCES companies(company_id),
    notes                 TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS company_aliases (
    alias_id         SERIAL PRIMARY KEY,
    company_id       INTEGER NOT NULL REFERENCES companies(company_id),
    alias_name       TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'MANUAL',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS buying_groups (
    buying_group_id  SERIAL PRIMARY KEY,
    name             TEXT NOT NULL,
    normalized_name  TEXT NOT NULL UNIQUE,
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS company_buying_groups (
    company_id      INTEGER NOT NULL REFERENCES companies(company_id),
    buying_group_id INTEGER NOT NULL REFERENCES buying_groups(buying_group_id),
    source          TEXT NOT NULL DEFAULT 'MASTER',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (company_id, buying_group_id)
);

CREATE TABLE IF NOT EXISTS domains (
    domain_id         SERIAL PRIMARY KEY,
    domain            TEXT NOT NULL,
    normalized_domain TEXT NOT NULL UNIQUE,
    company_id        INTEGER REFERENCES companies(company_id),
    is_historical     INTEGER NOT NULL DEFAULT 0,
    technical_status  TEXT NOT NULL DEFAULT 'UNKNOWN',
    quarantine_reason TEXT,
    quarantined_at    TIMESTAMPTZ,
    released_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS company_domain_relationships (
    rel_id        SERIAL PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES companies(company_id),
    domain_id     INTEGER NOT NULL REFERENCES domains(domain_id),
    is_historical INTEGER NOT NULL DEFAULT 0,
    is_current    INTEGER NOT NULL DEFAULT 1,
    source        TEXT NOT NULL DEFAULT 'MASTER',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, domain_id)
);

CREATE TABLE IF NOT EXISTS executives (
    executive_id           SERIAL PRIMARY KEY,
    company_id             INTEGER NOT NULL REFERENCES companies(company_id),
    first_name             TEXT NOT NULL,
    last_name              TEXT NOT NULL,
    normalized_full_name   TEXT NOT NULL,
    title                  TEXT,
    state                  TEXT,
    technical_status       TEXT NOT NULL DEFAULT 'NET_NEW',
    campaign_disposition   TEXT NOT NULL DEFAULT 'ACTIVE',
    current_status         TEXT NOT NULL DEFAULT 'NET_NEW',
    primary_classification TEXT,
    active_candidate_id    INTEGER,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (company_id, normalized_full_name)
);

CREATE TABLE IF NOT EXISTS executive_company_roles (
    role_id      SERIAL PRIMARY KEY,
    executive_id INTEGER NOT NULL REFERENCES executives(executive_id),
    company_id   INTEGER NOT NULL REFERENCES companies(company_id),
    title        TEXT,
    is_current   INTEGER NOT NULL DEFAULT 1,
    source       TEXT NOT NULL DEFAULT 'MASTER',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (executive_id, company_id)
);

CREATE TABLE IF NOT EXISTS email_candidates (
    candidate_id     SERIAL PRIMARY KEY,
    executive_id     INTEGER NOT NULL REFERENCES executives(executive_id),
    email            TEXT NOT NULL,
    normalized_email TEXT NOT NULL,
    domain_id        INTEGER REFERENCES domains(domain_id),
    candidate_basis  TEXT NOT NULL,
    pass_order       INTEGER NOT NULL DEFAULT 0,
    attempt_order    INTEGER NOT NULL DEFAULT 0,
    is_active        INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'PENDING',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (executive_id, normalized_email),
    UNIQUE (normalized_email)
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id     TEXT PRIMARY KEY,
    description  TEXT,
    source_type  TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by   TEXT
);

CREATE TABLE IF NOT EXISTS column_mappings (
    mapping_id         SERIAL PRIMARY KEY,
    source_type        TEXT NOT NULL,
    header_fingerprint TEXT NOT NULL,
    mapping_json       TEXT NOT NULL,
    last_used_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_type, header_fingerprint)
);

CREATE TABLE IF NOT EXISTS import_files (
    file_id            SERIAL PRIMARY KEY,
    source_type        TEXT NOT NULL,
    original_filename  TEXT,
    file_sha256        TEXT NOT NULL,
    byte_size          INTEGER,
    batch_id           TEXT,
    row_count          INTEGER,
    status             TEXT NOT NULL DEFAULT 'COMMITTED',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS import_source_rows (
    row_id      SERIAL PRIMARY KEY,
    batch_id    TEXT NOT NULL REFERENCES batches(batch_id),
    source_type TEXT NOT NULL,
    raw_json    TEXT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fingerprint TEXT,
    sheet_name  TEXT
);

CREATE TABLE IF NOT EXISTS verification_events (
    event_id                SERIAL PRIMARY KEY,
    candidate_id            INTEGER REFERENCES email_candidates(candidate_id),
    executive_id            INTEGER REFERENCES executives(executive_id),
    email                   TEXT NOT NULL,
    normalized_email        TEXT NOT NULL,
    neverbounce_status      TEXT NOT NULL,
    raw_result_json         TEXT,
    batch_id                TEXT REFERENCES batches(batch_id),
    idempotency_fingerprint TEXT NOT NULL UNIQUE,
    processed_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_file_sha256      TEXT,
    source_row_number       INTEGER,
    source_type             TEXT,
    verifier                TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id     SERIAL PRIMARY KEY,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT,
    action       TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT,
    reason       TEXT,
    batch_id     TEXT,
    user_context TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS manual_overrides (
    override_id   SERIAL PRIMARY KEY,
    entity_type   TEXT NOT NULL,
    entity_id     TEXT,
    override_type TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    reason        TEXT NOT NULL,
    batch_id      TEXT,
    user_context  TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS grisha_exports (
    export_id        SERIAL PRIMARY KEY,
    candidate_id     INTEGER NOT NULL REFERENCES email_candidates(candidate_id),
    normalized_email TEXT NOT NULL UNIQUE,
    executive_id     INTEGER REFERENCES executives(executive_id),
    batch_id         TEXT,
    exported_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS grisha_export_events (
    event_id           SERIAL PRIMARY KEY,
    candidate_id       INTEGER REFERENCES email_candidates(candidate_id),
    executive_id       INTEGER REFERENCES executives(executive_id),
    company_id         INTEGER REFERENCES companies(company_id),
    normalized_email   TEXT NOT NULL,
    email              TEXT NOT NULL,
    export_type        TEXT NOT NULL DEFAULT 'GRISHA_READY',
    batch_id           TEXT NOT NULL,
    verification_batch TEXT,
    exported_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_domains_company ON domains(company_id);
CREATE INDEX IF NOT EXISTS idx_executives_company ON executives(company_id);
CREATE INDEX IF NOT EXISTS idx_executives_tech ON executives(technical_status);
CREATE INDEX IF NOT EXISTS idx_executives_camp ON executives(campaign_disposition);
CREATE INDEX IF NOT EXISTS idx_candidates_executive ON email_candidates(executive_id);
CREATE INDEX IF NOT EXISTS idx_candidates_attempt ON email_candidates(attempt_order);
CREATE INDEX IF NOT EXISTS idx_verification_email ON verification_events(normalized_email);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_grisha_events_email ON grisha_export_events(normalized_email);
"""

SQLITE_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    company_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name         TEXT NOT NULL,
    normalized_name        TEXT NOT NULL,
    buying_group           TEXT,
    current_status         TEXT NOT NULL DEFAULT 'ACTIVE',
    merged_into_company_id INTEGER REFERENCES companies(company_id),
    notes                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS company_aliases (
    alias_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       INTEGER NOT NULL REFERENCES companies(company_id),
    alias_name       TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'MANUAL',
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS buying_groups (
    buying_group_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS company_buying_groups (
    company_id      INTEGER NOT NULL REFERENCES companies(company_id),
    buying_group_id INTEGER NOT NULL REFERENCES buying_groups(buying_group_id),
    source          TEXT NOT NULL DEFAULT 'MASTER',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (company_id, buying_group_id)
);

CREATE TABLE IF NOT EXISTS domains (
    domain_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    domain            TEXT NOT NULL,
    normalized_domain TEXT NOT NULL UNIQUE,
    company_id        INTEGER REFERENCES companies(company_id),
    is_historical     INTEGER NOT NULL DEFAULT 0,
    technical_status  TEXT NOT NULL DEFAULT 'UNKNOWN',
    quarantine_reason TEXT,
    quarantined_at    TEXT,
    released_at       TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS company_domain_relationships (
    rel_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id    INTEGER NOT NULL REFERENCES companies(company_id),
    domain_id     INTEGER NOT NULL REFERENCES domains(domain_id),
    is_historical INTEGER NOT NULL DEFAULT 0,
    is_current    INTEGER NOT NULL DEFAULT 1,
    source        TEXT NOT NULL DEFAULT 'MASTER',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, domain_id)
);

CREATE TABLE IF NOT EXISTS executives (
    executive_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id             INTEGER NOT NULL REFERENCES companies(company_id),
    first_name             TEXT NOT NULL,
    last_name              TEXT NOT NULL,
    normalized_full_name   TEXT NOT NULL,
    title                  TEXT,
    state                  TEXT,
    technical_status       TEXT NOT NULL DEFAULT 'NET_NEW',
    campaign_disposition   TEXT NOT NULL DEFAULT 'ACTIVE',
    current_status         TEXT NOT NULL DEFAULT 'NET_NEW',
    primary_classification TEXT,
    active_candidate_id    INTEGER,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at             TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, normalized_full_name)
);

CREATE TABLE IF NOT EXISTS executive_company_roles (
    role_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    executive_id INTEGER NOT NULL REFERENCES executives(executive_id),
    company_id   INTEGER NOT NULL REFERENCES companies(company_id),
    title        TEXT,
    is_current   INTEGER NOT NULL DEFAULT 1,
    source       TEXT NOT NULL DEFAULT 'MASTER',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (executive_id, company_id)
);

CREATE TABLE IF NOT EXISTS email_candidates (
    candidate_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    executive_id     INTEGER NOT NULL REFERENCES executives(executive_id),
    email            TEXT NOT NULL,
    normalized_email TEXT NOT NULL,
    domain_id        INTEGER REFERENCES domains(domain_id),
    candidate_basis  TEXT NOT NULL,
    pass_order       INTEGER NOT NULL DEFAULT 0,
    attempt_order    INTEGER NOT NULL DEFAULT 0,
    is_active        INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'PENDING',
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (executive_id, normalized_email),
    UNIQUE (normalized_email)
);

CREATE TABLE IF NOT EXISTS batches (
    batch_id     TEXT PRIMARY KEY,
    description  TEXT,
    source_type  TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    created_by   TEXT
);

CREATE TABLE IF NOT EXISTS column_mappings (
    mapping_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type        TEXT NOT NULL,
    header_fingerprint TEXT NOT NULL,
    mapping_json       TEXT NOT NULL,
    last_used_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_type, header_fingerprint)
);

CREATE TABLE IF NOT EXISTS import_files (
    file_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type       TEXT NOT NULL,
    original_filename TEXT,
    file_sha256       TEXT NOT NULL,
    byte_size         INTEGER,
    batch_id          TEXT,
    row_count         INTEGER,
    status            TEXT NOT NULL DEFAULT 'COMMITTED',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS import_source_rows (
    row_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    TEXT NOT NULL REFERENCES batches(batch_id),
    source_type TEXT NOT NULL,
    raw_json    TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    fingerprint TEXT,
    sheet_name  TEXT
);

CREATE TABLE IF NOT EXISTS verification_events (
    event_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id            INTEGER REFERENCES email_candidates(candidate_id),
    executive_id            INTEGER REFERENCES executives(executive_id),
    email                   TEXT NOT NULL,
    normalized_email        TEXT NOT NULL,
    neverbounce_status      TEXT NOT NULL,
    raw_result_json         TEXT,
    batch_id                TEXT REFERENCES batches(batch_id),
    idempotency_fingerprint TEXT NOT NULL UNIQUE,
    processed_at            TEXT NOT NULL DEFAULT (datetime('now')),
    source_file_sha256      TEXT,
    source_row_number       INTEGER,
    source_type             TEXT,
    verifier                TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT,
    action       TEXT NOT NULL,
    old_value    TEXT,
    new_value    TEXT,
    reason       TEXT,
    batch_id     TEXT,
    user_context TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS manual_overrides (
    override_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT NOT NULL,
    entity_id     TEXT,
    override_type TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    reason        TEXT NOT NULL,
    batch_id      TEXT,
    user_context  TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS grisha_exports (
    export_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id     INTEGER NOT NULL REFERENCES email_candidates(candidate_id),
    normalized_email TEXT NOT NULL UNIQUE,
    executive_id     INTEGER REFERENCES executives(executive_id),
    batch_id         TEXT,
    exported_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS grisha_export_events (
    event_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id       INTEGER REFERENCES email_candidates(candidate_id),
    executive_id       INTEGER REFERENCES executives(executive_id),
    company_id         INTEGER REFERENCES companies(company_id),
    normalized_email   TEXT NOT NULL,
    email              TEXT NOT NULL,
    export_type        TEXT NOT NULL DEFAULT 'GRISHA_READY',
    batch_id           TEXT NOT NULL,
    verification_batch TEXT,
    exported_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_domains_company ON domains(company_id);
CREATE INDEX IF NOT EXISTS idx_executives_company ON executives(company_id);
CREATE INDEX IF NOT EXISTS idx_executives_tech ON executives(technical_status);
CREATE INDEX IF NOT EXISTS idx_executives_camp ON executives(campaign_disposition);
CREATE INDEX IF NOT EXISTS idx_candidates_executive ON email_candidates(executive_id);
CREATE INDEX IF NOT EXISTS idx_candidates_attempt ON email_candidates(attempt_order);
CREATE INDEX IF NOT EXISTS idx_verification_email ON verification_events(normalized_email);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_grisha_events_email ON grisha_export_events(normalized_email);
"""

SCHEMA_SQL = SQLITE_SCHEMA_SQL
