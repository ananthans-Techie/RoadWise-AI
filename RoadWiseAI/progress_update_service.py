"""
RoadWise AI - Public Road-Repair Progress Updates & Attachments Service (Stage 8).
Provides append-only progress update logging, role-based authorization,
attachment validation (images and PDFs), publication controls,
and public query feeds with strict privacy redactions.
"""
import io
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
from werkzeug.utils import secure_filename
import psycopg
from psycopg.rows import dict_row

import work_order_service

ALLOWED_ATTACHMENT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB limit


# ─────────────────────────────────────────────────────────────────────────────
# 1. Authorization Helpers
# ─────────────────────────────────────────────────────────────────────────────

def can_user_update_work_order(user: Dict[str, Any], work_order: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Check if an authenticated user is authorized to update a work order.
    Rules:
      - Admin: Can update ANY work order.
      - Staff: Can update work orders assigned to them or their team,
               or unassigned work orders.
    """
    if not user:
        return False, "Authentication required."

    role = (user.get("role") or "").lower()
    if role == "admin":
        return True, None

    if role != "staff":
        return False, "Forbidden: Municipal staff or admin privileges required."

    assigned_to = (work_order.get("assigned_to") or "").strip().lower()
    if not assigned_to:
        # Unassigned work orders can be updated or claimed by any active staff member
        return True, None

    username = (user.get("username") or "").strip().lower()
    fullname = (user.get("full_name") or "").strip().lower()

    if username and username in assigned_to:
        return True, None
    if fullname and fullname in assigned_to:
        return True, None

    return False, f"Forbidden: You are not assigned to work order {work_order.get('work_order_code', '')}. Assigned to: {work_order.get('assigned_to')}."


# ─────────────────────────────────────────────────────────────────────────────
# 2. File & Attachment Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_attachment(file_bytes: bytes, original_filename: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Validate attachment file content, size, and extension.
    Never trusts the browser's claimed MIME type alone.
    Returns (file_type, mime_type, error_message).
    file_type will be 'image' or 'pdf'.
    """
    if not file_bytes:
        return None, None, "The uploaded file is empty."

    if len(file_bytes) > MAX_ATTACHMENT_BYTES:
        return None, None, f"File exceeds the 10 MB size limit ({len(file_bytes) / (1024*1024):.1f} MB uploaded)."

    if not original_filename or "." not in original_filename:
        return None, None, "File must have a valid extension (.jpg, .jpeg, .png, .pdf)."

    ext = os.path.splitext(original_filename)[1].lower()
    if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
        return None, None, f"Unsupported file type '{ext}'. Allowed: JPEG, PNG, PDF."

    if ext in (".jpg", ".jpeg", ".png"):
        # Image verification via Pillow
        try:
            image = Image.open(io.BytesIO(file_bytes))
            if image.format not in ("JPEG", "PNG", "MPO"):
                return None, None, "Invalid image encoding. Genuine JPEG or PNG required."
            image.verify()
            mime = "image/png" if ext == ".png" else "image/jpeg"
            return "image", mime, None
        except Exception:
            return None, None, "Could not decode uploaded photograph as a valid JPEG or PNG image."

    elif ext == ".pdf":
        # PDF verification via magic bytes
        if len(file_bytes) < 5 or not file_bytes.startswith(b"%PDF-"):
            return None, None, "Invalid PDF document: missing '%PDF-' file signature."
        return "pdf", "application/pdf", None

    return None, None, "Unknown file format."


def save_attachment_file(folder: str, file_bytes: bytes, original_filename: str, wo_id: int, update_id: int) -> str:
    """
    Save validated attachment bytes to a secure non-public storage directory.
    Enforces canonical path traversal protection.
    Returns stored_filename.
    """
    os.makedirs(folder, exist_ok=True)
    real_folder = os.path.realpath(folder)

    ext = os.path.splitext(original_filename)[1].lower()
    unique_suffix = uuid.uuid4().hex[:12]
    stored_name = f"wo_{wo_id}_up_{update_id}_{unique_suffix}{ext}"

    dest_path = os.path.realpath(os.path.join(real_folder, stored_name))
    if not dest_path.startswith(real_folder + os.sep):
        raise ValueError("Security violation: path traversal detected in attachment destination.")

    with open(dest_path, "wb") as f:
        f.write(file_bytes)

    return stored_name


# ─────────────────────────────────────────────────────────────────────────────
# 3. Progress Update Management (Append-Only)
# ─────────────────────────────────────────────────────────────────────────────

def create_progress_update(
    conn,
    wo_id: int,
    user: Dict[str, Any],
    work_completed: str,
    status: Optional[str] = None,
    progress_pct: int = 0,
    blockers: Optional[str] = None,
    next_actions: Optional[str] = None,
    estimated_completion_date: Optional[str] = None,
    internal_notes: Optional[str] = None,
    is_published: bool = False,
    is_correction: bool = False,
    correction_for_id: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Record an append-only progress update for an existing work order.
    Enforces work-order assignment permissions and status transition rules.
    Returns (created_update_dict, error_message).
    """
    # 1. Fetch work order
    wo_row = conn.execute(
        """
        SELECT id, work_order_code, report_id, status, assigned_to, target_date
        FROM work_orders
        WHERE id = %s
        """,
        (wo_id,),
    ).fetchone()

    if not wo_row:
        return None, f"Work order #{wo_id} not found."

    wo = dict(wo_row)

    # 2. Permission check
    can_update, err_perm = can_user_update_work_order(user, wo)
    if not can_update:
        return None, err_perm

    # 3. Validate work completed
    if not work_completed or not str(work_completed).strip():
        return None, "Progress description in 'work_completed' is required."

    # 4. Validate progress percentage
    try:
        pct = int(progress_pct)
        if pct < 0 or pct > 100:
            return None, "Progress percentage must be between 0 and 100."
    except (ValueError, TypeError):
        return None, "Progress percentage must be an integer between 0 and 100."

    # 5. Status Transition
    current_status = wo["status"]
    target_status = (status or "").strip() or current_status

    if target_status not in work_order_service.ALLOWED_STATUSES:
        return None, f"Invalid status '{target_status}'. Allowed: {', '.join(sorted(work_order_service.ALLOWED_STATUSES))}."

    status_changed = (target_status != current_status)
    if status_changed:
        is_valid_transition, err_trans = work_order_service.validate_transition(current_status, target_status)
        if not is_valid_transition:
            return None, err_trans

        # If marking Completed, require detailed completion summary
        if target_status == "Completed":
            if len(work_completed.strip()) < 10:
                return None, "A detailed completion summary of at least 10 characters is required when marking work as Completed."

        # Update work_orders status and completion timestamp
        set_clauses = ["status = %s", "updated_at = NOW()", "updated_by = %s"]
        params = [target_status, user.get("username")]

        if target_status == "Completed":
            set_clauses.append("completed_at = NOW()")
        elif current_status == "Completed" and target_status != "Completed":
            set_clauses.append("completed_at = NULL")

        params.append(wo_id)
        conn.execute(
            f"UPDATE work_orders SET {', '.join(set_clauses)} WHERE id = %s",
            params,
        )

        # Record Stage 6B status history entry
        transition_note = (
            f"Status transitioned from {current_status} to {target_status} via progress update. "
            f"Summary: {work_completed[:120]}..."
        )
        conn.execute(
            """
            INSERT INTO work_order_status_history
                (work_order_id, from_status, to_status, note, changed_by)
            VALUES
                (%s, %s, %s, %s, %s)
            """,
            (wo_id, current_status, target_status, transition_note, user.get("username")),
        )

    # 6. Validate estimated completion date
    clean_est_date = None
    if estimated_completion_date and str(estimated_completion_date).strip():
        date_str = str(estimated_completion_date).strip()
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            clean_est_date = date_str
            # Also update target_date on work order if target_date was empty
            if not wo.get("target_date"):
                conn.execute(
                    "UPDATE work_orders SET target_date = %s, updated_at = NOW() WHERE id = %s",
                    (clean_est_date, wo_id),
                )
        except ValueError:
            return None, "Invalid estimated_completion_date format. Expected YYYY-MM-DD."

    # 7. Correction check
    if is_correction and correction_for_id:
        parent_up = conn.execute(
            "SELECT id FROM work_order_updates WHERE id = %s AND work_order_id = %s",
            (correction_for_id, wo_id),
        ).fetchone()
        if not parent_up:
            return None, f"Cannot create correction: Original update #{correction_for_id} not found on this work order."

    # 8. Publication state
    published_at_val = "NOW()" if is_published else "NULL"
    published_by_val = user.get("username") if is_published else None

    insert_sql = f"""
        INSERT INTO work_order_updates
            (work_order_id, author_user_id, author_username, status, progress_pct,
             work_completed, blockers, next_actions, estimated_completion_date,
             internal_notes, is_published, published_at, published_by,
             is_correction, correction_for_id)
        VALUES
            (%s, %s, %s, %s, %s,
             %s, %s, %s, %s,
             %s, %s, {published_at_val}, %s,
             %s, %s)
        RETURNING id, work_order_id, author_user_id, author_username, status, progress_pct,
                  work_completed, blockers, next_actions, estimated_completion_date,
                  internal_notes, is_published, published_at, published_by,
                  is_correction, correction_for_id, created_at
    """
    row = conn.execute(
        insert_sql,
        (
            wo_id,
            user.get("id"),
            user.get("username"),
            target_status,
            pct,
            work_completed.strip(),
            blockers.strip() if blockers and str(blockers).strip() else None,
            next_actions.strip() if next_actions and str(next_actions).strip() else None,
            clean_est_date,
            internal_notes.strip() if internal_notes and str(internal_notes).strip() else None,
            bool(is_published),
            published_by_val,
            bool(is_correction),
            correction_for_id if is_correction else None,
        ),
    ).fetchone()

    res = dict(row)
    res["attachments"] = []
    return res, None


def add_attachment_record(
    conn,
    update_id: int,
    wo_id: int,
    file_type: str,
    mime_type: str,
    original_filename: str,
    stored_filename: str,
    file_size_bytes: int,
    caption: Optional[str] = None,
    is_approved: bool = True,
) -> Dict[str, Any]:
    """Store attachment metadata linked to a progress update."""
    clean_caption = caption.strip() if caption and str(caption).strip() else None
    row = conn.execute(
        """
        INSERT INTO work_order_update_attachments
            (update_id, work_order_id, file_type, mime_type, original_filename,
             stored_filename, file_size_bytes, caption, is_approved)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, update_id, work_order_id, file_type, mime_type, original_filename,
                  stored_filename, file_size_bytes, caption, is_approved, created_at
        """,
        (
            update_id,
            wo_id,
            file_type,
            mime_type,
            original_filename,
            stored_filename,
            file_size_bytes,
            clean_caption,
            is_approved,
        ),
    ).fetchone()
    return dict(row)


def set_update_published(conn, update_id: int, is_published: bool, user: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Explicitly publish or unpublish an update.
    Requires staff or admin privileges.
    """
    if not user:
        return None, "Authentication required."

    up_row = conn.execute(
        "SELECT id, work_order_id, author_username, is_published FROM work_order_updates WHERE id = %s",
        (update_id,),
    ).fetchone()
    if not up_row:
        return None, f"Progress update #{update_id} not found."

    role = (user.get("role") or "").lower()
    username = user.get("username")
    if role != "admin" and up_row["author_username"] != username:
        return None, "Forbidden: Only the update author or an administrator may publish/unpublish this update."

    if is_published:
        sql = """
            UPDATE work_order_updates
            SET is_published = TRUE, published_at = NOW(), published_by = %s
            WHERE id = %s
            RETURNING id, work_order_id, is_published, published_at, published_by
        """
        row = conn.execute(sql, (username, update_id)).fetchone()
    else:
        sql = """
            UPDATE work_order_updates
            SET is_published = FALSE, published_at = NULL, published_by = NULL
            WHERE id = %s
            RETURNING id, work_order_id, is_published, published_at, published_by
        """
        row = conn.execute(sql, (update_id,)).fetchone()

    return dict(row), None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Public Progress Query Service (Strict Privacy Redactions)
# ─────────────────────────────────────────────────────────────────────────────

def list_public_progress_updates(
    conn,
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    Retrieve published repair progress updates for the public main page.
    Strict Privacy Guarantees:
      - Returns ONLY records where is_published = TRUE.
      - NEVER returns internal_notes, author usernames, account IDs, or private assigned crews.
      - Includes approved attachments with secure download links.
    """
    where_clauses = ["u.is_published = TRUE"]
    params: List[Any] = []

    if status and str(status).strip():
        clean_status = str(status).strip()
        where_clauses.append("u.status = %s")
        params.append(clean_status)

    if search and str(search).strip():
        term = f"%{str(search).strip()}%"
        where_clauses.append("(r.road_name ILIKE %s OR u.work_completed ILIKE %s OR wo.work_order_code ILIKE %s)")
        params.extend([term, term, term])

    where_sql = " AND ".join(where_clauses)

    # 1. Total count
    count_sql = f"""
        SELECT COUNT(*) AS total
        FROM work_order_updates u
        JOIN work_orders wo ON wo.id = u.work_order_id
        JOIN road_damage_reports r ON r.id = wo.report_id
        WHERE {where_sql}
    """
    total = conn.execute(count_sql, params).fetchone()["total"]

    # 2. Page results
    limit_val = max(1, min(int(limit), 100))
    offset_val = max(0, int(offset))

    query_params = list(params) + [limit_val, offset_val]

    sql = f"""
        SELECT
            u.id,
            u.work_order_id,
            wo.work_order_code,
            r.road_name,
            r.damage_type,
            r.severity AS confirmed_severity,
            ST_Y(r.geom) AS latitude,
            ST_X(r.geom) AS longitude,
            u.status,
            u.progress_pct,
            u.work_completed,
            u.blockers,
            u.next_actions,
            u.estimated_completion_date,
            u.published_at,
            u.created_at,
            u.is_correction,
            u.correction_for_id
        FROM work_order_updates u
        JOIN work_orders wo ON wo.id = u.work_order_id
        JOIN road_damage_reports r ON r.id = wo.report_id
        WHERE {where_sql}
        ORDER BY COALESCE(u.published_at, u.created_at) DESC, u.id DESC
        LIMIT %s OFFSET %s
    """
    rows = conn.execute(sql, query_params).fetchall()
    update_ids = [r["id"] for r in rows]

    # 3. Attachments map (approved only)
    attachments_map: Dict[int, List[Dict[str, Any]]] = {uid: [] for uid in update_ids}
    if update_ids:
        att_rows = conn.execute(
            """
            SELECT id, update_id, file_type, original_filename, file_size_bytes, caption
            FROM work_order_update_attachments
            WHERE update_id = ANY(%s) AND is_approved = TRUE
            ORDER BY id ASC
            """,
            (update_ids,),
        ).fetchall()

        for a in att_rows:
            uid = a["update_id"]
            attachments_map[uid].append({
                "id": a["id"],
                "file_type": a["file_type"],
                "filename": a["original_filename"],
                "file_size_bytes": a["file_size_bytes"],
                "caption": a["caption"],
                "download_url": f"/api/work-orders/attachments/{a['id']}",
            })

    # 4. Serialize public shape
    results = []
    for r in rows:
        lat = r.get("latitude")
        lng = r.get("longitude")
        ts = r.get("published_at") or r.get("created_at")
        ts_str = ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if isinstance(ts, datetime) else str(ts)

        results.append({
            "id": r["id"],
            "work_order_id": r["work_order_id"],
            "work_order_code": r["work_order_code"],
            "road_name": r["road_name"],
            "damage_type": r["damage_type"],
            "confirmed_severity": r["confirmed_severity"],
            "latitude": round(float(lat), 6) if lat is not None else None,
            "longitude": round(float(lng), 6) if lng is not None else None,
            "status": r["status"],
            "progress_pct": r["progress_pct"] or 0,
            "work_completed": r["work_completed"],
            "blockers": r["blockers"] or None,
            "next_actions": r["next_actions"] or None,
            "estimated_completion_date": str(r["estimated_completion_date"]) if r.get("estimated_completion_date") else None,
            "published_at": ts_str,
            "is_correction": bool(r.get("is_correction")),
            "correction_for_id": r.get("correction_for_id"),
            "attachments": attachments_map.get(r["id"], []),
        })

    return {
        "total": total,
        "limit": limit_val,
        "offset": offset_val,
        "updates": results,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. Officer Timeline & Attachment Query
# ─────────────────────────────────────────────────────────────────────────────

def list_work_order_updates(conn, wo_id: int) -> List[Dict[str, Any]]:
    """
    Retrieve full update history for a work order (internal staff view).
    Includes internal notes, draft updates, author names, and all attachments.
    """
    rows = conn.execute(
        """
        SELECT id, work_order_id, author_user_id, author_username, status, progress_pct,
               work_completed, blockers, next_actions, estimated_completion_date,
               internal_notes, is_published, published_at, published_by,
               is_correction, correction_for_id, created_at
        FROM work_order_updates
        WHERE work_order_id = %s
        ORDER BY created_at ASC, id ASC
        """,
        (wo_id,),
    ).fetchall()

    update_ids = [r["id"] for r in rows]
    attachments_map: Dict[int, List[Dict[str, Any]]] = {uid: [] for uid in update_ids}
    if update_ids:
        att_rows = conn.execute(
            """
            SELECT id, update_id, work_order_id, file_type, mime_type, original_filename,
                   stored_filename, file_size_bytes, caption, is_approved, created_at
            FROM work_order_update_attachments
            WHERE update_id = ANY(%s)
            ORDER BY id ASC
            """,
            (update_ids,),
        ).fetchall()
        for a in att_rows:
            attachments_map[a["update_id"]].append({
                "id": a["id"],
                "file_type": a["file_type"],
                "mime_type": a["mime_type"],
                "filename": a["original_filename"],
                "stored_filename": a["stored_filename"],
                "file_size_bytes": a["file_size_bytes"],
                "caption": a["caption"],
                "is_approved": a["is_approved"],
                "download_url": f"/api/work-orders/attachments/{a['id']}",
            })

    results = []
    for r in rows:
        item = dict(r)
        ts = item.get("created_at")
        item["created_at"] = ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if isinstance(ts, datetime) else str(ts)
        pub_ts = item.get("published_at")
        item["published_at"] = pub_ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if isinstance(pub_ts, datetime) else None
        item["estimated_completion_date"] = str(item["estimated_completion_date"]) if item.get("estimated_completion_date") else None
        item["attachments"] = attachments_map.get(r["id"], [])
        results.append(item)

    return results


def get_attachment_record(conn, attachment_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve attachment metadata along with its parent update and work order."""
    row = conn.execute(
        """
        SELECT a.id, a.update_id, a.work_order_id, a.file_type, a.mime_type,
               a.original_filename, a.stored_filename, a.file_size_bytes, a.caption,
               a.is_approved, u.is_published, wo.assigned_to
        FROM work_order_update_attachments a
        JOIN work_order_updates u ON u.id = a.update_id
        JOIN work_orders wo ON wo.id = a.work_order_id
        WHERE a.id = %s
        """,
        (attachment_id,),
    ).fetchone()
    return dict(row) if row else None
