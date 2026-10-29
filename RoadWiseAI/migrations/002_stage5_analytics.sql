-- ============================================================
-- RoadWise AI: Migration 002 — Stage 5 GIS Analytics
-- Tables: road_segments, junctions, traffic_counts, priority scores
-- Safely rerunnable and idempotent (never drops existing tables or data)
-- ============================================================

-- Ensure PostGIS is active (already done in 001, kept for standalone safety)
CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------
-- 2a. road_segments
--   Stores imported road centreline geometries with classification.
--   Source: Kerala PWD OpenData, OSM Overpass, or manually imported GeoJSON.
--   Import using: psql -f import_road_segments.sql gisdb
--   Coordinate system: WGS84 (SRID 4326) for storage; SRID 32643
--   (UTM Zone 43N) used in queries for metre-accurate distance buffers.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS road_segments (
    id          BIGSERIAL PRIMARY KEY,
    osm_id      BIGINT,                       -- OpenStreetMap way ID (nullable)
    road_name   VARCHAR(300),
    road_class  VARCHAR(50) NOT NULL          -- 'National Highway', 'State Highway', 'District Road', 'Other'
                DEFAULT 'Other',
    geom        GEOMETRY(LineString, 4326) NOT NULL,
    source      VARCHAR(200)  DEFAULT 'manual',  -- Data provenance
    source_date DATE,                            -- Publication or survey date of source data
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_road_segments_geom
    ON road_segments USING GIST (geom);

-- ---------------------------------------------------------------
-- 2b. junctions
--   Road intersection points derived from road_segments or imported.
--   Used to compute junction density within a buffer around a report.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS junctions (
    id          BIGSERIAL PRIMARY KEY,
    geom        GEOMETRY(Point, 4326) NOT NULL,
    source      VARCHAR(200) DEFAULT 'manual',
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_junctions_geom
    ON junctions USING GIST (geom);

-- ---------------------------------------------------------------
-- 2c. traffic_counts
--   AADT (Annual Average Daily Traffic) or peak-period counts
--   measured at a specific point on a road segment.
--   Source: NCRB/Kerala PWD traffic surveys, or user-supplied CSV.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS traffic_counts (
    id              BIGSERIAL PRIMARY KEY,
    road_segment_id BIGINT REFERENCES road_segments(id) ON DELETE SET NULL,
    count_point     GEOMETRY(Point, 4326),      -- GPS location of the count station
    aadt            INTEGER,                    -- Annual Average Daily Traffic
    peak_hour       INTEGER,                    -- Peak-hour volume if available
    survey_year     INTEGER,
    source          VARCHAR(300) DEFAULT 'manual',
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_traffic_counts_geom
    ON traffic_counts USING GIST (count_point);

-- ---------------------------------------------------------------
-- 2d. report_priority_scores
--   Computed priority tier and score for each report.
--   Refreshed by the /api/analytics/priority endpoint.
--   Keeps scoring history; most recent row per report is the live score.
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS report_priority_scores (
    id                 BIGSERIAL PRIMARY KEY,
    report_id          BIGINT NOT NULL REFERENCES road_damage_reports(id) ON DELETE CASCADE,
    priority_tier      VARCHAR(20) NOT NULL,   -- 'High', 'Medium', 'Low'
    priority_score     NUMERIC(5,2) NOT NULL,  -- 0–100
    severity_pts       NUMERIC(5,2) DEFAULT 0,
    road_class_pts     NUMERIC(5,2) DEFAULT 0,
    traffic_pts        NUMERIC(5,2) DEFAULT 0,
    junction_pts       NUMERIC(5,2) DEFAULT 0,
    repeat_pts         NUMERIC(5,2) DEFAULT 0,
    age_pts            NUMERIC(5,2) DEFAULT 0,
    matched_segment_id BIGINT REFERENCES road_segments(id) ON DELETE SET NULL,
    match_distance_m   NUMERIC(8,2),           -- metres from report to matched segment
    explanation        TEXT,                   -- Human-readable factor breakdown
    scored_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_priority_scores_report_id
    ON report_priority_scores (report_id);

CREATE INDEX IF NOT EXISTS idx_priority_scores_scored_at
    ON report_priority_scores (scored_at DESC);
