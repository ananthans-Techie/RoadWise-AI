-- ============================================================
-- Migration 005: Stage 8 Public Road-Repair Progress Updates
-- Safely rerunnable and non-destructive
-- ============================================================

-- Work order progress updates (append-only timeline)
CREATE TABLE IF NOT EXISTS work_order_updates (
    id SERIAL PRIMARY KEY,
    work_order_id INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    author_user_id INTEGER REFERENCES pilot_users(id) ON DELETE SET NULL,
    author_username VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    progress_pct INTEGER DEFAULT 0 CHECK (progress_pct >= 0 AND progress_pct <= 100),
    work_completed TEXT NOT NULL,
    blockers TEXT,
    next_actions TEXT,
    estimated_completion_date DATE,
    internal_notes TEXT,
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMPTZ,
    published_by VARCHAR(64),
    is_correction BOOLEAN NOT NULL DEFAULT FALSE,
    correction_for_id INTEGER REFERENCES work_order_updates(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wo_updates_wo_id ON work_order_updates(work_order_id);
CREATE INDEX IF NOT EXISTS idx_wo_updates_published ON work_order_updates(is_published, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_wo_updates_created_at ON work_order_updates(created_at DESC);

-- Attachments associated with progress updates (photos and PDF reports)
CREATE TABLE IF NOT EXISTS work_order_update_attachments (
    id SERIAL PRIMARY KEY,
    update_id INTEGER NOT NULL REFERENCES work_order_updates(id) ON DELETE CASCADE,
    work_order_id INTEGER NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    file_type VARCHAR(16) NOT NULL, -- 'image' or 'pdf'
    mime_type VARCHAR(64) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    stored_filename VARCHAR(255) NOT NULL UNIQUE,
    file_size_bytes INTEGER NOT NULL,
    caption VARCHAR(255),
    is_approved BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_wo_attachments_update_id ON work_order_update_attachments(update_id);
CREATE INDEX IF NOT EXISTS idx_wo_attachments_wo_id ON work_order_update_attachments(work_order_id);
