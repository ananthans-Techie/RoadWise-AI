# RoadWise AI: Windows Operations Runbook (Stage 7: Field-Pilot Readiness)

This runbook provides step-by-step instructions for operating, monitoring, securing, and backing up RoadWise AI during controlled local and municipal field pilots on Windows.

---

## 1. System Requirements & Architecture

- **Operating System:** Windows 10 or Windows 11 (64-bit)
- **Python Runtime:** Python 3.10+ (located in virtual environment `..\.venv\`)
- **Database Engine:** PostgreSQL 18 with PostGIS extension on `127.0.0.1:5432`
- **WSGI Application Server:** Waitress (multithreaded Windows-native WSGI server) or production-safe fallback (`debug=False`)
- **Storage:** Local persistent disk storage for database and photograph uploads in `static/uploads/`

---

## 2. Pre-Flight Service Verification

Before launching the pilot server, verify that the prerequisite Windows services and database are ready:

### A. Check PostgreSQL Windows Service
Open PowerShell as Administrator:
```powershell
# Check service status
Get-Service -Name postgresql*

# If stopped, start the PostgreSQL 18 service
Start-Service -Name postgresql-x64-18
```

### B. Verify Environment Configuration
Ensure `.env` exists in the `RoadWiseAI/` directory and contains:
```ini
DATABASE_URL=postgresql://postgres:<your_password>@127.0.0.1:5432/gisdb
FLASK_SECRET_KEY=<32_byte_random_hex_string>
FLASK_DEBUG=0
```
> **Security Policy:**
> - Never commit `.env` to Git or expose credentials.
> - `FLASK_DEBUG=0` ensures the interactive debugger is disabled, preventing remote code execution.

### C. Apply Database Migrations
Run the non-destructive migration runner to ensure all tables, spatial indexes, and pilot user schemas exist:
```powershell
..\.venv\Scripts\python.exe migrate.py
```
Applies:
- `001_create_road_damage_reports.sql` (Reports & PostGIS spatial point index)
- `002_stage5_analytics.sql` (Road network, junctions, traffic counts, priority scores)
- `003_stage6b_work_orders.sql` (Work orders & status audit history)
- `004_stage7_pilot_users.sql` (Pilot users & audit tracking columns)
- `005_stage8_progress_updates.sql` (Work order progress updates, audit corrections, attachment metadata)

---

## 3. Pilot User & Access Management

To enforce role-based access control without hardcoding default passwords or exposing secrets, create pilot user accounts via `manage_users.py`:

### A. Create a Municipal Staff Account
```powershell
# Interactive password entry (recommended):
..\.venv\Scripts\python.exe manage_users.py create --username officer_pattom --role staff --full-name "Pattom Field Officer"
```

### B. Create an Administrator Account
```powershell
..\.venv\Scripts\python.exe manage_users.py create --username super_admin --role admin --full-name "Chief Municipal Engineer"
```

### C. List Registered Accounts
```powershell
..\.venv\Scripts\python.exe manage_users.py list
```
*(Lists username, role, active status, and creation timestamp; never prints password hashes).*

### D. Change Password or Deactivate User
```powershell
# Change password:
..\.venv\Scripts\python.exe manage_users.py passwd --username officer_pattom

# Disable an account:
..\.venv\Scripts\python.exe manage_users.py deactivate --username officer_pattom
```

---

## 4. Starting the Application Server

For field pilots, use the production WSGI runner (`run_pilot.py`). It enforces `debug=False`, masks credentials, and serves requests via Waitress multi-threading:

```powershell
# Standard local start (Waitress WSGI on 127.0.0.1:5000)
..\.venv\Scripts\python.exe run_pilot.py

# Optional: custom port and worker thread count
..\.venv\Scripts\python.exe run_pilot.py --host 127.0.0.1 --port 5000 --threads 4
```

### Startup Log Output:
```text
=================================================================
RoadWise AI: Pilot Configuration Loaded
Database:    postgresql://postgres:***@127.0.0.1:5432/gisdb
Debug Mode:  DISABLED
Host/Port:   http://127.0.0.1:5000
=================================================================
Starting Waitress WSGI server (4 worker threads)...
```
*(Notice all database passwords and credentials remain strictly masked).*

---

## 5. Health Check & Diagnostics

Probe the application health endpoint using PowerShell:
```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/health | ConvertTo-Json -Depth 4
```

### Expected Response:
```json
{
  "status": "ok",
  "project": "RoadWise AI",
  "study_area": "Thiruvananthapuram, Kerala",
  "storage": {
    "mode": "postgresql_postgis",
    "postgis_ready": true,
    "postgis_version": "3.4.0 ...",
    "report_count": 8
  },
  "pilot": {
    "persistent_uploads": true,
    "debug_mode": false,
    "auth_configured": true
  }
}
```

---

## 6. Stopping the Server Cleanly

To stop the running pilot server:
- If running in an active terminal: press `Ctrl + C`. Waitress gracefully finishes in-flight requests and shuts down worker threads.
- If running in the background: identify the process PID via `Get-Process python` and stop it via `Stop-Process -Id <PID>`.

---

## 7. Backup and Recovery Runbook

To ensure business continuity and zero data loss, use `backup_restore.py`:

### A. Create Full Daily Backup
```powershell
..\.venv\Scripts\python.exe backup_restore.py backup --output-dir backups
```
Creates:
- `backups/db/roadwise_backup_<YYYYMMDD_HHMMSS>.sql`: Complete schema and data dump for reports, work orders, progress updates, history, and users.
- `backups/photos/roadwise_photos_<YYYYMMDD_HHMMSS>.zip`: Timestamped compressed archive of all uploaded JPEG photographs in `static/uploads/`.
- `backups/attachments/roadwise_attachments_<YYYYMMDD_HHMMSS>.zip`: Compressed archive of all officer repair photos and PDFs in `uploads/work_orders/`.

### B. Testing Disaster Recovery (Safe Verification)
> **Safety Rule:** Never restore directly over the operational `gisdb` database! Always verify restore procedures into a separate, isolated database (e.g. `gisdb_restore_test`).

1. **Create an isolated temporary database in PostgreSQL:**
   ```powershell
   # Using psql or pgAdmin:
   CREATE DATABASE gisdb_restore_test;
   ```

2. **Restore database backup into the isolated test database:**
   ```powershell
   ..\.venv\Scripts\python.exe backup_restore.py restore-db --file backups/db/roadwise_backup_XXXX.sql --target-url postgresql://postgres:YOUR_PASSWORD@127.0.0.1:5432/gisdb_restore_test
   ```

3. **Verify record integrity in test database:**
   ```powershell
   ..\.venv\Scripts\python.exe -c "import psycopg; conn=psycopg.connect('postgresql://postgres:YOUR_PASSWORD@127.0.0.1:5432/gisdb_restore_test'); print('Reports restored:', conn.execute('SELECT COUNT(*) FROM road_damage_reports').fetchone()); print('Work orders restored:', conn.execute('SELECT COUNT(*) FROM work_orders').fetchone()); print('Progress updates restored:', conn.execute('SELECT COUNT(*) FROM work_order_updates').fetchone());"
   ```

4. **Restore photos and attachments into isolated temporary folders:**
   ```powershell
   ..\.venv\Scripts\python.exe backup_restore.py restore-photos --file backups/photos/roadwise_photos_XXXX.zip --target-dir temp_restore_photos
   ..\.venv\Scripts\python.exe backup_restore.py restore-attachments --file backups/attachments/roadwise_attachments_XXXX.zip --target-dir temp_restore_attachments
   ```

5. **Clean up test database & temporary folders:**
   ```powershell
   DROP DATABASE gisdb_restore_test;
   Remove-Item -Recurse -Force temp_restore_photos, temp_restore_attachments
   ```

---

## 8. Common Troubleshooting Scenarios

| Symptom | Cause | Solution |
| :--- | :--- | :--- |
| `Permission denied (0x0000271D/10013)` or `Connection refused` | PostgreSQL 18 Windows service is not running | Open Services (`services.msc`) or run `Start-Service postgresql-x64-18` in PowerShell. |
| `401 Unauthorized` on creating or updating work orders | Action attempted without staff login | Click **🔐 Staff Login** in the top navigation and authenticate with your staff credentials. |
| `Port 5000 already in use` | Another process is holding port 5000 | Find process: `Get-NetTCPConnection -LocalPort 5000` and terminate it, or specify `--port 5001`. |
| `DATABASE_URL is not configured` | `.env` missing or contains `YOUR_...` placeholder | Edit `.env` with actual PostgreSQL credentials. |
| `Missing inputs in priority explanation` | Sample GIS arterial network not yet loaded | Run `..\.venv\Scripts\python.exe import_gis_data.py --sample`. |

