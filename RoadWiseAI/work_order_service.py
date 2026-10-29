"""
RoadWise AI — Stage 6B: Maintenance Work-Order Tracking Service
work_order_service.py

Provides:
  - Work order creation from road-damage reports (with duplicate prevention)
  - State machine transition validation and audit history recording
  - Query queue with filtering by status, priority tier, and date
  - Individual work order retrieval with linked report context and timeline history
  - Parameterized SQL execution; never mutates reports or priority scores
  - No fake user claims (no logged-in identity assumed)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Dict, Any

logger = logging.getLogger("roadwise.work_orders")

ALLOWED_STATUSES = {"Open", "Assigned", "In Progress", "Completed", "Deferred"}

# Enforced business lifecycle transitions
VALID_TRANSITIONS: dict[str, set[str]] = {
    "Open": {"Assigned", "In Progress", "Deferred", "Completed"},
    "Assigned": {"Open", "In Progress", "Deferred", "Completed"},
    "In Progress": {"Completed", "Deferred", "Assigned", "Open"},
    "Deferred": {"Open", "Assigned", "In Progress"},
    "Completed": {"Open"},  # Re-open if repair fails inspection or defect recurs
}


def validate_transition(current_status: str, new_status: str) -> Tuple[bool, Optional[str]]:
    """
    Validate whether transitioning from current_status to new_status is allowed.
    Returns (is_valid, error_message).
    """
    if not new_status or new_status not in ALLOWED_STATUSES:
        return (
            False,
            f"Invalid status '{new_status}'. Allowed statuses: {', '.join(sorted(ALLOWED_STATUSES))}.",
        )

    if current_status == new_status:
        return True, None

    allowed_next = VALID_TRANSITIONS.get(current_status, set())
    if new_status in allowed_next:
        return True, None

    return (
        False,
        f"Invalid status transition from '{current_status}' to '{new_status}'. "
        f"Allowed next statuses from '{current_status}': {', '.join(sorted(allowed_next)) or 'None'}.",
    )


def format_work_order_code(report_id: int) -> str:
    """Generate a stable, human-readable work order identifier."""
    return f"WO-{int(report_id):04d}"


def create_work_orders(
    conn,
    report_ids: List[int],
    assigned_to: Optional[str] = None,
    target_date: Optional[str] = None,
    notes: Optional[str] = None,
    created_by: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create maintenance work orders for the specified report IDs.

    Guarantees:
      - At most one work order per report (checked in DB and enforced by UNIQUE constraint).
      - If a report already has a work order, it is reported in already_tracked.
      - Missing report IDs are reported in not_found.
      - Successfully created work orders start in 'Open' status and record an initial
        entry in work_order_status_history.
    """
    if not report_ids:
        return {"created": [], "already_tracked": [], "not_found": []}

    cleaned_ids = []
    for rid in report_ids:
        try:
            v = int(rid)
            if v > 0:
                cleaned_ids.append(v)
        except (ValueError, TypeError):
            continue

    if not cleaned_ids:
        return {"created": [], "already_tracked": [], "not_found": []}

    # 1. Identify which reports already have a work order
    existing_rows = conn.execute(
        """
        SELECT id, work_order_code, report_id, status, assigned_to, target_date
        FROM work_orders
        WHERE report_id = ANY(%s)
        """,
        (cleaned_ids,),
    ).fetchall()

    already_tracked = [
        {
            "id": r["id"],
            "work_order_code": r["work_order_code"],
            "report_id": r["report_id"],
            "status": r["status"],
            "assigned_to": r["assigned_to"],
            "target_date": str(r["target_date"]) if r["target_date"] else None,
        }
        for r in existing_rows
    ]
    tracked_report_ids = {r["report_id"] for r in already_tracked}

    # 2. Check existence of remaining reports
    remaining_ids = [rid for rid in cleaned_ids if rid not in tracked_report_ids]
    if not remaining_ids:
        return {
            "created": [],
            "already_tracked": already_tracked,
            "not_found": [],
        }

    valid_report_rows = conn.execute(
        """
        SELECT id, road_name, damage_type, severity
        FROM road_damage_reports
        WHERE id = ANY(%s)
        """,
        (remaining_ids,),
    ).fetchall()

    valid_reports_map = {r["id"]: dict(r) for r in valid_report_rows}
    not_found = [rid for rid in remaining_ids if rid not in valid_reports_map]

    # Clean target_date if provided
    clean_target_date = target_date.strip() if target_date and str(target_date).strip() else None
    clean_assigned_to = assigned_to.strip() if assigned_to and str(assigned_to).strip() else None
    clean_notes = notes.strip() if notes and str(notes).strip() else None

    # 3. Insert new work orders
    created = []
    initial_status = "Assigned" if clean_assigned_to else "Open"

    for rid in remaining_ids:
        if rid in not_found:
            continue

        wo_code = format_work_order_code(rid)
        initial_note = clean_notes or (
            f"Work order created and assigned to {clean_assigned_to}."
            if clean_assigned_to
            else "Work order created from road damage report."
        )

        row = conn.execute(
            """
            INSERT INTO work_orders
                (work_order_code, report_id, status, assigned_to, target_date, notes)
            VALUES
                (%s, %s, %s, %s, %s, %s)
            RETURNING id, work_order_code, report_id, status, assigned_to,
                      target_date, completed_at, notes, created_at, updated_at
            """,
            (wo_code, rid, initial_status, clean_assigned_to, clean_target_date, clean_notes),
        ).fetchone()

        wo_id = row["id"]

        # Record initial status history entry
        conn.execute(
            """
            INSERT INTO work_order_status_history
                (work_order_id, from_status, to_status, note)
            VALUES
                (%s, NULL, %s, %s)
            """,
            (wo_id, initial_status, initial_note),
        )

        wo_dict = serialize_work_order(row)
        wo_dict["report"] = valid_reports_map[rid]
        created.append(wo_dict)

    return {
        "created": created,
        "already_tracked": already_tracked,
        "not_found": not_found,
    }


