"""
VitalSeconds Lead Engine v1.1 — Streamlit UI

Deterministic, audit-first internal operations tool.
Production requires DATABASE_URL and APP_PASSWORD.
"""

from __future__ import annotations

import hmac
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vitalseconds import __version__  # noqa: E402
from vitalseconds.config import (  # noqa: E402
    ALLOW_SQLITE_TEST,
    APP_PASSWORD,
    DATABASE_URL,
    EXPORT_DIR,
    PRODUCTION_READY,
    USING_POSTGRES,
)
from vitalseconds.db.session import (  # noqa: E402
    DatabaseConfigError,
    check_db_health,
    get_connection,
    init_db,
    query_df,
    scalar,
    transaction,
)
from vitalseconds.services.batch import BatchService  # noqa: E402
from vitalseconds.services.exporter import BackupError, ExportService  # noqa: E402
from vitalseconds.services.importer import (  # noqa: E402
    MasterImporter,
    NeverBounceImporter,
    RawLeadsImporter,
    suggest_mapping,
)
from vitalseconds.services.master_workbook import (  # noqa: E402
    commit_workbook,
    inspect_workbook,
    preview_sheet_rows,
)
from vitalseconds.services.override import OverrideService  # noqa: E402
from vitalseconds.services.restore import (  # noqa: E402
    RestoreError,
    commit_restore,
    preview_backup,
    validate_backup,
)
from vitalseconds.utils.fingerprint import make_file_sha256  # noqa: E402

