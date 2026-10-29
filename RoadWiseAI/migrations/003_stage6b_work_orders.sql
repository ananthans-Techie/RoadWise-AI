-- ============================================================
-- RoadWise AI: Migration 003 — Stage 6B Maintenance Work-Order Tracking
-- Tables: work_orders, work_order_status_history
-- Safely rerunnable and idempotent (never drops existing tables or data)
-- ============================================================

-- 1. work_orders
--    Stores maintenance work orders linked to road_damage_reports.
--    Enforces at most one work order per report (UNIQUE constraint on report_id).
--    Statuses: 'Open', 'Assigned', 'In Progress', 'Completed', 'Deferred'.
CREATE TABLE IF NOT EXISTS work_orders (
    id               BIGSERIAL PRIMARY KEY,
    work_order_code  VARCHAR(30) UNIQUE NOT NULL,
    report_id        BIGINT UNIQUE NOT NULL REFERENCES road_damage_reports(id) ON DELETE CASCADE,
    status           VARCHAR(30) NOT NULL DEFAULT 'Open',
    assigned_to      VARCHAR(150),
    target_date      DATE,
    completed_at     TIMESTAMPTZ,
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_work_orders_report_id
    ON work_orders (report_id);

CREATE INDEX IF NOT EXISTS idx_work_orders_status
    ON work_orders (status);

CREATE INDEX IF NOT EXISTS idx_work_orders_created_at
    ON work_orders (created_at DESC);

-- 2. work_order_status_history
--    Maintains an audit trail of status transitions with timestamps and notes.
CREATE TABLE IF NOT EXISTS work_order_status_history (
    id             BIGSERIAL PRIMARY KEY,
    work_order_id  BIGINT NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    from_status    VARCHAR(30),
    to_status      VARCHAR(30) NOT NULL,
    note           TEXT,
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wo_history_work_order_id
    ON work_order_status_history (work_order_id);

CREATE INDEX IF NOT EXISTS idx_wo_history_changed_at
    ON work_order_status_history (changed_at ASC);

