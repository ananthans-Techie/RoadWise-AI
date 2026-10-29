# RoadWise AI: PostgreSQL 18 + PostGIS Setup Guide

RoadWise AI stores road-damage report details and their spatial point geometry in PostgreSQL 18 with PostGIS. Uploaded photographs remain as files in `static/uploads`; the database stores each photograph's relative URL path.

---

## Prerequisites

1. **PostgreSQL 18** installed and running on `127.0.0.1:5432`.
2. A database named `gisdb` created in PostgreSQL.
3. PostGIS enabled (or permissions for the user to execute `CREATE EXTENSION IF NOT EXISTS postgis`).

---

## 1. Environment Configuration (.env)

RoadWise AI reads the database connection string from the `DATABASE_URL` environment variable loaded via `.env`.

1. Copy `.env.example` to `.env` in the project root or `RoadWiseAI/` folder:
   ```powershell
   copy .env.example .env
   ```
2. Open `.env` and set your local PostgreSQL password:
   ```ini
   DATABASE_URL=postgresql://postgres:YOUR_PASSWORD_HERE@127.0.0.1:5432/gisdb
   ```
   > **Security Note:** `.env` is ignored by Git (`.gitignore`). Never commit credentials or passwords to version control.

---

## 2. Install Dependencies

Using PowerShell in the `RoadWiseAI` folder:

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

---

## 3. Run the Database Migration

RoadWise AI includes version-controlled, safe and idempotent SQL migrations located in `migrations/`:
- `001_create_road_damage_reports.sql`: `road_damage_reports` table & PostGIS GiST index.
- `002_stage5_analytics.sql`: `road_segments`, `junctions`, `traffic_counts`, and `report_priority_scores` tables.
- `003_stage6b_work_orders.sql`: `work_orders` table (with 1-to-1 unique `report_id` constraint) and `work_order_status_history`.
- `004_stage7_pilot_users.sql`: `pilot_users` table for municipal staff and audit tracking columns (`updated_by`, `changed_by`).
- `005_stage8_progress_updates.sql`: `work_order_updates` table (progress pct, work completed, blockers, estimated completion, internal notes, publication flags, append-only corrections) and `work_order_update_attachments` table (validated JPEG/PNG/PDF attachments).

Run the migration script:

```powershell
..\.venv\Scripts\python.exe migrate.py
```

This ensures:
- The `postgis` extension is activated.
- All spatial and relational tables and indexes are ready.
- Safe to rerun at any time (never drops tables, alters existing rows, or deletes photos).

---

## 4. Provision Municipal Staff Account

Create the initial field supervisor account without hardcoding default passwords:

```powershell
..\.venv\Scripts\python.exe manage_users.py create --username officer1 --role staff --full-name "Pattom Field Officer"
```

---

## 5. Run the Application

For development:
```powershell
..\.venv\Scripts\python.exe app.py
```

For production/pilot WSGI on Windows (with Waitress and debug disabled):
```powershell
..\.venv\Scripts\python.exe run_pilot.py
```

The app will verify the database connection and print:
```
RoadWise AI: PostgreSQL 18 + PostGIS connected. road_damage_reports table ready.
 * Running on http://127.0.0.1:5000
```

1. Open your browser to `http://127.0.0.1:5000`.
2. Check `http://127.0.0.1:5000/api/health` to confirm:
   ```json
   {
     "status": "ok",
     "project": "RoadWise AI",
     "study_area": "Thiruvananthapuram, Kerala",
     "storage": {
       "mode": "postgresql_postgis",
       "postgis_ready": true,
       "postgis_version": "...",
       "report_count": 0
     }
   }
   ```

---

## 5. Verify Persistence Across Restarts

1. Open `http://127.0.0.1:5000`.
2. Click on the map to choose a location (or use the photo GPS extraction tool).
3. Fill out the report form and click **Submit Road-Damage Report**. Note the assigned report ID (e.g., Report #1).
4. Refresh your browser page: the report marker and card in the feed remain visible.
5. In your PowerShell terminal, press `Ctrl+C` to stop Flask.
6. Restart the Flask app (`..\.venv\Scripts\python.exe app.py`).
7. Refresh your browser page: the report remains populated on the map and in the feed.

You can also inspect the saved records in pgAdmin Query Tool:

```sql
SELECT id, road_name, damage_type, severity, ST_AsText(geom) AS location, photo_url, created_at
FROM road_damage_reports
ORDER BY id DESC;
```

---

## In-Memory Reports Notice

Any reports that existed only in the old Python in-memory list existed strictly within that process's RAM. Stopping or restarting Flask discards volatile process memory. If an old server instance is still running, you can export the in-memory reports prior to shutdown by copying the JSON from `http://127.0.0.1:5000/api/reports` or running:

```powershell
curl http://127.0.0.1:5000/api/reports -o backup_reports.json
```

All new reports submitted through the web UI are permanently stored in PostgreSQL/PostGIS.

---

## Photo Storage & Backup Utility

Attached photographs are saved to the `static/uploads/` directory on your local machine. The PostgreSQL database stores each photo's relative URL path (e.g. `/static/uploads/report_20261021_...jpg`).

### Automated Backup (Database + Uploads)
Use the included `backup_restore.py` script to generate safe, timestamped snapshots:

```powershell
..\.venv\Scripts\python.exe backup_restore.py backup --output-dir backups
```
Creates:
- `backups/db/roadwise_backup_<timestamp>.sql`: Full PostgreSQL schema and data dump.
- `backups/photos/roadwise_photos_<timestamp>.zip`: Compressed archive of all field photographs.

### Disaster Recovery
To verify disaster recovery, restore into an isolated test database (never over `gisdb`):
```powershell
..\.venv\Scripts\python.exe backup_restore.py restore-db --file backups/db/roadwise_backup_XXXX.sql --target-url postgresql://postgres:YOUR_PASSWORD@127.0.0.1:5432/gisdb_restore_test
..\.venv\Scripts\python.exe backup_restore.py restore-photos --file backups/photos/roadwise_photos_XXXX.zip --target-dir temp_restore_photos
```
See [RUNBOOK_WINDOWS.md](RUNBOOK_WINDOWS.md) for full Windows operations procedures.

---

## Troubleshooting

- **Error: `DATABASE_URL is not configured`:**
  Ensure `.env` exists in the `RoadWiseAI/` or project-root folder and contains `DATABASE_URL=postgresql://...`.
- **Error: `connection to server at "127.0.0.1", port 5432 failed`:**
  Verify in Windows Services (`services.msc`) or pgAdmin that PostgreSQL 18 is running on port 5432.
- **Error: `database "gisdb" does not exist`:**
  In pgAdmin or `psql`, create the database: `CREATE DATABASE gisdb;`.
- **Error: `permission denied to create extension "postgis"`:**
  Ensure the database user has superuser privileges or run `CREATE EXTENSION postgis;` directly in pgAdmin Query Tool.