st.set_page_config(
    page_title="VitalSeconds Lead Engine",
    page_icon="VS",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container { padding-top: 1.4rem; max-width: 1400px; }
      div[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
      .vs-banner { padding: 0.9rem 1.1rem; border-radius: 8px; border: 1px solid #7f1d1d;
                   background: #450a0a; color: #fecaca; font-weight: 600; }
      .vs-ok { padding: 0.6rem 0.9rem; border-radius: 8px; border: 1px solid #14532d;
               background: #052e16; color: #bbf7d0; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _config_gate() -> None:
    if not APP_PASSWORD:
        st.title("APP CONFIGURATION ERROR")
        st.error("APP_PASSWORD is required.")
        st.caption("Set the APP_PASSWORD secret, then restart. The operational application will not open.")
        st.stop()
    if not DATABASE_URL and not ALLOW_SQLITE_TEST:
        st.title("DATABASE CONFIGURATION ERROR")
        st.error("DATABASE_URL is required.")
        st.caption("Set the DATABASE_URL PostgreSQL secret. SQLite is not a production fallback.")
        st.stop()


def _login_gate() -> None:
    if st.session_state.get("authenticated"):
        return
    st.title("VitalSeconds Lead Engine")
    st.caption(f"v{__version__} · operator sign-in required")
    pwd = st.text_input("Operator password", type="password")
    if st.button("Sign in", type="primary"):
        if hmac.compare_digest(str(pwd), str(APP_PASSWORD)):
            st.session_state.authenticated = True
            st.rerun()
        st.error("Invalid password.")
    if ALLOW_SQLITE_TEST and not DATABASE_URL:
        st.warning("Test/local SQLite mode is enabled (VITALSECONDS_ALLOW_SQLITE_TEST=1). Production Replit must use DATABASE_URL.")
    st.stop()


_config_gate()
_login_gate()


@st.cache_resource
def bootstrap_db():
    init_db()
    return True


try:
    bootstrap_db()
except DatabaseConfigError as exc:
    st.title("DATABASE CONFIGURATION ERROR")
    st.error(str(exc))
    st.stop()
except Exception as exc:  # noqa: BLE001
    st.title("DATABASE ERROR")
    st.exception(exc)
    st.stop()


def get_conn():
    return get_connection()


st.sidebar.title("VitalSeconds")
st.sidebar.caption(f"Lead Engine v{__version__}")
health = check_db_health()
if health.get("ok"):
    backend_label = "PostgreSQL" if health.get("using_postgres") else "SQLite (test only)"
    st.sidebar.success(f"DB: {backend_label}")
else:
    st.sidebar.error(health.get("error") or health.get("message") or "DATABASE ERROR")

if st.sidebar.button("Logout"):
    st.session_state.authenticated = False
    st.rerun()

page = st.sidebar.radio(
    "Navigation",
    [
        "Dashboard",
        "Master Workbook Import",
        "Import Master (single table)",
        "Import Raw Leads",
        "Export Verification Pass",
        "Import NeverBounce Results",
        "Exports",
        "Restore Full State Backup",
        "Manual Override",
        "Settings",
    ],
)
st.sidebar.markdown("---")
st.sidebar.caption("Zero credit waste · Zero duplicate outreach · Full audit")


def read_upload(uploaded) -> pd.DataFrame:
    name = uploaded.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded, dtype=str, keep_default_na=False)
    if name.endswith((".xlsx", ".xls")):
        xl = pd.ExcelFile(uploaded)
        if len(xl.sheet_names) > 1:
            st.warning(
                "This workbook has multiple sheets. Use **Master Workbook Import** "
                "so every sheet is detected. This page reads sheet 1 only and will not "
                "silently treat it as the full Master."
            )
        return pd.read_excel(uploaded, dtype=str, keep_default_na=False)
    st.error("Unsupported file type. Use CSV or XLSX.")
    return pd.DataFrame()


def mapping_ui(df: pd.DataFrame, source_type: str, conn) -> dict:
    headers = list(df.columns)
    importer = MasterImporter(conn)
    saved = importer.load_mapping(source_type, headers)
    suggested = saved or suggest_mapping(headers)
    st.subheader("Column Mapping")
    st.caption("Required for lead rows: first_name, last_name, company, domain.")
    canonical_options = [
        "(ignore)",
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
        "neverbounce_status",
        "master_status",
        "send_disposition",
        "blacklist_type",
        "invalid_emails",
        "risky_or_unknown_emails",
        "unverified_candidate_emails",
        "next_verification_email",
    ]
    mapping = {}
    cols = st.columns(2)
    for i, h in enumerate(headers):
        default = suggested.get(h, "(ignore)")
        if default not in canonical_options:
            default = "(ignore)"
        with cols[i % 2]:
            choice = st.selectbox(
                f"`{h}` →",
                canonical_options,
                index=canonical_options.index(default),
                key=f"map_{source_type}_{h}",
            )
            if choice != "(ignore)":
                mapping[h] = choice
    return mapping


if page == "Dashboard":
    st.title("Dashboard")
    conn = get_conn()
    try:
        companies = scalar(conn, "SELECT COUNT(*) AS n FROM companies") or 0
        executives = scalar(conn, "SELECT COUNT(*) AS n FROM executives") or 0
        domains = scalar(conn, "SELECT COUNT(*) AS n FROM domains") or 0
        active = scalar(conn, "SELECT COUNT(*) AS n FROM email_candidates WHERE is_active = 1") or 0
        valid = scalar(conn, "SELECT COUNT(*) AS n FROM email_candidates WHERE status = 'VERIFIED_VALID'") or 0
        quarantined = scalar(
            conn, "SELECT COUNT(*) AS n FROM domains WHERE technical_status = 'ACCEPT_ALL_QUARANTINE'"
        ) or 0
        batches = scalar(conn, "SELECT COUNT(*) AS n FROM batches") or 0
        c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
        c1.metric("Companies", companies)
        c2.metric("Executives", executives)
        c3.metric("Domains", domains)
        c4.metric("Active candidates", active)
        c5.metric("Verified valid", valid)
        c6.metric("Quarantined domains", quarantined)
        c7.metric("Batches", batches)

        left, right = st.columns(2)
        with left:
            st.subheader("Technical status")
            tech_df = query_df(
                conn,
                """
                SELECT technical_status, COUNT(*) AS cnt
                FROM executives GROUP BY technical_status ORDER BY cnt DESC
                """,
            )
            st.dataframe(tech_df if not tech_df.empty else pd.DataFrame({"technical_status": [], "cnt": []}),
                         use_container_width=True)
        with right:
            st.subheader("Campaign disposition")
            camp_df = query_df(
                conn,
                """
                SELECT campaign_disposition, COUNT(*) AS cnt
                FROM executives GROUP BY campaign_disposition ORDER BY cnt DESC
                """,
            )
            st.dataframe(camp_df if not camp_df.empty else pd.DataFrame({"campaign_disposition": [], "cnt": []}),
                         use_container_width=True)

        st.subheader("Recent batches")
        st.dataframe(
            query_df(
                conn,
                "SELECT batch_id, source_type, description, created_at FROM batches ORDER BY created_at DESC LIMIT 20",
            ),
            use_container_width=True,
        )
    finally:
        conn.close()

elif page == "Master Workbook Import":
    st.title("VitalSeconds Master Workbook Import")
    st.markdown(
        "Dedicated multi-sheet import. **Every sheet is detected.** Unrecognized sheets are warned, "
        "never silently imported as sheet 1. Historical Valid / Invalid / Unknown / Accept-All "
        "statuses are preserved. COMMIT is required."
    )
    uploaded = st.file_uploader("Master workbook (XLSX)", type=["xlsx"])
    if uploaded:
        content = uploaded.getvalue()
        inspection = inspect_workbook(content, uploaded.name)
        st.write(f"**Sheets detected:** {inspection['sheet_count']} · **Importable rows:** {inspection['importable_rows']}")
        if inspection["unrecognized"]:
            st.warning("Unrecognized sheets (will NOT be imported): " + ", ".join(inspection["unrecognized"]))
        sheet_rows = []
        for s in inspection["sheets"]:
            sheet_rows.append(
                {
                    "sheet": s["sheet_name"],
                    "recognized": s["recognized"],
                    "kind": s["kind"],
                    "will_import": s["will_import"],
                    "row_count": s["row_count"],
                    "warning": s.get("warning") or "",
                }
            )
        st.dataframe(pd.DataFrame(sheet_rows), use_container_width=True)

        pick = st.selectbox("Preview sheet rows", [s["sheet_name"] for s in inspection["sheets"]])
        if pick:
            st.dataframe(preview_sheet_rows(content, pick, limit=15), use_container_width=True)

        conn = get_conn()
        try:
            batch_svc = BatchService(conn)
            suggested = batch_svc.suggest_next_id()
            batch_id = st.text_input("Batch ID", value=suggested, key="wb_batch")
            st.checkbox("I have reviewed the sheet list and mappings. COMMIT will write operational state.", key="wb_ack")
            if st.button("COMMIT Master Workbook", type="primary"):
                if not st.session_state.get("wb_ack"):
                    st.error("Confirm the preview checkbox before COMMIT.")
                elif batch_svc.exists(batch_id):
                    st.error(f"Batch ID already exists: {batch_id}")
                else:
                    sha = make_file_sha256(content)
                    with transaction() as tconn:
                        result = commit_workbook(tconn, content, uploaded.name, batch_id, sha)
                    st.success("Workbook committed.")
                    st.json(result["sheet_stats"])
        finally:
            conn.close()

elif page == "Import Master (single table)":
    st.title("Import Master (single table)")
    st.caption("CSV or a single mapped table. Multi-sheet workbooks belong on Master Workbook Import.")
    uploaded = st.file_uploader("Master file", type=["csv", "xlsx"])
    if uploaded:
        df = read_upload(uploaded)
        if not df.empty:
            st.write(f"Preview ({len(df)} rows):")
            st.dataframe(df.head(20), use_container_width=True)
            conn = get_conn()
            try:
                mapping = mapping_ui(df, "MASTER", conn)
                batch_svc = BatchService(conn)
                batch_id = st.text_input("Batch ID", value=batch_svc.suggest_next_id())
                description = st.text_input("Description (optional)", value="Master seed")
                st.checkbox("PREVIEW reviewed — COMMIT this mapping", key="master_ack")
                if st.button("COMMIT Master Import", type="primary"):
                    required = {"first_name", "last_name", "company", "domain"}
                    mapped_fields = set(mapping.values())
                    if not st.session_state.get("master_ack"):
                        st.error("Confirm the preview checkbox before COMMIT.")
                    elif not required.issubset(mapped_fields):
                        st.error(f"Missing required mappings: {required - mapped_fields}")
                    elif batch_svc.exists(batch_id):
                        st.error(f"Batch ID already exists: {batch_id}")
                    else:
                        with transaction() as tconn:
                            imp = MasterImporter(tconn)
                            imp.save_mapping("MASTER", list(df.columns), mapping)
                            rows = imp.apply_mapping(df, mapping)
                            stats = imp.import_rows(rows, batch_id, description)
                        st.success(f"Master imported: {stats}")
            finally:
                conn.close()

elif page == "Import Raw Leads":
    st.title("Import Raw Leads")
    st.markdown("Only **NET_NEW** records receive email candidates. Buying-group match is independent of company name.")
    uploaded = st.file_uploader("Raw Leads file", type=["csv", "xlsx"])
    if uploaded:
        df = read_upload(uploaded)
        if not df.empty:
            st.write(f"Preview ({len(df)} rows):")
            st.dataframe(df.head(20), use_container_width=True)
            conn = get_conn()
            try:
                mapping = mapping_ui(df, "RAW_LEADS", conn)
                batch_svc = BatchService(conn)
                batch_id = st.text_input("Batch ID", value=batch_svc.suggest_next_id(), key="raw_batch")
                description = st.text_input("Description", value="Raw leads import", key="raw_desc")
                generate = st.checkbox("Generate email candidates for NET_NEW", value=True)
                st.checkbox("PREVIEW reviewed — COMMIT classification", key="raw_ack")
                if st.button("COMMIT Classify & Import", type="primary"):
                    required = {"first_name", "last_name", "company", "domain"}
                    mapped_fields = set(mapping.values())
                    if not st.session_state.get("raw_ack"):
                        st.error("Confirm the preview checkbox before COMMIT.")
                    elif not required.issubset(mapped_fields):
                        st.error(f"Missing required mappings: {required - mapped_fields}")
                    elif batch_svc.exists(batch_id):
                        st.error(f"Batch ID already exists: {batch_id}")
                    else:
                        with transaction() as tconn:
                            imp = RawLeadsImporter(tconn)
                            imp.save_mapping("RAW_LEADS", list(df.columns), mapping)
                            rows = imp.apply_mapping(df, mapping)
                            result = imp.import_and_classify(
                                rows, batch_id, description, generate_candidates=generate
                            )
                        st.success(f"Stats: {result['stats']}")
                        summary = {}
                        for r in result["results"]:
                            p = r["classification"]["primary_classification"]
                            summary[p] = summary.get(p, 0) + 1
                        st.write("Primary classifications:")
                        st.json(summary)
            finally:
                conn.close()

elif page == "Export Verification Pass":
    st.title("Export Verification Pass")
    st.markdown(
        "Exports the **single active candidate** per eligible executive. "
        "**Pass N = attempt_order N** (the Nth distinct address we actually verify). "
        "Unknown addresses are excluded unless you explicitly include them."
    )
    conn = get_conn()
    try:
        exporter = ExportService(conn)
        pass_opt = st.selectbox("Filter by attempt / pass", ["All active (new attempts)", "Pass 1", "Pass 2", "Pass 3"])
        pass_num = None if pass_opt.startswith("All") else int(pass_opt.split()[-1])
        include_unknown = st.checkbox("Include Unknown Retries", value=False)
        if st.button("Generate NeverBounce Pass CSV", type="primary"):
            path, count = exporter.active_pass(pass_num, include_unknown=include_unknown)
            if count == 0:
                st.info("No active candidates require first-time verification for this pass.")
            else:
                st.success(f"Wrote {count} rows → {path.name}")
                with open(path, "rb") as f:
                    st.download_button("Download CSV", f, file_name=path.name, mime="text/csv")
        st.markdown("---")
        if st.button("Export Unknown Retry only"):
            path, count = exporter.unknown_retry()
            if count == 0:
                st.info("No unknown-retry addresses.")
            else:
                st.success(f"Wrote {count} rows")
                with open(path, "rb") as f:
                    st.download_button("Download Unknown Retry", f, file_name=path.name, mime="text/csv")
    finally:
        conn.close()

elif page == "Import NeverBounce Results":
    st.title("Import NeverBounce Results")
    st.markdown(
        "Matched **strictly by full normalized email**. "
        "valid → stop · invalid → next attempt · unknown → stay · accept_all → exact-domain quarantine. "
        "Idempotency is file SHA-256 + row number, not email+status alone."
    )
    uploaded = st.file_uploader("NeverBounce results", type=["csv", "xlsx"])
    if uploaded:
        content = uploaded.getvalue()
        sha = make_file_sha256(content)
        uploaded.seek(0)
        df = read_upload(uploaded)
        if not df.empty:
            st.caption(f"File SHA-256: `{sha}`")
            st.dataframe(df.head(20), use_container_width=True)
            conn = get_conn()
            try:
                headers = list(df.columns)
                mapping = mapping_ui(df, "NEVERBOUNCE", conn)
                batch_svc = BatchService(conn)
                batch_id = st.text_input("Batch ID", value=batch_svc.suggest_next_id(), key="nb_batch")
                st.checkbox("PREVIEW reviewed — COMMIT waterfall", key="nb_ack")
                if st.button("COMMIT Process Results", type="primary"):
                    if not st.session_state.get("nb_ack"):
                        st.error("Confirm the preview checkbox before COMMIT.")
                    else:
                        lower = {c.lower(): c for c in df.columns}
                        if "email" not in mapping.values() and "email" in lower:
                            mapping = {lower["email"]: "email"}
                            if "status" in lower:
                                mapping[lower["status"]] = "neverbounce_status"
                            elif "result" in lower:
                                mapping[lower["result"]] = "neverbounce_status"
                        with transaction() as tconn:
                            imp = NeverBounceImporter(tconn)
                            imp.save_mapping("NEVERBOUNCE", headers, mapping)
                            rows = imp.apply_mapping(df, mapping)
                            if not rows or not any(r.get("email") for r in rows):
                                rows = df.to_dict(orient="records")
                            result = imp.process_results(
                                rows,
                                batch_id,
                                source_filename=uploaded.name,
                                source_file_sha256=sha,
                            )
                        st.success(f"Outcomes: {result['stats']}")
                        st.json(result["stats"])
            finally:
                conn.close()

elif page == "Exports":
    st.title("Exports")
    st.markdown("Empty result sets produce a message only — never an empty CSV. Grisha preview does **not** mark exported.")
    conn = get_conn()
    try:
        exporter = ExportService(conn)
        st.subheader("Grisha Ready")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("PREVIEW Grisha Ready"):
                path, count = exporter.preview_grisha_ready()
                if count == 0:
                    st.info("No Grisha-ready rows (Valid + campaign ACTIVE, not previously exported).")
                else:
                    st.success(f"Preview {count} rows (not marked exported).")
                    with open(path, "rb") as f:
                        st.download_button("Download preview CSV", f, file_name=path.name, mime="text/csv", key="gprev")
        with col_b:
            default_gid = BatchService(conn).grisha_export_id()
            gid = st.text_input("Grisha export batch ID", value=default_gid)
            st.checkbox("I confirm this outreach export. Mark leads EXPORTED_TO_GRISHA.", key="gack")
            if st.button("COMMIT Grisha Export", type="primary"):
                if not st.session_state.get("gack"):
                    st.error("Confirm before COMMIT. Preview does not create export events.")
                else:
                    with transaction() as tconn:
                        path, count = ExportService(tconn).confirm_grisha_export(gid)
                    if count == 0:
                        st.info("Nothing to export.")
                    else:
                        st.success(f"Confirmed {count} rows. Batch `{gid}`.")
                        with open(path, "rb") as f:
                            st.download_button("Download Grisha CSV", f, file_name=path.name, mime="text/csv", key="gconf")

        st.markdown("---")
        actions = [
            ("Unknown Retry", "unknown_retry"),
            ("Accept-All Quarantine", "accept_all_quarantine"),
            ("Exhausted Invalid", "exhausted_invalid"),
            ("Updated Master Hub", "updated_master_hub"),
            ("Dedupe / Audit Log", "dedupe_audit"),
        ]
        for label, attr in actions:
            if st.button(label, key=f"exp_{attr}"):
                path, count = getattr(exporter, attr)()
                if count == 0:
                    st.info(f"No records for: {label}")
                else:
                    st.success(f"{label}: {count} rows")
                    with open(path, "rb") as f:
                        st.download_button(
                            f"Download {path.name}",
                            f,
                            file_name=path.name,
                            mime="text/csv",
                            key=f"dl_{path.name}",
                        )

        st.markdown("---")
        st.subheader("Full State Backup")
        st.caption("Fails if any required disaster-recovery table cannot be exported. Never a flattened CSV.")
        if st.button("Generate Full State Backup XLSX", type="primary"):
            try:
                path, total = exporter.full_state_backup()
                st.success(f"Backup wrote {total} rows across required tables → {path.name}")
                with open(path, "rb") as f:
                    st.download_button(
                        "Download Full State Backup",
                        f,
                        file_name=path.name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="dl_backup",
                    )
            except BackupError as exc:
                st.error(str(exc))
    finally:
        conn.close()

elif page == "Restore Full State Backup":
    st.title("Restore Full State Backup")
    st.warning("Never overwrites an operational database without explicit confirmation. PREVIEW → VALIDATE → COMMIT.")
    uploaded = st.file_uploader("Disaster-recovery XLSX", type=["xlsx"])
    if uploaded:
        content = uploaded.getvalue()
        preview = preview_backup(content)
        st.write("Sheets in backup:")
        st.json({k: v["row_count"] for k, v in preview["sheets"].items()})
        if preview["missing_required"]:
            st.error("Missing required sheets: " + ", ".join(preview["missing_required"]))
        else:
            st.success("All required disaster-recovery tables are present.")
        check = validate_backup(content)
        if not check["ok"]:
            st.error("VALIDATE failed: " + "; ".join(check["errors"]))
        conn = get_conn()
        try:
            existing = scalar(conn, "SELECT COUNT(*) AS n FROM companies") or 0
            st.info(f"Current operational companies: {existing}")
            batch_svc = BatchService(conn)
            batch_id = st.text_input("Restore batch ID", value=batch_svc.suggest_next_id("Restore"))
            confirm = st.checkbox(
                "I understand this COMMIT writes into the operational database and cannot silently roll back append-only history.",
                key="restore_ack",
            )
            if st.button("COMMIT Restore", type="primary"):
                if not confirm:
                    st.error("Explicit confirmation is required.")
                elif not check["ok"]:
                    st.error("Cannot COMMIT an invalid backup.")
                else:
                    try:
                        with transaction() as tconn:
                            if not BatchService(tconn).exists(batch_id):
                                BatchService(tconn).create(batch_id, "RESTORE", "Full state restore")
                            result = commit_restore(tconn, content, confirm_overwrite=True, batch_id=batch_id)
                        st.success("Restore committed.")
                        st.json(result["restored"])
                    except RestoreError as exc:
                        st.error(str(exc))
        finally:
            conn.close()

elif page == "Manual Override":
    st.title("Manual Override")
    st.warning("Every action is audited. Historical verification events are never deleted or edited. Campaign changes never rewrite technical truth.")
    conn = get_conn()
    try:
        action = st.selectbox(
            "Action",
            [
                "Release quarantined domain",
                "Resume verification",
                "HOLD → ACTIVE",
                "Mark EXCLUDED",
                "Mark DO_NOT_CONTACT",
                "Mark already contacted",
                "Mark company duplicate",
                "Mark executive duplicate",
                "Merge company aliases",
                "Correct company name",
                "Correct domain",
                "Add historical domain",
                "Assign buying group",
                "Correct executive metadata",
            ],
        )
        reason = st.text_input("Reason (required)", value="")

        if action == "Release quarantined domain":
            domain = st.text_input("Domain")
            st.caption("Domain becomes RELEASED. Affected executives move to HOLD. Verification does not auto-resume.")
            if st.button("Execute") and reason and domain:
                with transaction() as tconn:
                    res = OverrideService(tconn).release_quarantined_domain(domain, reason)
                st.write(res)

        elif action == "Resume verification":
            eid = st.number_input("Executive ID", min_value=1, step=1, key="resume_eid")
            st.checkbox("I confirm resume. This reactivates a candidate and does not itself spend a verifier credit.", key="resume_ack")
            if st.button("Execute") and reason:
                if not st.session_state.get("resume_ack"):
                    st.error("Confirm resume before executing.")
                else:
                    with transaction() as tconn:
                        res = OverrideService(tconn).resume_verification(int(eid), reason)
                    st.write(res)

        elif action == "Merge company aliases":
            src = st.number_input("Source company ID (becomes MERGED)", min_value=1, step=1, key="merge_src")
            tgt = st.number_input("Canonical target company ID", min_value=1, step=1, key="merge_tgt")
            st.caption("Source is marked MERGED and kept. History is never deleted. Dedupe follows the canonical target.")
            if st.button("Execute") and reason:
                with transaction() as tconn:
                    res = OverrideService(tconn).merge_company_aliases(int(src), int(tgt), reason)
                st.write(res)

        elif action in (
            "HOLD → ACTIVE",
            "Mark EXCLUDED",
            "Mark DO_NOT_CONTACT",
            "Mark already contacted",
            "Mark company duplicate",
            "Mark executive duplicate",
        ):
            eid = st.number_input("Executive ID", min_value=1, step=1)
            if st.button("Execute") and reason:
                with transaction() as tconn:
                    svc = OverrideService(tconn)
                    if action == "HOLD → ACTIVE":
                        res = svc.hold_to_net_new(int(eid), reason)
                    elif action == "Mark EXCLUDED":
                        res = svc.mark_excluded(int(eid), reason)
                    elif action == "Mark DO_NOT_CONTACT":
                        res = svc.mark_do_not_contact(int(eid), reason)
                    elif action == "Mark already contacted":
                        res = svc.mark_already_contacted(int(eid), reason)
                    elif action == "Mark company duplicate":
                        res = svc.mark_company_duplicate(int(eid), reason)
                    else:
                        res = svc.mark_executive_duplicate(int(eid), reason)
                st.write(res)

        elif action == "Correct company name":
            cid = st.number_input("Company ID", min_value=1, step=1)
            new_name = st.text_input("New canonical name")
            if st.button("Execute") and reason and new_name:
                with transaction() as tconn:
                    res = OverrideService(tconn).correct_canonical_company_name(int(cid), new_name, reason)
                st.write(res)

        elif action == "Correct domain":
            did = st.number_input("Domain ID", min_value=1, step=1)
            new_dom = st.text_input("New domain")
            if st.button("Execute") and reason and new_dom:
                with transaction() as tconn:
                    res = OverrideService(tconn).correct_canonical_domain(int(did), new_dom, reason)
                st.write(res)

        elif action == "Add historical domain":
            cid = st.number_input("Company ID", min_value=1, step=1, key="hist_cid")
            dom = st.text_input("Historical domain")
            if st.button("Execute") and reason and dom:
                with transaction() as tconn:
                    res = OverrideService(tconn).add_historical_domain(int(cid), dom, reason)
                st.write(res)

        elif action == "Assign buying group":
            cid = st.number_input("Company ID", min_value=1, step=1, key="bg_cid")
            bg = st.text_input("Buying group name")
            if st.button("Execute") and reason:
                with transaction() as tconn:
                    res = OverrideService(tconn).assign_buying_group(int(cid), bg, reason)
                st.write(res)

        elif action == "Correct executive metadata":
            eid = st.number_input("Executive ID", min_value=1, step=1, key="meta_eid")
            fn = st.text_input("First name (leave blank to keep)")
            ln = st.text_input("Last name (leave blank to keep)")
            title = st.text_input("Title (leave blank to keep)")
            state = st.text_input("State (leave blank to keep)")
            if st.button("Execute") and reason:
                with transaction() as tconn:
                    res = OverrideService(tconn).correct_executive_metadata(
                        int(eid),
                        first_name=fn or None,
                        last_name=ln or None,
                        title=title or None,
                        state=state or None,
                        reason=reason,
                    )
                st.write(res)

        st.subheader("Lookup")
        q = st.text_input("Search executives by name or company")
        if q:
            df = query_df(
                conn,
                """
                SELECT e.executive_id, e.first_name, e.last_name,
                       e.technical_status, e.campaign_disposition,
                       c.canonical_name AS company, c.company_id, c.current_status AS company_status
                FROM executives e
                JOIN companies c ON c.company_id = e.company_id
                WHERE e.first_name LIKE ? OR e.last_name LIKE ? OR c.canonical_name LIKE ?
                LIMIT 50
                """,
                (f"%{q}%", f"%{q}%", f"%{q}%"),
            )
            st.dataframe(df, use_container_width=True)
    finally:
        conn.close()

elif page == "Settings":
    st.title("Settings")
    health = check_db_health()
    if health.get("ok") and health.get("using_postgres"):
        st.markdown('<div class="vs-ok">DATABASE CONNECTED · PostgreSQL</div>', unsafe_allow_html=True)
    elif health.get("ok"):
        st.warning("DATABASE CONNECTED · SQLite test mode. Not production.")
    else:
        st.markdown(
            f'<div class="vs-banner">{health.get("error") or health.get("message")}</div>',
            unsafe_allow_html=True,
        )
    st.write(f"**Engine version:** `{__version__}`")
    st.write(f"**PostgreSQL configured:** `{bool(DATABASE_URL)}`")
    st.write(f"**Using PostgreSQL:** `{USING_POSTGRES}`")
    st.write(f"**APP_PASSWORD set:** `{bool(APP_PASSWORD)}`")
    st.write(f"**Production ready (URL + password):** `{PRODUCTION_READY}`")
    st.write(f"**SQLite test flag:** `{ALLOW_SQLITE_TEST}`")
    st.write(f"**Export directory:** `{EXPORT_DIR}`")
    conn = get_conn()
    try:
        batch_svc = BatchService(conn)
        st.write(f"**Next suggested Batch ID:** `{batch_svc.suggest_next_id()}`")
        versions = query_df(conn, "SELECT version, description, applied_at FROM schema_version ORDER BY version")
        st.subheader("Applied migrations")
        st.dataframe(versions, use_container_width=True)
    finally:
        conn.close()
    st.markdown("---")
    st.caption(
        "VitalSeconds Lead Engine v1.1 · Attempt order is Pass number · "
        "Technical status ≠ campaign disposition · Full auditability"
    )
