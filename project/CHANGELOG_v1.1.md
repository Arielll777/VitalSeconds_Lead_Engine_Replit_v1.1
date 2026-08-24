# VitalSeconds Lead Engine v1.1 — what changed vs v1

Patched in place. Business rules were not redesigned.

## Changed (audit items 1–14)

1. Production startup requires `DATABASE_URL` and `APP_PASSWORD`. Missing URL shows DATABASE CONFIGURATION ERROR and does not open the app. SQLite only with `VITALSECONDS_ALLOW_SQLITE_TEST=1`.
2. PostgreSQL integration tests live in `tests/test_postgres.py` and run only against `VITALSECONDS_PG_TEST_URL` (isolated schema). SQLite tests are not a Postgres proof.
3. Dedicated Master Workbook Import detects all sheets, warns on unrecognized sheets, requires COMMIT, and preserves historical Valid/Invalid/Unknown/Accept-All instead of rewriting them as PUBLIC_EXACT + PENDING.
4. Full State Backup fails if any required table cannot be exported. Restore is PREVIEW → VALIDATE → COMMIT with explicit confirmation.
5. `technical_status` and `campaign_disposition` are separate. Already-contacted / DNC / excluded never rewrite technical truth.
6. Buying-group dedupe is independent of company-name match. Groups are never inferred.
7. Relationship tables restored: `executive_company_roles`, `buying_groups`, `company_buying_groups`, `company_domain_relationships`, aliases.
8. Company merge marks the source `MERGED` with `merged_into_company_id`. History is kept. Dedupe follows the canonical target.
9. `attempt_order` (Pass 1/2/3) is separate from `pass_order` (pattern rank). PUBLIC_EXACT is Attempt 1 when present.
10. Normal Pass export excludes Unknown unless "Include Unknown Retries" is checked.
11. Accept-All release sets domain `RELEASED` and executives `HOLD`. Resume verification is a separate confirmed action.
12. Partial unique index: one active candidate per executive. Migration places extras in IMPORT_REVIEW; nothing is deleted. Index is applied after that cleanup.
13. Confirmed Grisha export always records `grisha_export_events` with a batch ID (`Grisha_Export_YYYYMMDD_HHMMSS` if needed). Failures abort the transaction. Preview creates no events.
14. Migration 004 does not swallow Postgres errors. SQLite duplicate-column is the only local exception.

## Stayed

- Pass 1 local-part `jsmith` (no dot), Pass 2 `john`, Pass 3 `john.smith`
- Max 3 automatic attempts
- Unknown stays on the same email
- Accept-All quarantines the exact domain only
- Append-only `verification_events`, `audit_log`, `import_source_rows`
- Preview does not mutate operational export state
- Empty exports produce a message, not an empty CSV
- Historical Master rows are preserved
- Raw leads only generate candidates for NET_NEW