def serialize_work_order(row: Any) -> dict:
    """Format a work_orders database row into JSON-serializable dictionary."""
    if not row:
        return {}
    r = dict(row)

    created_at = r.get("created_at")
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at_str = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        created_at_str = str(created_at) if created_at else ""

    updated_at = r.get("updated_at")
    if isinstance(updated_at, datetime):
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        updated_at_str = updated_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        updated_at_str = str(updated_at) if updated_at else ""

    completed_at = r.get("completed_at")
    if isinstance(completed_at, datetime):
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=timezone.utc)
        completed_at_str = completed_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        completed_at_str = str(completed_at) if completed_at else None

    target_date = r.get("target_date")
    target_date_str = str(target_date) if target_date else None

    return {
        "id": r.get("id"),
        "work_order_code": r.get("work_order_code"),
        "report_id": r.get("report_id"),
        "status": r.get("status"),
        "assigned_to": r.get("assigned_to"),
        "target_date": target_date_str,
        "completed_at": completed_at_str,
        "notes": r.get("notes") or "",
        "created_at": created_at_str,
        "updated_at": updated_at_str,
    }


def list_work_orders(
    conn,
    status: Optional[str] = None,
    priority_tier: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
) -> List[dict]:
    """
    Query work orders with linked report details and priority ratings.
    Supports filtering by status, priority tier, creation date range, and search keyword.
    """
    clauses: list[str] = []
    params: list = []

    if status and status.strip():
        # Handle comma-separated statuses if provided
        st_parts = [s.strip() for s in status.split(",") if s.strip()]
        if len(st_parts) == 1:
            clauses.append("wo.status = %s")
            params.append(st_parts[0])
        elif len(st_parts) > 1:
            clauses.append("wo.status = ANY(%s)")
            params.append(st_parts)

    if priority_tier and priority_tier.strip():
        clauses.append("ps.priority_tier = %s")
        params.append(priority_tier.strip())

    if date_from and date_from.strip():
        clauses.append("wo.created_at >= %s")
        params.append(date_from.strip())

    if date_to and date_to.strip():
        clauses.append("wo.created_at < (%s::date + INTERVAL '1 day')")
        params.append(date_to.strip())

    if search and search.strip():
        term = f"%{search.strip()}%"
        clauses.append("(wo.work_order_code ILIKE %s OR r.road_name ILIKE %s OR wo.assigned_to ILIKE %s)")
        params.extend([term, term, term])

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = f"""
        SELECT
            wo.id,
            wo.work_order_code,
            wo.report_id,
            wo.status,
            wo.assigned_to,
            wo.target_date,
            wo.completed_at,
            wo.notes,
            wo.created_at,
            wo.updated_at,
            r.road_name,
            r.damage_type,
            r.severity,
            r.description AS report_description,
            r.photo_url,
            r.created_at  AS report_created_at,
            ST_Y(r.geom)  AS latitude,
            ST_X(r.geom)  AS longitude,
            ps.priority_tier,
            ps.priority_score,
            ps.explanation AS priority_explanation
        FROM work_orders wo
        JOIN road_damage_reports r
          ON r.id = wo.report_id
        LEFT JOIN LATERAL (
            SELECT priority_tier, priority_score, explanation
            FROM   report_priority_scores
            WHERE  report_id = r.id
            ORDER  BY scored_at DESC
            LIMIT  1
        ) ps ON TRUE
        {where}
        ORDER BY
            CASE WHEN wo.status = 'In Progress' THEN 1
                 WHEN wo.status = 'Assigned'    THEN 2
                 WHEN wo.status = 'Open'        THEN 3
                 WHEN wo.status = 'Deferred'    THEN 4
                 ELSE 5 END ASC,
            CASE WHEN ps.priority_tier = 'High'   THEN 1
                 WHEN ps.priority_tier = 'Medium'  THEN 2
                 ELSE 3 END ASC,
            COALESCE(ps.priority_score, 0) DESC,
            wo.created_at DESC
    """

    rows = conn.execute(sql, params).fetchall()
    results = []
    for row in rows:
        wo_dict = serialize_work_order(row)
        lat = row.get("latitude")
        lng = row.get("longitude")
        rep_created = row.get("report_created_at")
        rep_created_str = (
            rep_created.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if isinstance(rep_created, datetime)
            else str(rep_created) if rep_created else ""
        )

        wo_dict["linked_report"] = {
            "id": row.get("report_id"),
            "road_name": row.get("road_name"),
            "damage_type": row.get("damage_type"),
            "severity": row.get("severity"),
            "description": row.get("report_description") or "",
            "photo_url": row.get("photo_url"),
            "created_at": rep_created_str,
            "latitude": round(float(lat), 6) if lat is not None else None,
            "longitude": round(float(lng), 6) if lng is not None else None,
            "priority_tier": row.get("priority_tier") or "Not scored",
            "priority_score": float(row["priority_score"]) if row.get("priority_score") is not None else None,
            "priority_explanation": row.get("priority_explanation") or "",
        }
        results.append(wo_dict)

    return results


