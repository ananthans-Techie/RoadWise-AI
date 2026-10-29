"""
RoadWise AI — Stage 6A: Maintenance Work-Order Export Service
export_service.py

Generates:
  - PDF  maintenance work-order documents  (reportlab)
  - CSV  spreadsheets for office tracking  (stdlib csv)
  - GeoJSON  for GIS import               (stdlib json)

Design principles:
  - Read-only: never modifies reports, priority scores, photos, or any DB row.
  - No AI-confirmed confusion: AI-predicted values are always labelled as
    "AI-predicted (not confirmed)" in every output format.
  - CSV injection safety: every cell value is stripped of leading
    formula-trigger characters (=, +, -, @, TAB, CR) per OWASP guidelines.
  - Parameterised SQL only — filter inputs are validated before calling here.
  - DATABASE_URL and credentials are never included in any output.
  - Google Live Traffic data is never used or referenced.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("roadwise.export")

# ─────────────────────────────────────────────────────────────────────────────
# Disclaimer text (matches analytics.py wording)
# ─────────────────────────────────────────────────────────────────────────────
PRIORITY_DISCLAIMER = (
    "Priority scores are decision support for human review and are not an "
    "official engineering assessment. Scores incorporate severity, road "
    "classification, traffic volume (AADT), junction density, nearby repeat "
    "incidents, and report age. Missing GIS layers score 0 for those factors."
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared query helper
# ─────────────────────────────────────────────────────────────────────────────

def fetch_export_rows(
    conn,
    report_ids: Optional[list[int]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    damage_type: Optional[str] = None,
    severity: Optional[str] = None,
) -> list[dict]:
    """
    Fetch full export data for the requested reports from PostgreSQL/PostGIS.

    Either report_ids (list of ints, already validated) or filter params
    are used.  Returns a list of dicts with all work-order fields populated.

    Never modifies any table row.  Returns [] when no rows match.
    """
    clauses: list[str] = []
    params: list = []

    if report_ids:
        # psycopg ANY(%s) with a list — safe parameterised query
        clauses.append("r.id = ANY(%s)")
        params.append(report_ids)
    else:
        if date_from:
            clauses.append("r.created_at >= %s")
            params.append(date_from)
        if date_to:
            clauses.append("r.created_at < (%s::date + INTERVAL '1 day')")
            params.append(date_to)
        if damage_type:
            clauses.append("r.damage_type = %s")
            params.append(damage_type)
        if severity:
            clauses.append("r.severity = %s")
            params.append(severity)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    sql = f"""
        SELECT
            r.id,
            r.road_name,
            r.damage_type,
            r.severity,
            r.description,
            r.photo_url,
            r.created_at,
            ST_Y(r.geom)  AS latitude,
            ST_X(r.geom)  AS longitude,
            ps.priority_tier,
            ps.priority_score,
            ps.severity_pts,
            ps.road_class_pts,
            ps.traffic_pts,
            ps.junction_pts,
            ps.repeat_pts,
            ps.age_pts,
            ps.explanation,
            ps.matched_segment_id,
            ps.match_distance_m,
            ps.scored_at,
            seg.road_name   AS segment_road_name,
            seg.road_class  AS segment_road_class,
            seg.osm_id      AS segment_osm_id,
            seg.source      AS segment_source,
            seg.source_date AS segment_source_date,
            wo.work_order_code,
            wo.status       AS work_order_status
        FROM road_damage_reports r
        LEFT JOIN LATERAL (
            SELECT priority_tier, priority_score,
                   severity_pts, road_class_pts, traffic_pts,
                   junction_pts, repeat_pts, age_pts,
                   explanation, matched_segment_id, match_distance_m, scored_at
            FROM   report_priority_scores
            WHERE  report_id = r.id
            ORDER  BY scored_at DESC
            LIMIT  1
        ) ps ON TRUE
        LEFT JOIN road_segments seg
               ON seg.id = ps.matched_segment_id
        LEFT JOIN work_orders wo
               ON wo.report_id = r.id
        {where}
        ORDER BY
            CASE WHEN ps.priority_tier = 'High'   THEN 1
                 WHEN ps.priority_tier = 'Medium'  THEN 2
                 ELSE 3 END ASC,
            COALESCE(ps.priority_score, 0) DESC,
            r.created_at ASC
    """

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# CSV export
# ─────────────────────────────────────────────────────────────────────────────

_FORMULA_CHARS = re.compile(r"^[=+\-@\t\r]")


def _csv_safe(value) -> str:
    """
    Return a string safe from CSV formula-injection attacks.
    Prepends a single-quote prefix to values that begin with =, +, -, @, TAB, CR.
    Ref: OWASP CSV Injection.
    """
    s = "" if value is None else str(value).strip()
    if s and _FORMULA_CHARS.match(s):
        s = "'" + s
    return s


_CSV_HEADERS = [
    "Report ID",
    "Work Order ID",
    "Work Order Status",
    "Report Date (UTC)",
    "Road Name (User Confirmed)",
    "Damage Type (User Confirmed)",
    "Severity (User Confirmed)",
    "Description",
    "Latitude",
    "Longitude",
    "Priority Tier",
    "Priority Score (0–100)",
    "Factor: Severity Points",
    "Factor: Road Class Points",
    "Factor: Traffic (AADT) Points",
    "Factor: Junction Density Points",
    "Factor: Repeat Incidents Points",
    "Factor: Report Age Points",
    "Matched Road Segment Name",
    "Matched Road Class",
    "Matched Segment ID",
    "Segment Data Source",
    "Segment Source Date",
    "Match Distance (m)",
    "Priority Scored At (UTC)",
    "Priority Factor Explanation",
    "Photo Reference",
    "Disclaimer",
]


def build_csv(rows: list[dict]) -> bytes:
    """
    Generate a UTF-8 encoded CSV work-order.
    Returns raw bytes ready to send as a file download.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL)
    writer.writerow(_CSV_HEADERS)

    for r in rows:
        # Format timestamps
        created_at = r.get("created_at")
        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            created_at_str = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            created_at_str = str(created_at) if created_at else ""

        scored_at = r.get("scored_at")
        if isinstance(scored_at, datetime):
            if scored_at.tzinfo is None:
                scored_at = scored_at.replace(tzinfo=timezone.utc)
            scored_at_str = scored_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            scored_at_str = str(scored_at) if scored_at else "Not yet scored"

        lat = r.get("latitude")
        lng = r.get("longitude")
        lat_str = f"{float(lat):.6f}" if lat is not None else ""
        lng_str = f"{float(lng):.6f}" if lng is not None else ""

        priority_score = r.get("priority_score")
        score_str = f"{float(priority_score):.2f}" if priority_score is not None else "Not scored"

        def pts(key):
            v = r.get(key)
            return f"{float(v):.1f}" if v is not None else "0.0"

        seg_source_date = r.get("segment_source_date")
        seg_source_date_str = str(seg_source_date) if seg_source_date else "Not available"

        match_dist = r.get("match_distance_m")
        match_dist_str = f"{float(match_dist):.1f}" if match_dist is not None else "No road segment matched"

        writer.writerow([
            _csv_safe(r.get("id")),
            _csv_safe(r.get("work_order_code") or "None"),
            _csv_safe(r.get("work_order_status") or "Untracked"),
            _csv_safe(created_at_str),
            _csv_safe(r.get("road_name")),
            _csv_safe(r.get("damage_type")),
            _csv_safe(r.get("severity")),
            _csv_safe(r.get("description") or ""),
            _csv_safe(lat_str),
            _csv_safe(lng_str),
            _csv_safe(r.get("priority_tier") or "Not scored"),
            _csv_safe(score_str),
            _csv_safe(pts("severity_pts")),
            _csv_safe(pts("road_class_pts")),
            _csv_safe(pts("traffic_pts")),
            _csv_safe(pts("junction_pts")),
            _csv_safe(pts("repeat_pts")),
            _csv_safe(pts("age_pts")),
            _csv_safe(r.get("segment_road_name") or "No road segment matched"),
            _csv_safe(r.get("segment_road_class") or "Not available"),
            _csv_safe(r.get("matched_segment_id") or ""),
            _csv_safe(r.get("segment_source") or "Not available"),
            _csv_safe(seg_source_date_str),
            _csv_safe(match_dist_str),
            _csv_safe(scored_at_str),
            _csv_safe(r.get("explanation") or "Priority not yet computed. Run Recalculate Priorities."),
            _csv_safe(r.get("photo_url") or "No photo attached"),
            _csv_safe(PRIORITY_DISCLAIMER),
        ])

    return buf.getvalue().encode("utf-8-sig")  # BOM for Excel compatibility


