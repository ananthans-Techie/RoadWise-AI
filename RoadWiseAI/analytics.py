"""
RoadWise AI — Stage 5: GIS Analytics & Maintenance Prioritisation
analytics.py

Provides:
  - Parameterised filtered report queries (date, damage type, severity)
  - Aggregated summary counts (total, by type, by severity, by priority tier)
  - Hotspot/cluster query using PostGIS ST_ClusterDBSCAN in UTM Zone 43N (SRID 32643)
  - Priority scoring engine with configurable weights, transparent factor breakdown
  - Road-segment matching using metre-accurate ST_DWithin on projected geometry

Scoring model (documented, configurable via PRIORITY_WEIGHTS):
  severity_pts    — user-confirmed severity label (Critical=40, Moderate=25, Minor=10)
  road_class_pts  — National Highway=30, State Highway=20, District Road=10, Other=5
  traffic_pts     — derived from AADT bucket; missing data → 0 (shown in explanation)
  junction_pts    — junction count within 200m buffer; capped at 15 pts
  repeat_pts      — nearby open reports ≤150m, up to 10 pts
  age_pts         — age in days; older unfixed reports score higher (cap 10 pts)

Total raw score is clamped to 0–100.
Tier:  High ≥60 | Medium ≥30 | Low <30

All SQL queries use parameterised placeholders; no string interpolation of
user-supplied values. Credentials are never exposed to the frontend.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger("roadwise.analytics")


# ─────────────────────────────────────────────────────────────────────────────
# Scoring weights (configurable — edit values here to tune the model)
# ─────────────────────────────────────────────────────────────────────────────
PRIORITY_WEIGHTS: dict = {
    # Severity base points
    "severity": {"Critical": 40, "Moderate": 25, "Minor": 10},

    # Road classification points (label must match road_segments.road_class)
    "road_class": {
        "National Highway": 30,
        "State Highway": 20,
        "District Road": 10,
        "Other": 5,
    },

    # AADT traffic volume bands
    "traffic_aadt": [
        (50_000, 20),   # ≥50 000 vehicles/day  → 20 pts
        (20_000, 15),   # ≥20 000               → 15 pts
        (10_000, 10),   # ≥10 000               → 10 pts
        (2_000,   5),   # ≥ 2 000               →  5 pts
        (0,        0),  # below → 0 pts
    ],

    # Per-junction within 200 m buffer
    "per_junction": 2.5,
    "junction_cap": 15.0,

    # Repeat reports within 150 m, up to max_repeat_pts
    "per_repeat": 3.0,
    "repeat_cap": 10.0,

    # Age-related urgency: +1 pt per 30 days, capped
    "per_30_days": 1.0,
    "age_cap": 10.0,

    # Spatial matching: max distance in metres to associate a report with a road segment
    "match_radius_m": 100,
}

# UTM Zone 43N (covers Kerala): used for metre-accurate distance calculations
_PROJ_SRID = 32643


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_filter_clause(
    date_from: Optional[str],
    date_to: Optional[str],
    damage_type: Optional[str],
    severity: Optional[str],
) -> tuple[str, list]:
    """Return (WHERE clause string, parameter list) for filtered report queries."""
    clauses: list[str] = []
    params: list = []

    if date_from:
        clauses.append("created_at >= %s")
        params.append(date_from)

    if date_to:
        # Include the full end day
        clauses.append("created_at < (%s::date + INTERVAL '1 day')")
        params.append(date_to)

    if damage_type:
        clauses.append("damage_type = %s")
        params.append(damage_type)

    if severity:
        clauses.append("severity = %s")
        params.append(severity)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# ─────────────────────────────────────────────────────────────────────────────
# Public query functions
# ─────────────────────────────────────────────────────────────────────────────

def filtered_reports(
    conn,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    damage_type: Optional[str] = None,
    severity: Optional[str] = None,
) -> list[dict]:
    """
    Return reports matching all supplied filters.
    Each row includes priority_tier and priority_score if a score has been computed.
    """
    where, params = _build_filter_clause(date_from, date_to, damage_type, severity)

    sql = f"""
        SELECT
            r.id, r.road_name, r.damage_type, r.severity, r.description,
            r.photo_url, r.created_at,
            ST_Y(r.geom) AS latitude,
            ST_X(r.geom) AS longitude,
            ps.priority_tier,
            ps.priority_score,
            ps.explanation,
            wo.id AS work_order_id,
            wo.work_order_code,
            wo.status AS work_order_status
        FROM road_damage_reports r
        LEFT JOIN LATERAL (
            SELECT priority_tier, priority_score, explanation
            FROM   report_priority_scores
            WHERE  report_id = r.id
            ORDER  BY scored_at DESC
            LIMIT  1
        ) ps ON TRUE
        LEFT JOIN work_orders wo
               ON wo.report_id = r.id
        {where}
        ORDER BY r.created_at ASC, r.id ASC
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def summary_counts(
    conn,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    damage_type: Optional[str] = None,
    severity: Optional[str] = None,
) -> dict:
    """
    Return aggregated counts for the current filter set:
      total, by_damage_type, by_severity, by_priority_tier
    """
    where, params = _build_filter_clause(date_from, date_to, damage_type, severity)

    # Total
    total_row = conn.execute(
        f"SELECT COUNT(*) AS n FROM road_damage_reports r {where}", params
    ).fetchone()
    total = int(total_row["n"])

    # By damage type
    rows_dt = conn.execute(
        f"""
        SELECT damage_type, COUNT(*) AS n
        FROM road_damage_reports r {where}
        GROUP BY damage_type ORDER BY n DESC
        """,
        params,
    ).fetchall()
    by_damage_type = {r["damage_type"]: int(r["n"]) for r in rows_dt}

    # By severity
    rows_sv = conn.execute(
        f"""
        SELECT severity, COUNT(*) AS n
        FROM road_damage_reports r {where}
        GROUP BY severity ORDER BY n DESC
        """,
        params,
    ).fetchall()
    by_severity = {r["severity"]: int(r["n"]) for r in rows_sv}

    # By priority tier (only reports that have been scored)
    rows_pr = conn.execute(
        f"""
        SELECT ps.priority_tier, COUNT(*) AS n
        FROM road_damage_reports r
        JOIN LATERAL (
            SELECT priority_tier
            FROM   report_priority_scores
            WHERE  report_id = r.id
            ORDER  BY scored_at DESC LIMIT 1
        ) ps ON TRUE
        {where}
        GROUP BY ps.priority_tier
        """,
        params,
    ).fetchall()
    by_priority = {r["priority_tier"]: int(r["n"]) for r in rows_pr}

    return {
        "total": total,
        "by_damage_type": by_damage_type,
        "by_severity": by_severity,
        "by_priority_tier": by_priority,
    }