def get_work_order(conn, wo_id: Optional[int] = None, work_order_code: Optional[str] = None, report_id: Optional[int] = None) -> Optional[dict]:
    """
    Retrieve full work order details including linked report and status audit history.
    Can be looked up by work_order id, work_order_code, or report_id.
    """
    if wo_id:
        where = "wo.id = %s"
        param = wo_id
    elif work_order_code:
        where = "wo.work_order_code = %s"
        param = work_order_code
    elif report_id:
        where = "wo.report_id = %s"
        param = report_id
    else:
        return None

    sql = f"""
        SELECT
            wo.id,
            wo.work_order_code,
            wo.report_id,
            wo.status,
            wo.assigned_to,
            wo.target_date,
            wo.completed_at,
            wo.notes,
            wo.created_at,
            wo.updated_at,
            r.road_name,
            r.damage_type,
            r.severity,
            r.description AS report_description,
            r.photo_url,
            r.created_at  AS report_created_at,
            ST_Y(r.geom)  AS latitude,
            ST_X(r.geom)  AS longitude,
            ps.priority_tier,
            ps.priority_score,
            ps.explanation AS priority_explanation
        FROM work_orders wo
        JOIN road_damage_reports r
          ON r.id = wo.report_id
        LEFT JOIN LATERAL (
            SELECT priority_tier, priority_score, explanation
            FROM   report_priority_scores
            WHERE  report_id = r.id
            ORDER  BY scored_at DESC
            LIMIT  1
        ) ps ON TRUE
        WHERE {where}
        LIMIT 1
    """

    row = conn.execute(sql, (param,)).fetchone()
    if not row:
        return None

    wo_dict = serialize_work_order(row)
    lat = row.get("latitude")
    lng = row.get("longitude")
    rep_created = row.get("report_created_at")
    rep_created_str = (
        rep_created.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if isinstance(rep_created, datetime)
        else str(rep_created) if rep_created else ""
    )

    wo_dict["linked_report"] = {
        "id": row.get("report_id"),
        "road_name": row.get("road_name"),
        "damage_type": row.get("damage_type"),
        "severity": row.get("severity"),
        "description": row.get("report_description") or "",
        "photo_url": row.get("photo_url"),
        "created_at": rep_created_str,
        "latitude": round(float(lat), 6) if lat is not None else None,
        "longitude": round(float(lng), 6) if lng is not None else None,
        "priority_tier": row.get("priority_tier") or "Not scored",
        "priority_score": float(row["priority_score"]) if row.get("priority_score") is not None else None,
        "priority_explanation": row.get("priority_explanation") or "",
    }

    # Fetch status history timeline
    history_rows = conn.execute(
        """
        SELECT id, from_status, to_status, note, changed_at
        FROM work_order_status_history
        WHERE work_order_id = %s
        ORDER BY changed_at ASC, id ASC
        """,
        (row["id"],),
    ).fetchall()

    history = []
    for h in history_rows:
        ts = h.get("changed_at")
        ts_str = (
            ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if isinstance(ts, datetime)
            else str(ts)
        )
        history.append({
            "id": h["id"],
            "from_status": h["from_status"],
            "to_status": h["to_status"],
            "note": h["note"] or "",
            "changed_at": ts_str,
        })

    wo_dict["status_history"] = history
    return wo_dict


