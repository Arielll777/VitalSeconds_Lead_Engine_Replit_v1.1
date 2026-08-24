# VitalSeconds Lead Engine v1.1

Deterministic, audit-first internal operations tool for the VitalSeconds B2B outbound lead pipeline (private EMS / IFT / CCT operators).

This is the **Replit + PostgreSQL** build. The operational database is PostgreSQL. SQLite is test-only and never a silent fallback.

## Production secrets (required)

Set these in Replit **Secrets** before the first run:

| Secret | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection string (Replit PostgreSQL) |
| `APP_PASSWORD` | Operator sign-in password |

If `DATABASE_URL` is missing the app shows **DATABASE CONFIGURATION ERROR** and does **not** open. If `APP_PASSWORD` is missing it shows **APP CONFIGURATION ERROR** and does **not** open.

Do not set `VITALSECONDS_ALLOW_SQLITE_TEST` on Replit.

## Browser-only Replit setup

No Python editing is required.

1. Create a new Replit Repl.
2. Upload / unzip `VitalSeconds_Lead_Engine_Replit_v1.1_FINAL.zip` into the Repl root so `app.py` and `requirements.txt` are at the top level.
3. Open **Tools → Secrets**.
4. Add `DATABASE_URL` from Replit PostgreSQL (create the Postgres database in the Repl if it is not already provisioned).
5. Add `APP_PASSWORD` (a long random password you control).
6. Click **Run**.
7. Sign in with `APP_PASSWORD`.
8. Confirm **Settings** shows `DATABASE CONNECTED · PostgreSQL`.
9. Use **Master Workbook Import** for the real 8-sheet VitalSeconds Master. Do not use a single-sheet loader for that workbook.
10. Take a **Full State Backup** from Exports after the first successful Master commit.

## Email attempt order (locked)

Pass / attempt numbers are **attempt_order**, not pattern rank.

1. Attempt 1: `PUBLIC_EXACT` when present, otherwise `jsmith@domain.com` (first initial + last, **no dot**)
2. Attempt 2: next distinct candidate (`john@domain.com` or `jsmith@domain.com` depending on Attempt 1)
3. Attempt 3: next distinct candidate (`john.smith@domain.com` when still unused)

Maximum automatic attempts = 3. No Pass 4.

Public Exact counts toward the 3 and is never duplicated as a generated permutation.

## Waterfall

| NeverBounce | Technical result |
| --- | --- |
| valid | `VERIFIED_VALID` — stop. Campaign disposition unchanged. |
| invalid | Advance to next attempt_order, max 3, then `EXHAUSTED_INVALID` |
| unknown | Stay on the same email forever. Not mixed into normal Pass exports. |
| accept_all | Quarantine the **exact** normalized domain only |

## Technical status vs campaign disposition

These are independent.

Example: `technical_status = VERIFIED_VALID` and `campaign_disposition = DO_NOT_CONTACT` is valid. That lead is excluded from Grisha.

Campaign values: `ACTIVE`, `DO_NOT_CONTACT`, `EXCLUDED`, `ALREADY_CONTACTED`, `EXPORTED_TO_GRISHA`, `HOLD`.

Already-contacted does **not** rewrite technical validity.

## Master workbook sheets

Recognized:

1. `1_Sparke_Raw_Input`
2. `2_Verified_Ready_For_Grisha`
3. `3_Live_Blacklist`
4. `4_State_Control`
5. `5_Gemini_Domain_Master`
6. `6_All_Verified_Valid`
7. `7_Unresolved_History`
8. `8_Gemini_Instructions` (detected, not imported)

Unrecognized sheets are listed as warnings and are not imported. COMMIT is required.

Historical Valid stays Valid. Unknown stays Unknown. Invalid stays Invalid. Accept-All stays exact-domain quarantine. Unmappable rows go to `HOLD` / `IMPORT_REVIEW`. Every original row is stored append-only.

## Accept-All release

Release Domain → domain `RELEASED`, executives `HOLD`. Verification does **not** auto-resume.

**Resume verification** is a separate, confirmed, audited action.

## Grisha

- **PREVIEW** writes a file and does not mark exported.
- **COMMIT** records `grisha_export_events` with a unique export batch ID (`Grisha_Export_YYYYMMDD_HHMMSS` if you leave the default). If the event cannot be recorded, the export transaction fails and leads are not marked exported.

## Backup / restore

Full State Backup is a multi-sheet XLSX of required tables. If any required table cannot be exported, the backup **fails**. A flattened CSV is never presented as a full backup.

Restore is PREVIEW → VALIDATE → COMMIT with an explicit overwrite confirmation.

## Tests

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
VITALSECONDS_ALLOW_SQLITE_TEST=1 PYTHONPATH=src pytest tests/ -v
```

PostgreSQL integration tests run only when `VITALSECONDS_PG_TEST_URL` is set to an isolated test database. They never target production data.

SQLite unit tests are **not** a PostgreSQL persistence proof.

## Local Streamlit (test only)

```bash
export VITALSECONDS_ALLOW_SQLITE_TEST=1
export APP_PASSWORD=local-test-password
PYTHONPATH=src streamlit run app.py
```

## Invariants

- Zero credit waste (one email at a time, early stop).
- Zero duplicate outreach.
- Full auditability of every decision.
- Never guess missing data → HOLD / DATA_ERROR / IMPORT_REVIEW / EXHAUSTED.
- Historical verification events, audit log, and import source rows are **append-only**.
- Never invent buying groups. Buying-group dedupe is independent of company-name match.
- Company alias merge marks the source `MERGED` and keeps history. Nothing is deleted.
- One active verification candidate per executive (database partial unique index).