# ─────────────────────────────────────────────────────────────────────────────
# GeoJSON export
# ─────────────────────────────────────────────────────────────────────────────

def build_geojson(rows: list[dict]) -> bytes:
    """
    Generate a GeoJSON FeatureCollection for GIS import.
    Each feature is a Point with all work-order properties.
    Coordinates are [longitude, latitude] per GeoJSON spec (RFC 7946).
    """
    features = []
    for r in rows:
        lat = r.get("latitude")
        lng = r.get("longitude")
        if lat is None or lng is None:
            continue

        created_at = r.get("created_at")
        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            created_at_iso = created_at.astimezone(timezone.utc).isoformat()
        else:
            created_at_iso = str(created_at) if created_at else None

        scored_at = r.get("scored_at")
        if isinstance(scored_at, datetime):
            if scored_at.tzinfo is None:
                scored_at = scored_at.replace(tzinfo=timezone.utc)
            scored_at_iso = scored_at.astimezone(timezone.utc).isoformat()
        else:
            scored_at_iso = str(scored_at) if scored_at else None

        priority_score = r.get("priority_score")
        seg_source_date = r.get("segment_source_date")

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [round(float(lng), 6), round(float(lat), 6)],
            },
            "properties": {
                # Core report fields (all user-confirmed)
                "report_id":             r.get("id"),
                "work_order_id":          r.get("work_order_code") or "None",
                "work_order_status":      r.get("work_order_status") or "Untracked",
                "report_date_utc":       created_at_iso,
                "road_name":             r.get("road_name"),
                "damage_type":           r.get("damage_type"),
                "severity":              r.get("severity"),
                "description":           r.get("description") or "",
                "latitude":              round(float(lat), 6),
                "longitude":             round(float(lng), 6),
                "photo_url":             r.get("photo_url") or "",
                # Priority (computed, not AI-predicted)
                "priority_tier":         r.get("priority_tier") or "Not scored",
                "priority_score":        float(priority_score) if priority_score is not None else None,
                "factor_severity_pts":   float(r["severity_pts"])    if r.get("severity_pts")    is not None else None,
                "factor_road_class_pts": float(r["road_class_pts"])  if r.get("road_class_pts")  is not None else None,
                "factor_traffic_pts":    float(r["traffic_pts"])     if r.get("traffic_pts")     is not None else None,
                "factor_junction_pts":   float(r["junction_pts"])    if r.get("junction_pts")    is not None else None,
                "factor_repeat_pts":     float(r["repeat_pts"])      if r.get("repeat_pts")      is not None else None,
                "factor_age_pts":        float(r["age_pts"])         if r.get("age_pts")         is not None else None,
                "priority_explanation":  r.get("explanation") or "Not yet scored.",
                "priority_scored_at":    scored_at_iso,
                # Matched road segment (GIS data — not AI)
                "segment_id":            r.get("matched_segment_id"),
                "segment_road_name":     r.get("segment_road_name"),
                "segment_road_class":    r.get("segment_road_class"),
                "segment_osm_id":        r.get("segment_osm_id"),
                "segment_source":        r.get("segment_source"),
                "segment_source_date":   str(seg_source_date) if seg_source_date else None,
                "match_distance_m":      float(r["match_distance_m"]) if r.get("match_distance_m") is not None else None,
                # Metadata
                "disclaimer":            PRIORITY_DISCLAIMER,
                "exported_at_utc":       datetime.now(timezone.utc).isoformat(),
            },
        }
        features.append(feature)

    collection = {
        "type": "FeatureCollection",
        "name": "RoadWise AI — Maintenance Work Orders",
        "features": features,
        "metadata": {
            "project":    "RoadWise AI",
            "study_area": "Thiruvananthapuram, Kerala, India",
            "crs":        "WGS 84 (EPSG:4326)",
            "disclaimer": PRIORITY_DISCLAIMER,
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    }
    return json.dumps(collection, ensure_ascii=False, indent=2).encode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# PDF export  (reportlab)
# ─────────────────────────────────────────────────────────────────────────────

def _tier_color(tier: Optional[str]):
    """Return (R, G, B) tuple matching project severity palette."""
    from reportlab.lib import colors
    if tier == "High":
        return colors.HexColor("#ef4444")
    if tier == "Medium":
        return colors.HexColor("#f59e0b")
    return colors.HexColor("#10b981")


def build_pdf(rows: list[dict], title: str = "Maintenance Work Order") -> bytes:
    """
    Generate a printable PDF work-order document.
    Returns raw bytes.

    Layout per report:
      - Report ID header + severity badge
      - Location, road, damage, photo reference
      - Priority score bar + factor breakdown
      - Engineering note reminder
    Overall footer: export timestamp + disclaimer.
    """
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        HRFlowable,
        PageTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    PAGE_W, PAGE_H = A4
    MARGIN = 18 * mm
    CONTENT_W = PAGE_W - 2 * MARGIN

    buf = io.BytesIO()

    # ── Styles ────────────────────────────────────────────────────────────────
    styles = getSampleStyleSheet()

    h1 = ParagraphStyle(
        "RW_H1",
        parent=styles["Heading1"],
        fontSize=17,
        leading=22,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=2 * mm,
    )
    h2 = ParagraphStyle(
        "RW_H2",
        parent=styles["Heading2"],
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=4 * mm,
        spaceAfter=1 * mm,
    )
    body = ParagraphStyle(
        "RW_Body",
        parent=styles["Normal"],
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#334155"),
    )
    label_style = ParagraphStyle(
        "RW_Label",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#64748b"),
    )
    disclaimer_style = ParagraphStyle(
        "RW_Disclaimer",
        parent=styles["Normal"],
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#94a3b8"),
        fontName="Helvetica-Oblique",
    )
    tier_high = ParagraphStyle(
        "RW_TierH", parent=body, textColor=colors.HexColor("#ef4444"), fontName="Helvetica-Bold", fontSize=9
    )
    tier_med = ParagraphStyle(
        "RW_TierM", parent=body, textColor=colors.HexColor("#b45309"), fontName="Helvetica-Bold", fontSize=9
    )
    tier_low = ParagraphStyle(
        "RW_TierL", parent=body, textColor=colors.HexColor("#047857"), fontName="Helvetica-Bold", fontSize=9
    )

    def tier_style(tier):
        if tier == "High":
            return tier_high
        if tier == "Medium":
            return tier_med
        return tier_low

    # ── Page template ─────────────────────────────────────────────────────────
    exported_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _add_header_footer(canvas, doc):
        canvas.saveState()
        # Header bar
        canvas.setFillColor(colors.HexColor("#0f172a"))
        canvas.rect(0, PAGE_H - 14 * mm, PAGE_W, 14 * mm, fill=1, stroke=0)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.setFillColor(colors.white)
        canvas.drawString(MARGIN, PAGE_H - 9 * mm, "RoadWise AI — Maintenance Work Order")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 9 * mm, f"Thiruvananthapuram, Kerala · Exported {exported_at}")
        # Footer
        canvas.setFillColor(colors.HexColor("#e2e8f0"))
        canvas.rect(0, 0, PAGE_W, 10 * mm, fill=1, stroke=0)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(MARGIN, 3.5 * mm, PRIORITY_DISCLAIMER[:130] + "…")
        canvas.drawRightString(PAGE_W - MARGIN, 3.5 * mm, f"Page {doc.page}")
        canvas.restoreState()

    frame = Frame(MARGIN, 12 * mm, CONTENT_W, PAGE_H - 28 * mm, id="main")
    template = PageTemplate(id="main_page", frames=[frame], onPage=_add_header_footer)
    doc = BaseDocTemplate(buf, pagesize=A4, pageTemplates=[template], title=title)

    # ── Build story ───────────────────────────────────────────────────────────
    story = []

    # Cover info
    report_count = len(rows)
    story.append(Paragraph(title, h1))
    story.append(
        Paragraph(
            f"Study Area: Thiruvananthapuram, Kerala, India &nbsp;·&nbsp; "
            f"Total Reports: <b>{report_count}</b> &nbsp;·&nbsp; Exported: {exported_at}",
            body,
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0")))
    story.append(Spacer(1, 3 * mm))

    for r in rows:
        rid = r.get("id", "?")
        road_name   = r.get("road_name") or "—"
        damage_type = r.get("damage_type") or "—"
        severity    = r.get("severity") or "—"
        description = r.get("description") or ""
        photo_url   = r.get("photo_url") or ""

        lat = r.get("latitude")
        lng = r.get("longitude")
        lat_str = f"{float(lat):.6f}" if lat is not None else "—"
        lng_str = f"{float(lng):.6f}" if lng is not None else "—"

        created_at = r.get("created_at")
        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            date_str = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        else:
            date_str = str(created_at) if created_at else "—"

        priority_tier  = r.get("priority_tier") or "Not scored"
        priority_score = r.get("priority_score")
        score_str = f"{float(priority_score):.1f} / 100" if priority_score is not None else "Not yet scored"
        explanation = r.get("explanation") or "Priority not yet computed. Run Recalculate Priorities from the analytics panel."

        # Segment info
        seg_name   = r.get("segment_road_name") or "No road segment matched"
        seg_class  = r.get("segment_road_class") or "Not available"
        seg_id     = r.get("matched_segment_id")
        seg_source = r.get("segment_source") or "Not available"
        seg_date   = r.get("segment_source_date")
        match_dist = r.get("match_distance_m")
        match_dist_str = f"{float(match_dist):.1f} m" if match_dist is not None else "No match"

        # ── Report header ──────────────────────────────────────────────────────
        sev_color = (
            colors.HexColor("#ef4444") if severity == "Critical"
            else colors.HexColor("#f59e0b") if severity == "Moderate"
            else colors.HexColor("#10b981")
        )
        wo_code = r.get("work_order_code")
        wo_status = r.get("work_order_status") or "Open"
        if wo_code:
            hdr_text = f"Report #{rid} &nbsp;&nbsp;|&nbsp;&nbsp; Work Order: <b>{wo_code}</b> ({wo_status})"
        else:
            hdr_text = f"Report #{rid}"

        header_table = Table(
            [[
                Paragraph(hdr_text, ParagraphStyle(
                    "rhdr", parent=body, fontSize=10.5, fontName="Helvetica",
                    textColor=colors.HexColor("#0f172a")
                )),
                Paragraph(severity, ParagraphStyle(
                    "sev", parent=body, fontSize=9, fontName="Helvetica-Bold",
                    textColor=sev_color, alignment=TA_RIGHT
                )),
            ]],
            colWidths=[CONTENT_W * 0.75, CONTENT_W * 0.25],
        )
        header_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
            ("BOX",        (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(header_table)
        story.append(Spacer(1, 2 * mm))

        # ── Field table ────────────────────────────────────────────────────────
        def lbl(text):
            return Paragraph(text, label_style)
        def val(text):
            return Paragraph(str(text), body)

        field_data = [
            [lbl("Report Date (UTC)"),         val(date_str),
             lbl("Road Name (User Confirmed)"), val(road_name)],
            [lbl("Damage Type (User Confirmed)"), val(damage_type),
             lbl("Severity (User Confirmed)"),    val(severity)],
            [lbl("Latitude"),  val(lat_str),
             lbl("Longitude"), val(lng_str)],
        ]
        if description:
            field_data.append([
                lbl("Description"), Paragraph(description, body), lbl(""), val(""),
            ])
        if photo_url:
            field_data.append([
                lbl("Photo Reference"),
                Paragraph(photo_url, ParagraphStyle("ph", parent=body, textColor=colors.HexColor("#2563eb"))),
                lbl(""), val(""),
            ])

        col_half = CONTENT_W / 4
        field_table = Table(field_data, colWidths=[col_half] * 4)
        field_table.setStyle(TableStyle([
            ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(field_table)
        story.append(Spacer(1, 2 * mm))

        # ── Segment / road network data ────────────────────────────────────────
        seg_data = [
            [lbl("Matched Road Segment"),  val(seg_name),
             lbl("Road Class"),            val(seg_class)],
            [lbl("Segment ID"),            val(str(seg_id) if seg_id else "None"),
             lbl("Match Distance"),        val(match_dist_str)],
            [lbl("Data Source"),           val(seg_source),
             lbl("Source Date"),           val(str(seg_date) if seg_date else "Not available")],
        ]
        seg_table = Table(seg_data, colWidths=[col_half] * 4)
        seg_table.setStyle(TableStyle([
            ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#e2e8f0")),
            ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(seg_table)
        story.append(Spacer(1, 2 * mm))

        # ── Priority score ─────────────────────────────────────────────────────
        t_color = _tier_color(priority_tier)
        pct = min(100, max(0, float(priority_score))) if priority_score is not None else 0
        bar_filled = CONTENT_W * (pct / 100)

        priority_header = Table(
            [[
                Paragraph(
                    f"<b>Priority Score:</b> {score_str}",
                    body
                ),
                Paragraph(priority_tier, tier_style(priority_tier)),
            ]],
            colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4],
        )
        priority_header.setStyle(TableStyle([
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",  (0, 0), (-1, -1), 0),
            ("ALIGN",        (1, 0), (1, 0), "RIGHT"),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(priority_header)

        # Horizontal score bar (drawn via a tiny 2-column table)
        bar_table = Table(
            [["", ""]],
            colWidths=[bar_filled, max(0.01, CONTENT_W - bar_filled)],
            rowHeights=[4],
        )
        bar_table.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (0, 0), t_color),
            ("BACKGROUND",    (1, 0), (1, 0), colors.HexColor("#e2e8f0")),
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]))
        story.append(bar_table)
        story.append(Spacer(1, 1.5 * mm))

        # Factor breakdown
        story.append(Paragraph(explanation, label_style))
        story.append(Spacer(1, 1 * mm))
        story.append(Paragraph(
            "⚠ Priority scores are decision support for human review; not an official engineering assessment.",
            disclaimer_style,
        ))
        story.append(Spacer(1, 4 * mm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e2e8f0")))
        story.append(Spacer(1, 3 * mm))

    if not rows:
        story.append(Paragraph("No reports matched the requested criteria.", body))

    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Safe filename helper
# ─────────────────────────────────────────────────────────────────────────────

def safe_export_filename(prefix: str, ext: str) -> str:
    """
    Produce a safe, sanitised download filename.
    Only alphanumeric characters, hyphens, and underscores are kept.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_prefix = re.sub(r"[^a-zA-Z0-9_\-]", "_", prefix)[:40]
    return f"{safe_prefix}_{ts}.{ext}"