def update_work_order(
    conn,
    wo_id: int,
    new_status: Optional[str] = None,
    status_note: Optional[str] = None,
    assigned_to: Optional[str] = None,
    target_date: Optional[str] = None,
    notes: Optional[str] = None,
    update_assignment: bool = False,
    update_target_date: bool = False,
    update_notes: bool = False,
    changed_by: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Update work order fields and/or perform a status transition.

    Enforces lifecycle transitions and records an entry in work_order_status_history
    whenever status changes or a status_note is supplied.

    Returns (updated_wo_dict, error_message).
    """
    cur_row = conn.execute(
        """
        SELECT id, work_order_code, report_id, status, assigned_to, target_date,
               completed_at, notes, created_at, updated_at
        FROM work_orders
        WHERE id = %s
        """,
        (wo_id,),
    ).fetchone()

    if not cur_row:
        return None, f"Work order with ID {wo_id} not found."

    current_status = cur_row["status"]
    status_changed = False
    target_status = current_status

    if new_status and new_status.strip() and new_status.strip() != current_status:
        target_status = new_status.strip()
        is_valid, err = validate_transition(current_status, target_status)
        if not is_valid:
            return None, err
        status_changed = True

    # Build SET clause
    set_clauses = ["updated_at = NOW()"]
    set_params = []

    if status_changed:
        set_clauses.append("status = %s")
        set_params.append(target_status)

        if target_status == "Completed":
            set_clauses.append("completed_at = NOW()")
        elif current_status == "Completed" and target_status != "Completed":
            # Reopening or moving away from completed
            set_clauses.append("completed_at = NULL")

    if update_assignment:
        clean_assigned = assigned_to.strip() if assigned_to and assigned_to.strip() else None
        set_clauses.append("assigned_to = %s")
        set_params.append(clean_assigned)

    if update_target_date:
        clean_target = target_date.strip() if target_date and str(target_date).strip() else None
        set_clauses.append("target_date = %s")
        set_params.append(clean_target)

    if update_notes:
        clean_notes = notes.strip() if notes and str(notes).strip() else None
        set_clauses.append("notes = %s")
        set_params.append(clean_notes)

    set_params.append(wo_id)
    sql = f"""
        UPDATE work_orders
        SET {', '.join(set_clauses)}
        WHERE id = %s
        RETURNING id, work_order_code, report_id, status, assigned_to,
                  target_date, completed_at, notes, created_at, updated_at
    """
    updated_row = conn.execute(sql, set_params).fetchone()

    # Record history if status changed or a note was explicitly attached
    if status_changed or (status_note and status_note.strip()):
        note_text = (status_note or "").strip() or (
            f"Status changed from {current_status} to {target_status}."
            if status_changed
            else "Work order details updated."
        )
        conn.execute(
            """
            INSERT INTO work_order_status_history
                (work_order_id, from_status, to_status, note)
            VALUES
                (%s, %s, %s, %s)
            """,
            (wo_id, current_status if status_changed else None, target_status, note_text),
        )

    # Return full serialized work order with history
    full_wo = get_work_order(conn, wo_id=wo_id)
    return full_wo, None

