-- ============================================================
-- Migration 004: Stage 7 Pilot Users & Audit Columns
-- Non-destructive, safely rerunnable
-- ============================================================

-- Pilot users table for authenticated municipal staff and administrators
CREATE TABLE IF NOT EXISTS pilot_users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(32) NOT NULL DEFAULT 'staff',
    full_name VARCHAR(128),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pilot_users_username ON pilot_users(username);
CREATE INDEX IF NOT EXISTS idx_pilot_users_role ON pilot_users(role);

-- Non-destructively add operator audit columns to work-order tables
ALTER TABLE work_orders ADD COLUMN IF NOT EXISTS updated_by VARCHAR(64);
ALTER TABLE work_order_status_history ADD COLUMN IF NOT EXISTS changed_by VARCHAR(64);

