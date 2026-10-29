-- ============================================================
-- RoadWise AI: Migration 001
-- Table: road_damage_reports with PostGIS geometry
-- Safely rerunnable and idempotent (never drops tables or erases reports)
-- ============================================================

-- 1. Enable PostGIS extension for spatial data operations
CREATE EXTENSION IF NOT EXISTS postgis;

-- 2. Create road_damage_reports table
CREATE TABLE IF NOT EXISTS road_damage_reports (
    id BIGSERIAL PRIMARY KEY,
    road_name VARCHAR(200) NOT NULL,
    damage_type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    description TEXT,
    photo_url VARCHAR(255),
    geom geometry(Point, 4326) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. Create GiST spatial index on geometry column for fast spatial queries
CREATE INDEX IF NOT EXISTS idx_road_damage_reports_geom
ON road_damage_reports USING GIST (geom);
