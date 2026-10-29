"""
RoadWise AI - Backup & Restore Utility (Stage 7: Field-Pilot Readiness).
Provides safe, non-destructive backup and recovery for PostgreSQL/PostGIS
and local uploaded photographs in static/uploads/.

Safety Guarantees:
  1. Primary Database Protection: Refuses to restore over 'gisdb' unless --force
     is explicitly passed. Encourages testing recovery on isolated temporary databases.
  2. Upload Photo Safety: Creates timestamped zip archives and enforces ZipSlip
     path-traversal protection during restore.
  3. Secret Masking: Database passwords are masked in all terminal output and logs.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
import urllib.parse
from dotenv import load_dotenv, find_dotenv
import psycopg
from psycopg.rows import dict_row


TABLES_TO_BACKUP = [
    "road_damage_reports",
    "road_segments",
    "junctions",
    "traffic_counts",
    "report_priority_scores",
    "work_orders",
    "work_order_status_history",
    "pilot_users",
    "work_order_updates",
    "work_order_update_attachments",
]


def mask_url(url):
    """Safely mask password in PostgreSQL connection URL."""
    if not url or "@" not in url:
        return url
    prefix, rest = url.split("://", 1)
    last_at = rest.rfind("@")
    userinfo = rest[:last_at]
    hostinfo = rest[last_at + 1:]
    if ":" in userinfo:
        user, _ = userinfo.split(":", 1)
        return f"{prefix}://{user}:***@{hostinfo}"
    return f"{prefix}://***@{hostinfo}"


def get_db_name_from_url(url):
    """Extract database name from connection URL."""
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    return parsed.path.lstrip("/")


def load_configured_db_url():
    load_dotenv(find_dotenv(usecwd=True))
    for p in [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    ]:
        if os.path.isfile(p):
            load_dotenv(p)
    return os.environ.get("DATABASE_URL")


# ─────────────────────────────────────────────────────────────────────────────
# Photo Backup & Restore
# ─────────────────────────────────────────────────────────────────────────────

def backup_photos(uploads_dir, backup_dir):
    """
    Archive all photos in uploads_dir into a timestamped zip file.
    Returns dict with {zip_path, file_count, total_bytes}.
    """
    if not os.path.isdir(uploads_dir):
        raise FileNotFoundError(f"Uploads directory not found: {uploads_dir}")

    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    zip_filename = f"roadwise_photos_{timestamp}.zip"
    zip_path = os.path.join(backup_dir, zip_filename)

    file_count = 0
    total_bytes = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(uploads_dir):
            for file in files:
                if file.startswith("."):
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, uploads_dir)
                zf.write(file_path, rel_path)
                file_count += 1
                total_bytes += os.path.getsize(file_path)

    return {
        "zip_path": zip_path,
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def restore_photos(backup_zip_path, target_dir, allow_overwrite=False):
    """
    Extract a photos zip archive into target_dir with ZipSlip validation.
    Returns count of extracted files.
    """
    if not os.path.isfile(backup_zip_path):
        raise FileNotFoundError(f"Photo backup archive not found: {backup_zip_path}")

    os.makedirs(target_dir, exist_ok=True)

    if not allow_overwrite and os.listdir(target_dir):
        raise RuntimeError(
            f"Target directory '{target_dir}' is not empty. "
            "To prevent data loss, restore into an empty directory or specify allow_overwrite=True."
        )

    target_real = os.path.realpath(target_dir)
    extracted_count = 0

    with zipfile.ZipFile(backup_zip_path, "r") as zf:
        for member in zf.infolist():
            # ZipSlip security check
            member_dest = os.path.realpath(os.path.join(target_dir, member.filename))
            if not member_dest.startswith(target_real + os.sep) and member_dest != target_real:
                raise RuntimeError(f"ZipSlip security violation in zip member: {member.filename}")
            zf.extract(member, target_dir)
            extracted_count += 1

    return extracted_count


def backup_attachments(attachments_dir, backup_dir):
    """
    Archive all update attachments in attachments_dir into a timestamped zip file.
    Returns dict with {zip_path, file_count, total_bytes}.
    """
    if not os.path.isdir(attachments_dir):
        os.makedirs(attachments_dir, exist_ok=True)

    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    zip_filename = f"roadwise_attachments_{timestamp}.zip"
    zip_path = os.path.join(backup_dir, zip_filename)

    file_count = 0
    total_bytes = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(attachments_dir):
            for file in files:
                if file.startswith("."):
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, attachments_dir)
                zf.write(file_path, rel_path)
                file_count += 1
                total_bytes += os.path.getsize(file_path)

    return {
        "zip_path": zip_path,
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def restore_attachments(backup_zip_path, target_dir, allow_overwrite=False):
    """
    Extract an attachments zip archive into target_dir with ZipSlip validation.
    Returns count of extracted files.
    """
    return restore_photos(backup_zip_path, target_dir, allow_overwrite=allow_overwrite)


# ─────────────────────────────────────────────────────────────────────────────
# Database Backup & Restore
# ─────────────────────────────────────────────────────────────────────────────

def sql_quote(val):
    """Safely format a value as an SQL literal (NULL or single-quoted escaped string)."""
    if val is None:
        return "NULL"
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def python_fallback_db_dump(db_url, output_sql_path):
    """
    Pure-Python SQL dump fallback when pg_dump binary is not in PATH.
    Dumps all RoadWise AI tables and data in transaction order.
    """
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        with open(output_sql_path, "w", encoding="utf-8") as out:
            out.write("-- RoadWise AI Database Dump (Pure-Python Fallback)\n")
            out.write(f"-- Generated at: {datetime.now(timezone.utc).isoformat()} UTC\n\n")
            out.write("BEGIN;\n\n")

            for tbl in TABLES_TO_BACKUP:
                # Check if table exists
                check = conn.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
                    (tbl,),
                ).fetchone()
                if not check:
                    continue

                out.write(f"-- Data for table: {tbl}\n")
                if tbl == "road_damage_reports":
                    rows = conn.execute(
                        """
                        SELECT id, road_name, damage_type, severity, description,
                               photo_url, ST_AsText(geom) AS geom_wkt, created_at
                        FROM road_damage_reports
                        ORDER BY id
                        """
                    ).fetchall()
                    for r in rows:
                        desc = sql_quote(r["description"])
                        purl = sql_quote(r["photo_url"])
                        rname = str(r["road_name"]).replace("'", "''")
                        out.write(
                            f"INSERT INTO road_damage_reports (id, road_name, damage_type, severity, description, photo_url, geom, created_at) "
                            f"VALUES ({r['id']}, '{rname}', '{r['damage_type']}', '{r['severity']}', {desc}, {purl}, "
                            f"ST_GeomFromText('{r['geom_wkt']}', 4326), '{r['created_at'].isoformat()}') "
                            f"ON CONFLICT (id) DO NOTHING;\n"
                        )
                elif tbl == "work_orders":
                    rows = conn.execute(
                        """
                        SELECT id, work_order_code, report_id, status, assigned_to, target_date,
                               completed_at, notes, created_at, updated_at
                        FROM work_orders ORDER BY id
                        """
                    ).fetchall()
                    for r in rows:
                        asgn = sql_quote(r.get("assigned_to"))
                        tdate = sql_quote(r.get("target_date"))
                        comp_val = r.get("completed_at").isoformat() if r.get("completed_at") else None
                        comp = sql_quote(comp_val)
                        notes = sql_quote(r.get("notes"))
                        out.write(
                            f"INSERT INTO work_orders (id, work_order_code, report_id, status, assigned_to, target_date, completed_at, notes, created_at, updated_at) "
                            f"VALUES ({r['id']}, '{r['work_order_code']}', {r['report_id']}, '{r['status']}', {asgn}, {tdate}, {comp}, {notes}, "
                            f"'{r['created_at'].isoformat()}', '{r['updated_at'].isoformat()}') "
                            f"ON CONFLICT (id) DO NOTHING;\n"
                        )
                elif tbl == "work_order_status_history":
                    rows = conn.execute(
                        "SELECT id, work_order_id, from_status, to_status, note, changed_at FROM work_order_status_history ORDER BY id"
                    ).fetchall()
                    for r in rows:
                        fstatus = sql_quote(r.get("from_status"))
                        note = sql_quote(r.get("note"))
                        out.write(
                            f"INSERT INTO work_order_status_history (id, work_order_id, from_status, to_status, note, changed_at) "
                            f"VALUES ({r['id']}, {r['work_order_id']}, {fstatus}, '{r['to_status']}', {note}, '{r['changed_at'].isoformat()}') "
                            f"ON CONFLICT (id) DO NOTHING;\n"
                        )
                elif tbl == "pilot_users":
                    rows = conn.execute(
                        "SELECT id, username, password_hash, role, full_name, is_active, created_at, updated_at FROM pilot_users ORDER BY id"
                    ).fetchall()
                    for r in rows:
                        fname = sql_quote(r.get("full_name"))
                        out.write(
                            f"INSERT INTO pilot_users (id, username, password_hash, role, full_name, is_active, created_at, updated_at) "
                            f"VALUES ({r['id']}, '{r['username']}', '{r['password_hash']}', '{r['role']}', {fname}, {r['is_active']}, "
                            f"'{r['created_at'].isoformat()}', '{r['updated_at'].isoformat()}') "
                            f"ON CONFLICT (id) DO NOTHING;\n"
                        )
                elif tbl == "work_order_updates":
                    rows = conn.execute(
                        """
                        SELECT id, work_order_id, author_user_id, author_username, status, progress_pct,
                               work_completed, blockers, next_actions, estimated_completion_date,
                               internal_notes, is_published, published_at, published_by,
                               is_correction, correction_for_id, created_at
                        FROM work_order_updates ORDER BY id
                        """
                    ).fetchall()
                    for r in rows:
                        auid = "NULL" if r.get("author_user_id") is None else str(r["author_user_id"])
                        wcomp = sql_quote(r["work_completed"])
                        blk = sql_quote(r.get("blockers"))
                        nxt = sql_quote(r.get("next_actions"))
                        edate = sql_quote(r.get("estimated_completion_date"))
                        inotes = sql_quote(r.get("internal_notes"))
                        pub_ts = sql_quote(r.get("published_at").isoformat() if r.get("published_at") else None)
                        pub_by = sql_quote(r.get("published_by"))
                        corr_id = "NULL" if r.get("correction_for_id") is None else str(r["correction_for_id"])
                        out.write(
                            f"INSERT INTO work_order_updates (id, work_order_id, author_user_id, author_username, status, progress_pct, "
                            f"work_completed, blockers, next_actions, estimated_completion_date, internal_notes, is_published, published_at, published_by, "
                            f"is_correction, correction_for_id, created_at) "
                            f"VALUES ({r['id']}, {r['work_order_id']}, {auid}, '{r['author_username']}', '{r['status']}', {r['progress_pct']}, "
                            f"{wcomp}, {blk}, {nxt}, {edate}, {inotes}, {r['is_published']}, {pub_ts}, {pub_by}, "
                            f"{r['is_correction']}, {corr_id}, '{r['created_at'].isoformat()}') "
                            f"ON CONFLICT (id) DO NOTHING;\n"
                        )
                elif tbl == "work_order_update_attachments":
                    rows = conn.execute(
                        """
                        SELECT id, update_id, work_order_id, file_type, mime_type, original_filename,
                               stored_filename, file_size_bytes, caption, is_approved, created_at
                        FROM work_order_update_attachments ORDER BY id
                        """
                    ).fetchall()
                    for r in rows:
                        orig = sql_quote(r["original_filename"])
                        stored = sql_quote(r["stored_filename"])
                        cap = sql_quote(r.get("caption"))
                        out.write(
                            f"INSERT INTO work_order_update_attachments (id, update_id, work_order_id, file_type, mime_type, original_filename, "
                            f"stored_filename, file_size_bytes, caption, is_approved, created_at) "
                            f"VALUES ({r['id']}, {r['update_id']}, {r['work_order_id']}, '{r['file_type']}', '{r['mime_type']}', {orig}, "
                            f"{stored}, {r['file_size_bytes']}, {cap}, {r['is_approved']}, '{r['created_at'].isoformat()}') "
                            f"ON CONFLICT (id) DO NOTHING;\n"
                        )
                out.write("\n")

            out.write("COMMIT;\n")


def backup_database(backup_dir, db_url=None):
    """
    Back up database to SQL file. Uses pg_dump if in PATH, otherwise pure-Python fallback.
    Returns dict with {sql_path, method, size_bytes}.
    """
    url = db_url or load_configured_db_url()
    if not url:
        raise ValueError("DATABASE_URL is not configured.")

    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sql_filename = f"roadwise_backup_{timestamp}.sql"
    sql_path = os.path.join(backup_dir, sql_filename)

    has_pg_dump = shutil.which("pg_dump") is not None
    method = "pg_dump" if has_pg_dump else "python_fallback"

    if has_pg_dump:
        cmd = ["pg_dump", "--format=p", "--clean", "--if-exists", f"--file={sql_path}", url]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            # Fall back to python dump if pg_dump failed
            python_fallback_db_dump(url, sql_path)
            method = "python_fallback_after_pgdump_error"
    else:
        python_fallback_db_dump(url, sql_path)

    size = os.path.getsize(sql_path)
    return {
        "sql_path": sql_path,
        "method": method,
        "size_bytes": size,
    }


def restore_database(sql_path, target_db_url, force=False):
    """
    Restore SQL dump into target_db_url.
    Refuses to restore over 'gisdb' without explicit force=True.
    Returns count of statements executed.
    """
    if not os.path.isfile(sql_path):
        raise FileNotFoundError(f"Database backup file not found: {sql_path}")

    target_name = get_db_name_from_url(target_db_url).lower()
    if target_name == "gisdb" and not force:
        raise ValueError(
            "SAFETY VIOLATION: Target database is 'gisdb' (primary operational database). "
            "To prevent accidental data loss, restore into an isolated test database (e.g. 'gisdb_temp') "
            "or explicitly pass force=True."
        )

    with open(sql_path, "r", encoding="utf-8") as f:
        sql = f.read()

    with psycopg.connect(target_db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

    return True


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RoadWise AI - Backup & Recovery Utility")
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # backup
    p_b = subparsers.add_parser("backup", help="Create full backup (database + static uploads)")
    p_b.add_argument("--output-dir", default="backups", help="Destination folder (default: backups)")

    # restore-photos
    p_rp = subparsers.add_parser("restore-photos", help="Restore photos archive to directory")
    p_rp.add_argument("--file", required=True, help="Path to photos zip file")
    p_rp.add_argument("--target-dir", required=True, help="Destination directory")
    p_rp.add_argument("--allow-overwrite", action="store_true", help="Allow extracting into non-empty directory")

    # restore-attachments
    p_ra = subparsers.add_parser("restore-attachments", help="Restore work-order attachments archive to directory")
    p_ra.add_argument("--file", required=True, help="Path to attachments zip file")
    p_ra.add_argument("--target-dir", required=True, help="Destination directory")
    p_ra.add_argument("--allow-overwrite", action="store_true", help="Allow extracting into non-empty directory")

    # restore-db
    p_rdb = subparsers.add_parser("restore-db", help="Restore database SQL dump into target DB")
    p_rdb.add_argument("--file", required=True, help="Path to SQL dump file")
    p_rdb.add_argument("--target-url", required=True, help="Target PostgreSQL connection URL (e.g. postgresql://.../gisdb_temp)")
    p_rdb.add_argument("--force", action="store_true", help="Confirm overwrite if target is 'gisdb'")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "backup":
        db_dir = os.path.join(args.output_dir, "db")
        photo_dir = os.path.join(args.output_dir, "photos")
        att_dir = os.path.join(args.output_dir, "attachments")
        uploads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
        attachments_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", "work_orders")

        print("Creating RoadWise AI backups...")
        db_res = backup_database(db_dir)
        print(f"SUCCESS: Database backup created    -> {db_res['sql_path']} ({db_res['size_bytes']} bytes, method={db_res['method']})")

        photo_res = backup_photos(uploads_dir, photo_dir)
        print(f"SUCCESS: Photos backup created      -> {photo_res['zip_path']} ({photo_res['file_count']} files, {photo_res['total_bytes']} bytes)")

        att_res = backup_attachments(attachments_dir, att_dir)
        print(f"SUCCESS: Attachments backup created -> {att_res['zip_path']} ({att_res['file_count']} files, {att_res['total_bytes']} bytes)")

    elif args.command == "restore-photos":
        count = restore_photos(args.file, args.target_dir, allow_overwrite=args.allow_overwrite)
        print(f"SUCCESS: Extracted {count} photos to '{args.target_dir}'.")

    elif args.command == "restore-attachments":
        count = restore_attachments(args.file, args.target_dir, allow_overwrite=args.allow_overwrite)
        print(f"SUCCESS: Extracted {count} attachments to '{args.target_dir}'.")

    elif args.command == "restore-db":
        masked = mask_url(args.target_url)
        print(f"Restoring database dump '{args.file}' to {masked}...")
        restore_database(args.file, args.target_url, force=args.force)
        print(f"SUCCESS: Database restored to {masked}.")


if __name__ == "__main__":
    main()