def hotspot_clusters(conn, min_reports: int = 3, cluster_eps_m: float = 300.0) -> dict:
    """
    Cluster report locations using ST_ClusterDBSCAN (DBSCAN algorithm) in UTM 43N.
    eps_m: cluster radius in metres (default 300 m).
    min_pts: minimum reports to form a cluster (default 3 — configurable via min_reports param).

    Returns:
      {
        "algorithm": "ST_ClusterDBSCAN",
        "srid_used": 32643,
        "eps_metres": 300,
        "min_reports": 3,
        "total_reports": N,
        "included_reports": M,   # reports belonging to a cluster (non-noise)
        "threshold_met": bool,
        "clusters": [
            {
              "cluster_id": 0,
              "count": 5,
              "centroid_lat": ...,
              "centroid_lng": ...,
              "dominant_severity": "Critical",
              "dominant_damage_type": "Pothole",
              "report_ids": [1,2,3,4,5]
            }, ...
        ]
      }
    """
    # Check total reports
    total = int(conn.execute(
        "SELECT COUNT(*) AS n FROM road_damage_reports"
    ).fetchone()["n"])

    if total < min_reports:
        return {
            "algorithm": "ST_ClusterDBSCAN",
            "srid_used": _PROJ_SRID,
            "eps_metres": cluster_eps_m,
            "min_reports": min_reports,
            "total_reports": total,
            "included_reports": 0,
            "threshold_met": False,
            "message": (
                f"Hotspot view requires at least {min_reports} reports; "
                f"only {total} found. Add more field reports to see density clusters."
            ),
            "clusters": [],
        }

    rows = conn.execute(
        """
        WITH clustered AS (
            SELECT
                id,
                damage_type,
                severity,
                ST_ClusterDBSCAN(
                    ST_Transform(geom, %(srid)s),
                    eps   := %(eps)s,
                    minpoints := %(minpts)s
                ) OVER () AS cluster_id,
                ST_Y(geom) AS lat,
                ST_X(geom) AS lng
            FROM road_damage_reports
        )
        SELECT
            cluster_id,
            COUNT(*)                            AS cnt,
            AVG(lat)                            AS centroid_lat,
            AVG(lng)                            AS centroid_lng,
            MODE() WITHIN GROUP (ORDER BY severity)       AS dominant_severity,
            MODE() WITHIN GROUP (ORDER BY damage_type)    AS dominant_damage_type,
            ARRAY_AGG(id ORDER BY id)           AS report_ids
        FROM clustered
        WHERE cluster_id IS NOT NULL
        GROUP BY cluster_id
        ORDER BY cnt DESC
        """,
        {"srid": _PROJ_SRID, "eps": cluster_eps_m, "minpts": min_reports},
    ).fetchall()

    included = sum(int(r["cnt"]) for r in rows)

    clusters = []
    for r in rows:
        clusters.append({
            "cluster_id": r["cluster_id"],
            "count": int(r["cnt"]),
            "centroid_lat": round(float(r["centroid_lat"]), 6),
            "centroid_lng": round(float(r["centroid_lng"]), 6),
            "dominant_severity": r["dominant_severity"],
            "dominant_damage_type": r["dominant_damage_type"],
            "report_ids": list(r["report_ids"]),
        })

    return {
        "algorithm": "ST_ClusterDBSCAN",
        "srid_used": _PROJ_SRID,
        "eps_metres": cluster_eps_m,
        "min_reports": min_reports,
        "total_reports": total,
        "included_reports": included,
        "threshold_met": True,
        "clusters": clusters,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Priority scoring engine
# ─────────────────────────────────────────────────────────────────────────────

def _tier(score: float) -> str:
    if score >= 60:
        return "High"
    if score >= 30:
        return "Medium"
    return "Low"


def compute_priority_score(conn, report: dict, weights: dict = PRIORITY_WEIGHTS) -> dict:
    """
    Compute a transparent, explainable priority score for one report dict.
    report must have: id, severity, damage_type, latitude, longitude, created_at
    Returns a dict ready to INSERT into report_priority_scores.
    """
    rid = report["id"]
    lat = float(report["latitude"])
    lng = float(report["longitude"])
    severity = report["severity"]
    created_at = report.get("created_at")
    factors: list[str] = []
    missing: list[str] = []

    # ── 1. Severity ──────────────────────────────────────────────
    sev_pts = float(weights["severity"].get(severity, 0))
    factors.append(f"Severity ({severity}) = {sev_pts:.0f} pts")

    # ── 2. Road classification ────────────────────────────────────
    road_class_pts = 0.0
    matched_segment_id = None
    match_distance_m = None
    road_class_label = None

    try:
        seg_row = conn.execute(
            """
            SELECT id, road_class,
                   ST_Distance(
                       ST_Transform(geom, %(srid)s),
                       ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s)
                   ) AS dist_m
            FROM road_segments
            WHERE ST_DWithin(
                ST_Transform(geom, %(srid)s),
                ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s),
                %(radius)s
            )
            ORDER BY dist_m ASC
            LIMIT 1
            """,
            {"srid": _PROJ_SRID, "lat": lat, "lng": lng,
             "radius": weights["match_radius_m"]},
        ).fetchone()

        if seg_row:
            matched_segment_id = seg_row["id"]
            match_distance_m = round(float(seg_row["dist_m"]), 1)
            road_class_label = seg_row["road_class"]
            road_class_pts = float(weights["road_class"].get(road_class_label, 5))
            factors.append(
                f"Road class ({road_class_label}, matched {match_distance_m} m away)"
                f" = {road_class_pts:.0f} pts"
            )
        else:
            missing.append("road classification (no road segment within "
                           f"{weights['match_radius_m']} m — import road network to enable)")
            factors.append(f"Road class = 0 pts (no matched segment)")
    except psycopg.Error as exc:
        logger.warning("Road segment match error for report %s: %s", rid, exc)
        missing.append("road classification (road_segments table may be empty)")

    # ── 3. Traffic volume ─────────────────────────────────────────
    traffic_pts = 0.0
    try:
        tc_row = conn.execute(
            """
            SELECT aadt
            FROM traffic_counts
            WHERE ST_DWithin(
                ST_Transform(count_point, %(srid)s),
                ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s),
                %(radius)s
            )
            ORDER BY ST_Distance(
                ST_Transform(count_point, %(srid)s),
                ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s)
            ) ASC
            LIMIT 1
            """,
            {"srid": _PROJ_SRID, "lat": lat, "lng": lng,
             "radius": weights["match_radius_m"]},
        ).fetchone()

        if tc_row and tc_row["aadt"] is not None:
            aadt = int(tc_row["aadt"])
            for threshold, pts in weights["traffic_aadt"]:
                if aadt >= threshold:
                    traffic_pts = float(pts)
                    break
            factors.append(f"Traffic AADT ({aadt:,}) = {traffic_pts:.0f} pts")
        else:
            missing.append("traffic counts (no AADT measurement within "
                           f"{weights['match_radius_m']} m — import traffic survey data)")
            factors.append("Traffic = 0 pts (no traffic count data available)")
    except psycopg.Error as exc:
        logger.warning("Traffic count match error for report %s: %s", rid, exc)
        missing.append("traffic counts (traffic_counts table may be empty)")

    # ── 4. Junction density ───────────────────────────────────────
    junction_pts = 0.0
    try:
        jct_row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM junctions
            WHERE ST_DWithin(
                ST_Transform(geom, %(srid)s),
                ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s),
                200
            )
            """,
            {"srid": _PROJ_SRID, "lat": lat, "lng": lng},
        ).fetchone()

        jct_count = int(jct_row["n"]) if jct_row else 0
        junction_pts = min(
            float(jct_count) * weights["per_junction"],
            weights["junction_cap"]
        )
        if jct_count == 0:
            missing.append("junction density (no junction data within 200 m — import junction dataset)")
        factors.append(f"Junctions within 200 m ({jct_count}) = {junction_pts:.1f} pts")
    except psycopg.Error as exc:
        logger.warning("Junction density error for report %s: %s", rid, exc)
        missing.append("junction density (junctions table may be empty)")

    # ── 5. Repeat nearby reports ──────────────────────────────────
    repeat_pts = 0.0
    try:
        rep_row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM road_damage_reports
            WHERE id <> %(rid)s
              AND ST_DWithin(
                  ST_Transform(geom, %(srid)s),
                  ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s),
                  150
              )
            """,
            {"srid": _PROJ_SRID, "lat": lat, "lng": lng, "rid": rid},
        ).fetchone()

        repeat_count = int(rep_row["n"]) if rep_row else 0
        repeat_pts = min(
            float(repeat_count) * weights["per_repeat"],
            weights["repeat_cap"]
        )
        factors.append(f"Nearby reports within 150 m ({repeat_count}) = {repeat_pts:.1f} pts")
    except psycopg.Error as exc:
        logger.warning("Repeat report count error for report %s: %s", rid, exc)

    # ── 6. Report age urgency ─────────────────────────────────────
    age_pts = 0.0
    try:
        if created_at:
            if isinstance(created_at, str):
                # Parse ISO string
                ts = datetime.fromisoformat(created_at.replace(" UTC", "+00:00"))
            elif isinstance(created_at, datetime):
                ts = created_at
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = None

            if ts:
                age_days = max(0, (datetime.now(timezone.utc) - ts).days)
                age_pts = min(
                    float(age_days // 30) * weights["per_30_days"],
                    weights["age_cap"]
                )
                factors.append(f"Report age ({age_days} days) = {age_pts:.1f} pts")
    except Exception as exc:
        logger.warning("Age calculation error for report %s: %s", rid, exc)

    # ── Total ─────────────────────────────────────────────────────
    raw_score = sev_pts + road_class_pts + traffic_pts + junction_pts + repeat_pts + age_pts
    score = round(min(100.0, max(0.0, raw_score)), 2)
    tier = _tier(score)

    explanation_parts = ["Factor breakdown: " + " | ".join(factors)]
    if missing:
        explanation_parts.append(
            "Missing inputs (score 0 for these factors): " + "; ".join(missing)
        )
    explanation_parts.append(
        "This score is decision support for human review and is not an official engineering assessment."
    )

    return {
        "report_id": rid,
        "priority_tier": tier,
        "priority_score": score,
        "severity_pts": sev_pts,
        "road_class_pts": road_class_pts,
        "traffic_pts": traffic_pts,
        "junction_pts": junction_pts,
        "repeat_pts": repeat_pts,
        "age_pts": age_pts,
        "matched_segment_id": matched_segment_id,
        "match_distance_m": match_distance_m,
        "explanation": " | ".join(explanation_parts),
    }


def upsert_priority_score(conn, score_dict: dict) -> None:
    """Insert a new priority score row (history-preserving append)."""
    conn.execute(
        """
        INSERT INTO report_priority_scores
            (report_id, priority_tier, priority_score,
             severity_pts, road_class_pts, traffic_pts,
             junction_pts, repeat_pts, age_pts,
             matched_segment_id, match_distance_m, explanation)
        VALUES
            (%(report_id)s, %(priority_tier)s, %(priority_score)s,
             %(severity_pts)s, %(road_class_pts)s, %(traffic_pts)s,
             %(junction_pts)s, %(repeat_pts)s, %(age_pts)s,
             %(matched_segment_id)s, %(match_distance_m)s, %(explanation)s)
        """,
        score_dict,
    )


def compute_all_priorities(conn) -> list[dict]:
    """Score every report and persist results. Returns list of score dicts."""
    rows = conn.execute(
        """
        SELECT id, severity, damage_type, created_at,
               ST_Y(geom) AS latitude, ST_X(geom) AS longitude
        FROM road_damage_reports
        ORDER BY id
        """
    ).fetchall()

    results = []
    for row in rows:
        score_dict = compute_priority_score(conn, dict(row))
        upsert_priority_score(conn, score_dict)
        results.append(score_dict)

    return results


def external_data_status(conn) -> dict:
    """
    Report whether the optional external datasets have been imported.
    Used by the frontend to show which priority inputs are available.
    """
    status = {}
    for table, label in [
        ("road_segments", "Road network"),
        ("junctions", "Junction locations"),
        ("traffic_counts", "Traffic counts (AADT)"),
    ]:
        try:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            count = int(row["n"])
            status[table] = {"label": label, "count": count, "available": count > 0}
        except psycopg.Error:
            status[table] = {"label": label, "count": 0, "available": False}

    return status
