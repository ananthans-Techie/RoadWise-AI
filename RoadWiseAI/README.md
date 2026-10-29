# RoadWise AI: Road Damage GIS (Stage 8: Public Road-Repair Progress Updates)

RoadWise AI is a Web GIS platform designed for logging, mapping, and prioritising road damage and maintenance incidents across the study area of **Thiruvananthapuram, Kerala, India**.

Stage 8 introduces **Public Road-Repair Progress Updates**, bridging municipal operations with public accountability through an authenticated officer repair logging interface, an append-only milestone timeline with photo/PDF attachments, and an open, privacy-preserving public progress feed on the main dashboard.

---

## Architecture Overview

- **Web Framework:** Flask 3 (Python)
- **Production WSGI Server:** Waitress (multithreaded Windows-native WSGI server with debug mode disabled)
- **Frontend Basemaps:**
  - **Primary Map:** Leaflet 1.9.4 & OpenStreetMap (interactive markers, PostGIS DBSCAN density clusters)
  - **Contextual Traffic View:** Optional Google Maps JavaScript API `TrafficLayer` (isolated container, visual context only)
- **Spatial Database:** PostgreSQL 18 + PostGIS
- **Coordinate Reference Systems:**
  - **Storage:** WGS 84 (`EPSG:4326`) for lat/lng interoperability
  - **Projected Spatial Analytics:** UTM Zone 43N (`EPSG:32643`) for metre-accurate buffer calculations, road-segment matching, and DBSCAN clustering
- **AI Detection:** Local YOLOv8n object detector for damage classification and bounding box suggestions
- **Multi-Criteria Prioritisation Engine:** Transparent scoring model combining user severity, road classification, AADT traffic counts, junction density, repeat incidents, and report age
- **Work-Order Export Engine:** Clean ReportLab PDF generation, OWASP-sanitised CSV spreadsheet exports, and RFC 7946 GeoJSON exports for GIS applications
- **Maintenance Lifecycle Tracking (Stage 6B):** PostgreSQL-backed work-order queue linking damage reports 1-to-1 to field maintenance tasks with finite state-machine transition validation, assigned personnel/team tracking, target dates, completion timestamps, and non-destructive audit history timelines
- **Field-Pilot Security & Access Control (Stage 7):** Role-based access control (`staff`, `admin`), salted password hashing (`werkzeug.security`), protected work-order endpoints, secure session cookies (`HttpOnly`, `SameSite=Lax`), and safe database/photo recovery tools
- **Public Progress Transparency & Officer Workflows (Stage 8):** Authenticated officer progress update interface, append-only repair milestone history with correction tracking, validated image (JPEG/PNG) and PDF attachments, and a public-facing road repair progress feed with strict operational privacy protections

---

## Stage 8: Public Road-Repair Progress Updates

Stage 8 enables municipal field crews and supervisors to record repair milestones and publish verified progress updates, photos, and engineering documents directly to a public feed viewable by all citizens without logging in.

### 1. Authenticated Officer Update Workflow
- **Role Permissions:**
  - Field staff can post updates to work orders assigned to them or unassigned work orders.
  - Supervisors and administrators (`admin`) can post updates to any work order.
  - Unauthenticated requests receive `401 Unauthorized`.
- **Milestone & Progress Tracking:**
  - `work_completed`: Mandatory description of physical repair actions taken. When transitioning status to `Completed`, a detailed non-empty completion summary is strictly required.
  - `progress_pct`: Explicit percentage completion (0–100%).
  - `status`: Work-order status transition following the established finite state-machine rules (`Open`, `Assigned`, `In Progress`, `Completed`, `Deferred`).
  - `blockers` & `next_actions`: Operational issue tracking and planned next steps.
  - `estimated_completion_date`: Target date for repair completion.
  - `internal_notes`: Private staff-only field for crew instructions or material logs, **strictly redacted from all public responses**.
- **Append-Only Timeline & Audit Corrections:**
  - All updates are recorded as immutable historical timeline entries.
  - If an officer makes a mistake, a correction record can be submitted referencing the earlier update (`is_correction = TRUE`, `correction_for_id = <id>`) rather than silently rewriting database history.

### 2. Public Main-Page Section ("Road Repair Progress")
- **Public Access:** Available on the main dashboard (`#public-progress-panel`) without requiring authentication.
- **Published Milestones Only:** Only updates explicitly approved and published (`is_published = TRUE`) are displayed.
- **Display Fields:** Road name, work order code, public status badge, progress percentage bar, timestamp, work completed summary, blockers, next actions, and approved attachments.
- **Privacy Protections:**
  - Internal notes, author usernames, user account IDs, and private assigned crew names are **never exposed** in public endpoints or HTML elements.
- **Interactivity & GIS Integration:**
  - "📍 View on Map" button smoothly pans and zooms the Leaflet map to the incident location.
  - Filter by repair status (`In Progress`, `Completed`, `Assigned`, etc.).
  - Search by road name or work order code.
  - Client-side and server-side pagination (10 updates per page).

### 3. Secure File Attachment Handling
- **Permitted Formats:** Images (JPEG, PNG) and PDF documents only (max 10 MB).
- **Deep Content Validation:**
  - Images are verified using Pillow (PIL) to ensure genuine image stream decoding.
  - PDFs are verified by inspecting the leading `%PDF-` magic byte sequence.
  - Fake extensions (e.g. `.exe` renamed to `.jpg`) are rejected with clear validation errors.
- **Storage Isolation:** Files are stored in `uploads/work_orders/` (outside the directly browsable `static/` directory) with cryptographically unique filenames `wo_<id>_up_<id>_<uuid>.<ext>`.
- **Path Traversal Protection:** Canonical directory checks prevent directory traversal attacks.
- **Access Control Route (`GET /api/work-orders/attachments/<id>`):**
  - Public visitors can only download attachments if the parent update is published and the attachment is approved.
  - Authenticated staff and admins can access attachments for all work orders.

### 4. Disaster Recovery & Attachment Backups
- `backup_restore.py` includes `work_order_updates` and `work_order_update_attachments` tables in automated database dumps.
- `backup_attachments` archives all progress photos and PDFs into a timestamped zip archive with ZipSlip path traversal protection during restore:
  ```powershell
  ..\.venv\Scripts\python.exe backup_restore.py backup --output-dir backups
  ..\.venv\Scripts\python.exe backup_restore.py restore-attachments --archive backups/attachments_20261029_103000.zip
  ```

---

## Stage 7: Field-Pilot Readiness

Stage 7 hardens RoadWise AI for supervised municipal field trials, establishing role-based access control, production WSGI serving for Windows, disaster recovery backup/restore tooling, masked health diagnostics, and end-to-end pilot runbooks.

### 1. Access Control & Role Management
- **Role Model:**
  - `staff`: Field inspectors and maintenance supervisors authorized to create work orders (`POST /api/work-orders`), update lifecycle statuses (`PATCH /api/work-orders/<id>`), and dispatch repair teams.
  - `admin`: Chief municipal engineers authorized for administrative tasks, user management, and global GIS priority recalculations (`POST /api/analytics/priority/compute`).
  - **Public / Unauthenticated Observers:** Can view the interactive map, inspect public damage markers, and submit citizen road-damage reports (`POST /api/reports`).
- **Endpoint Protection:** Maintenance actions are protected by `@login_required`. Unauthenticated requests receive `401 Unauthorized`.
- **Zero Default Credentials:** No default usernames or passwords exist in code, migrations, or database seeds. Pilot accounts are provisioned via the secure CLI manager:
  ```powershell
  ..\.venv\Scripts\python.exe manage_users.py create --username officer_pattom --role staff --full-name "Pattom Field Officer"
  ```
- **Password Security:** Stored using salted password hashes generated via `werkzeug.security` (`scrypt` / `pbkdf2:sha256`). Plaintext passwords are never stored or logged.
- **Session Security:** Session cookies enforce `HttpOnly=True` (preventing XSS theft), `SameSite="Lax"` (mitigating CSRF), and configurable `SESSION_COOKIE_SECURE`.

### 2. Reliable Windows Operations & Production WSGI
- **Waitress WSGI Runner (`run_pilot.py`):** Serves RoadWise AI on Windows using a production-capable, multithreaded WSGI server with `debug=False` enforced:
  ```powershell
  ..\.venv\Scripts\python.exe run_pilot.py --host 127.0.0.1 --port 5000 --threads 4
  ```
- **Windows Operations Runbook:** Comprehensive guide covering service checks, startup, monitoring, user management, and shutdown: [RUNBOOK_WINDOWS.md](RUNBOOK_WINDOWS.md).
- **Diagnostics Health Check (`/api/health`):** Returns database connectivity, PostGIS readiness, report count, upload persistence, and auth readiness while strictly masking connection strings, passwords, and secrets.

### 3. Data Protection & Disaster Recovery
- **Automated Backup Utility (`backup_restore.py`):**
  - **Database:** Generates SQL dumps using `pg_dump` (or pure-Python fallback):
    ```powershell
    ..\.venv\Scripts\python.exe backup_restore.py backup --output-dir backups
    ```
  - **Photographs:** Compresses all high-resolution field photos in `static/uploads/` into a timestamped zip archive.
- **Safety Overwrite Protection:** The restore utility **refuses to restore over the primary `gisdb` database** unless `--force` is explicitly provided, preventing accidental data loss.
- **ZipSlip Protection:** Photo extraction enforces canonical path validation to prevent directory traversal exploits.
- **Isolated Testing:** Full recovery steps into temporary test databases (`gisdb_restore_test`) are documented in [RUNBOOK_WINDOWS.md](RUNBOOK_WINDOWS.md).

### 4. Field Pilot Workflow & Governance
- **Operational Protocol:** Step-by-step workflow covering photo capture, EXIF extraction, YOLO review, report submission, multi-criteria scoring, work-order creation, lifecycle updates, and official PDF/CSV/GeoJSON export: [PILOT_GUIDE.md](PILOT_GUIDE.md).
- **Decision Support Notice:** AI predictions and priority scores are explicitly decision-support tools for human review and do NOT replace official civil engineering assessments.
- **Synthetic vs Operational GIS Data:** Sample road network and traffic counts (`import_gis_data.py --sample`) are synthetic demonstration baselines; real municipal trials must ingest verified Kerala PWD/NATPAC layers.

### 5. Deployment Status & Boundaries
- **Controlled Local Operation Only:** RoadWise AI is configured and tested for local, on-premise, and municipal intranet pilot operation.
- **No Public / Cloud Infrastructure:** No public cloud instances, cloud storage buckets, or paid cloud resources have been provisioned, deployed, or committed.

---

## Stage 6A: Maintenance Work-Order Exports

### 1. Export Formats & Use Cases
- **Printable PDF Work Order (`/api/export/pdf`):** Formal, printable engineering work-order document generated using `ReportLab`. Features a structured report card per incident with road name, confirmed severity and damage type, coordinates, photo reference, matched road classification, horizontal priority score meter, full factor breakdown, and legal engineering disclaimer.
- **Office Tracking CSV (`/api/export/csv`):** Standardised spreadsheet for municipal tracking and record-keeping with UTF-8 BOM encoding for seamless Microsoft Excel compatibility. Protected against CSV formula injection (OWASP sanitisation).
- **GIS Feature Collection GeoJSON (`/api/export/geojson`):** RFC 7946 compliant GeoJSON `FeatureCollection` with WGS 84 Point geometries `[longitude, latitude]` and full work-order metadata, ready for instant drag-and-drop into QGIS, ArcGIS, or GeoPandas.

### 2. Export Modes
- **Filtered Results:** Exports all reports matching the active date range, damage type, and severity filters selected in the GIS Analytics panel.
- **Selected Reports:** Checkboxes on each report card allow field supervisors to select specific reports (or "Select all") and export only the chosen subset via `report_ids=1,2,3`.

### 3. Included Work-Order Fields
Every export includes the following attributes where available:
- **Work-Order Tracking Status (Stage 6B):** Linked Work Order Code (`WO-0001`) and current lifecycle status (`Open`, `Assigned`, `In Progress`, `Completed`, `Deferred`), or `None` / `Untracked` if no work order exists
- **Report Identification:** Report ID, creation timestamp (UTC)
- **User-Confirmed Road & Defect Details:** Road name, confirmed damage type, confirmed severity, optional user description
- **Spatial Coordinates:** High-precision decimal latitude and longitude (WGS 84)
- **Priority Evaluation:** Priority tier (`High`, `Medium`, `Low`), numeric score (0–100), scored timestamp, and transparent factor contribution breakdown (severity points, road class points, traffic points, junction density points, repeat incident points, report age points)
- **Road Network & GIS Provenance:** Matched segment ID, matched road name, road classification, match distance in metres, dataset source (e.g. Kerala PWD), and source survey date
- **Photo Reference:** Relative URL to stored high-resolution field photograph
- **Safety & Review Disclaimer:** Explicit notice that priority scores are decision support for human review, not an official engineering assessment.

### 4. Safety & Data Integrity Guarantees
- **Strict Read-Only:** Generating exports never alters database state, priority scores, reports, or stored photographs.
- **AI-Confirmed Separation:** AI predictions are never conflated with confirmed field data. All report attributes in exports reflect user-confirmed values.
- **CSV Formula Injection Defence:** All cell values starting with `= `, `+`, `-`, `@`, `\t`, or `\r` are prefixed with `'` to prevent command execution in Excel or Calc.
- **Sanitised Download Filenames:** Generated filenames contain only alphanumeric characters, underscores, and timestamps (`roadwise_workorder_<filters>_<timestamp>.<ext>`).
- **No Google Traffic Data:** Google Live Traffic remains strictly visual in an isolated Leaflet-independent viewer and is never scraped, stored, or exported.
- **Credential Protection:** Backend credentials, `DATABASE_URL`, and server configuration secrets are never exposed in export payloads or download headers.

---

## Stage 6B: Maintenance Work-Order Tracking

Stage 6B introduces an operational lifecycle and maintenance workflow for transforming confirmed road-damage incidents into actionable field work orders, tracking task progression, recording audit history, and coordinating repair efforts.

### 1. Work-Order Workflow & Explicit Creation
- **Explicit Creation:** Work orders are created deliberately by municipal supervisors or engineers selecting reports in the UI and clicking **"⚡ Create Work Order(s)"** (via `POST /api/work-orders`). Exporting a PDF, CSV, or GeoJSON file remains strictly read-only and never automatically generates work orders.
- **One-to-One Mapping:** Each road-damage report maps to at most one maintenance work order. This rule is enforced at both the application level and the database level with a `UNIQUE` constraint on `work_orders.report_id`.
- **Graceful Duplicate Handling:** If a batch of selected reports includes items that are already tracked, RoadWise AI does not create duplicate work orders or overwrite existing tasks. The API returns a clear response categorising created work orders versus already tracked reports (`already_tracked: [{report_id, work_order_code, status}]`), and the UI displays an informative toast notification.
- **Stable Human-Readable Code:** Each work order receives a permanent human-readable identifier formatted as `WO-{report_id:04d}` (e.g. `WO-0001`, `WO-0042`), maintaining direct correspondence with the underlying report ID.

### 2. Lifecycle Statuses & State Machine Transitions
RoadWise AI enforces valid lifecycle state transitions to ensure maintenance records reflect realistic field operations:

```
  ┌─────────┐
  │  Open   │ ◄───────────────────────────┐
  └────┬────┘                             │
       │  ▲                               │
       │  │                               │ Re-open
       ▼  │                               │
  ┌─────────┐                             │
  │Assigned │                             │
  └────┬────┘                             │
       │  ▲                               │
       ▼  │                               │
 ┌───────────┐                            │
 │In Progress│                            │
 └─────┬─────┘                            │
       │                                  │
       ▼                                  │
 ┌───────────┐                            │
 │ Completed ├────────────────────────────┘
 └───────────┘

 [ Deferred ] ◄──► Can transition to/from Open, Assigned, or In Progress
```

| Lifecycle Status | Allowed Next Statuses | Description |
| :--- | :--- | :--- |
| **Open** | `Assigned`, `In Progress`, `Deferred`, `Completed` | Work order logged; awaiting crew allocation or direct action. |
| **Assigned** | `In Progress`, `Open`, `Deferred`, `Completed` | Assigned to a specific maintenance team or contractor. |
| **In Progress** | `Completed`, `Assigned`, `Open`, `Deferred` | Road repair crew actively operating on site. |
| **Completed** | `Open` | Maintenance completed and verified. Transitioning to `Completed` automatically sets `completed_at` to the current timestamp. Can only be reopened to `Open` if subsequent inspection fails. |
| **Deferred** | `Open`, `Assigned`, `In Progress` | Repair postponed due to weather, budget, or scheduling. Cannot transition directly to `Completed` without resuming work. |

- **Same-Status Updates:** Submitting an update with the same status is permitted to allow updating `assigned_to`, `target_date`, or `notes` without modifying the lifecycle status.
- **Completion Timestamp Automation:** Transitioning to `Completed` automatically populates `completed_at = CURRENT_TIMESTAMP`. If a completed work order is reopened to `Open`, `completed_at` is safely cleared to `NULL`.

### 3. Non-Destructive Audit History Logging
Every status transition and update records an immutable history entry in `work_order_status_history`:
- **Previous Status:** `from_status`
- **New Status:** `to_status`
- **Transition Note:** Optional supervisor note explaining why the status changed (e.g. *"Patching completed with hot-mix asphalt"* or *"Postponed due to monsoon rains"*).
- **Timestamp:** Exact UTC timestamp of transition (`changed_at`).
- **Data Preservation:** Updating work-order status never mutates or overwrites the original road damage report, GPS coordinates, confirmed severity, AI detection history, or priority evaluation.

### 4. Interactive Maintenance Queue UI
- **Queue KPI Cards:** Real-time counters showing total active work orders grouped by status (`Open`, `In Progress`, `Completed`, and `Total Active`).
- **Interactive Queue Filters:** Instant filtering by lifecycle status (`All`, `Open`, `Assigned`, `In Progress`, `Completed`, `Deferred`), priority tier (`All`, `High`, `Medium`, `Low`), and text search.
- **Report Card Integration:** Each report card in the report feed displays a status badge (e.g. `[WO-0001: In Progress]`) and a **"View Work Order"** quick-action button when tracked.
- **Work Order Management Modal:**
  - View linked report location, confirmed defect type, confirmed severity, priority score with horizontal meter, and thumbnail photograph.
  - Dynamically restricted status dropdown showing only permissible transitions based on the current state.
  - Edit assigned team/contractor, target completion date, and notes.
  - Interactive audit history timeline showing all historical status changes and supervisor notes.

### 5. Export Integration (Stage 6A + 6B)
Work order identifiers and lifecycle statuses are automatically woven into all Stage 6A export formats:
- **CSV Export:** Includes `Work Order ID` and `Work Order Status` columns. Unassigned reports clearly display `None` / `Untracked`.
- **GeoJSON Export:** Features include `work_order_id` / `work_order_code` and `work_order_status` in feature properties, strictly adhering to RFC 7946 WGS 84 coordinate ordering `[longitude, latitude]` with no top-level `crs`.
- **PDF Export:** Printable work order sheets display linked work order badge and status in the header and metadata summary table.

### 6. Security & Limitations
- **No Spoofed User Claims:** Because the application does not have an authentication/login system, work orders and history entries do not claim to record an authenticated username.
- **Google Traffic Isolation:** Work order workflows and priority calculations remain strictly decoupled from Google Maps live traffic.
- **Single Work Order Constraint:** A report cannot have multiple concurrent work orders. If repeat maintenance is needed, the existing work order is reopened.

---

## Stage 5: GIS Analytics & Maintenance Prioritisation

### 1. Combined Report Filters & Summary KPIs
- **Filters:** Start date, End date, Damage type, Severity, and a single-click Reset/Clear action.
- **Consistent Application:** Filters simultaneously filter the database query, the report feed cards, the map markers, and the aggregated summary counts.
- **Summary Counts:** Total filtered reports, counts by damage type, counts by confirmed severity, and counts by priority tier (High, Medium, Low).
- **Date Boundaries:** End-date queries use parameterized SQL with full-day coverage (`created_at < (%s::date + INTERVAL '1 day')`).

### 2. PostGIS DBSCAN Hotspot Clustering
- **Algorithm:** PostGIS `ST_ClusterDBSCAN` executed on projected geometries (`ST_Transform(geom, 32643)`).
- **Configurable Threshold:** Requires a minimum report count (default: 3) before displaying density clusters.
- **Threshold Safeguard:** When fewer than the minimum required reports exist, the map displays a clear advisory banner explaining that the threshold has not been reached, preventing misleading heatmaps from sparse data.
- **Cluster Attributes:** Centroid coordinates, incident count badge, dominant damage type, dominant severity, and contributing report IDs.
- **Marker Layer Isolation:** Users can seamlessly toggle between individual report markers and density cluster views.

### 3. Transparent Maintenance Prioritisation Engine
Each report receives an explainable priority score (0–100) and priority tier (`High` ≥ 60, `Medium` ≥ 30, `Low` < 30) based on weighted multi-criteria decision support:

| Factor | Weight / Formula | Max Pts | Source / Description |
| :--- | :--- | :---: | :--- |
| **Severity** | Critical = 40, Moderate = 25, Minor = 10 | 40 | Confirmed by user in field report |
| **Road Class** | National = 30, State = 20, District = 10, Other = 5 | 30 | Matched via `ST_DWithin` (100 m buffer in SRID 32643) |
| **Traffic (AADT)** | ≥50k = 20, ≥20k = 15, ≥10k = 10, ≥2k = 5, <2k = 0 | 20 | Independent AADT station count within 100 m |
| **Junction Density** | +2.5 pts per junction within 200 m buffer | 15 | Junction points from road network intersections |
| **Repeat Incidents** | +3.0 pts per nearby report within 150 m | 10 | PostGIS spatial proximity check |
| **Report Age** | +1.0 pt per 30 days unaddressed | 10 | Days elapsed since report creation |

- **Missing Data Handling:** If road networks, junctions, or traffic counts have not been imported, the score for those factors is set to 0 and explicitly explained in the factor breakdown string without interrupting the application.
- **Human Decision Support:** All priority scores carry the disclaimer: *"Decision support for human review; not an official engineering assessment."*

### 4. Non-Destructive External GIS Data Import Tool
Use `import_gis_data.py` to import external GIS datasets into PostGIS safely without dropping existing tables or reports:

```powershell
# Check status of GIS layers in PostGIS
..\.venv\Scripts\python.exe import_gis_data.py --status

# Load sample arterial roads, junctions, and AADT stations for Thiruvananthapuram
..\.venv\Scripts\python.exe import_gis_data.py --sample

# Import custom road network from GeoJSON
..\.venv\Scripts\python.exe import_gis_data.py --roads path/to/roads.geojson --source "Kerala PWD 2024"

# Derive intersection junctions automatically from road segments
..\.venv\Scripts\python.exe import_gis_data.py --derive-junctions
```

### 5. Optional Google Live Traffic View
- **Isolated View:** Rendered in a separate Google Maps view container using `google.maps.TrafficLayer`. It is **never** overlaid on Leaflet or OpenStreetMap tiles.
- **Visual Context Only:** Google traffic data is strictly visual. It is never scraped, cached, stored, or converted into priority scores or PostGIS records.
- **API Key Configuration:**
  Set your restricted browser API key in `.env`:
  ```ini
  GOOGLE_MAPS_API_KEY=AIzaSy...YourKeyHere
  ```
  - Apply **Website / HTTP referrer restrictions** in Google Cloud Console (`http://127.0.0.1:5000/*`, `http://localhost:5000/*`).
  - Restrict the key to **Maps JavaScript API**.
  - Ensure billing is enabled for your project.
- **Graceful Fallback:** If no key is set or the placeholder `YOUR_...` remains, the app displays a clear setup guide and all Leaflet mapping, reporting, and GIS analytics remain fully functional.

---

## Local Setup & Quickstart

### 1. Database Migration
Run the version-controlled migration script to ensure all Stage 1–7 tables and spatial indexes are ready:

```powershell
..\.venv\Scripts\python.exe migrate.py
```
Applies:
- `001_create_road_damage_reports.sql`: `road_damage_reports` table & GiST index.
- `002_stage5_analytics.sql`: `road_segments`, `junctions`, `traffic_counts`, and `report_priority_scores` tables and indexes.
- `003_stage6b_work_orders.sql`: `work_orders` (with 1-to-1 unique `report_id` constraint) and `work_order_status_history` tables and indexes.
- `004_stage7_pilot_users.sql`: `pilot_users` table for municipal staff and audit columns (`updated_by`, `changed_by`).

### 2. Create Municipal Staff Pilot User
Securely provision the initial field supervisor account without hardcoded credentials:
```powershell
..\.venv\Scripts\python.exe manage_users.py create --username officer1 --role staff --full-name "Pattom Field Officer"
```

### 3. Seed GIS Datasets (Optional but Recommended)
```powershell
..\.venv\Scripts\python.exe import_gis_data.py --sample
```

### 4. Start the Application Server
Run the production WSGI server with multi-threaded Waitress and debug mode disabled:
```powershell
..\.venv\Scripts\python.exe run_pilot.py
```
Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

---

## Automated Test Suite

Run all isolated unit tests across Stages 1–7:

```powershell
# From the RoadWiseAI project folder:
..\.venv\Scripts\python.exe -m unittest discover -s tests -v

# Or from workspace root:
.\.venv\Scripts\python.exe -m unittest discover -s RoadWiseAI/tests -v
```

Total: **181 tests** across 9 test suites:
- `tests/test_progress_updates.py` (20 tests): Stage 8 progress updates & public repair feed — role authorization (admin can update any work order, staff restricted to assigned or unassigned, unauthorized staff rejected), unauthenticated 401 rejection on post and publish, status transition validation during update, mandatory detailed completion summary when marking `Completed`, append-only audit corrections (`is_correction`, `correction_for_id`), genuine JPEG and PNG validation via Pillow decoding, genuine PDF validation via `%PDF-` magic bytes, rejection of fake/spoofed file extensions and corrupt data, 10 MB file size limit enforcement, secure unique stored filenames with path traversal prevention, public endpoint privacy filtering (redaction of internal notes, author usernames, account IDs, and crew names), access control on file downloads (public allowed only if published & approved, staff/admin always allowed), and backup/restore coverage of attachment archives.
- `tests/test_pilot_readiness.py` (25 tests): Stage 7 field-pilot readiness — access control, unauthenticated 401 rejections on work-order creation/updates and priority recomputations, role authorization, session management (`login`, `logout`, `me`), salted password hashing and verification, secret protection (no credential leakage in `/api/config` or `/api/health`), session cookie security attributes (`HttpOnly`, `SameSite=Lax`), database health diagnostics, photo archive backup and restore, primary `gisdb` overwrite safeguards, and ZipSlip path traversal prevention.
- `tests/test_work_orders.py` (27 tests): Stage 6B work-order tracking tests — explicit creation, duplicate prevention and partial duplicates, 1-to-1 relationship enforcement, state machine lifecycle transitions (`Open` <-> `Assigned` <-> `In Progress` -> `Completed` -> `Open`; `Deferred`), invalid transition rejection, automatic `completed_at` population/clearing, status audit history logging, queue filtering by status and priority tier, REST API endpoints (`POST`, `GET`, `PATCH`), and Stage 6A export integration.
- `tests/test_export.py` (48 tests): Stage 6A work-order export tests — selected ID exports, filter-based exports, empty results (404), CSV formula injection defence (`=`, `+`, `-`, `@`, `\t`), Excel UTF-8 BOM, GeoJSON FeatureCollection validation & RFC 7946 coordinate ordering (`[longitude, latitude]` without top-level `crs`), ReportLab PDF generation & magic byte headers, user-confirmed vs AI-predicted labelling, priority disclaimer presence, credential secrecy, and safe filename sanitisation.
- `tests/test_analytics.py` (13 tests): Query filter builder, date boundaries, summary counts, priority scoring rules, metric caps, DBSCAN minimum-report threshold, missing data handling, and `/api/config` security.
- `tests/test_detection.py` (12 tests): VOC XML parsing, bounding box validation, typo corrections, and `/api/detect` contracts.
- `tests/test_geotag.py` (17 tests): EXIF GPS extraction, DMS to decimal conversion, photo validation, and API contracts.
- `tests/test_db_persistence.py` (12 tests): PostgreSQL connection setup, parameterized queries, PostGIS coordinate ordering `(longitude, latitude)`, and transaction safety.
- `tests/test_location_service.py` (7 tests): Watermark text OCR regex parsing, corridor analysis, and live priority estimation.

---

## API Endpoints Reference

| Endpoint | Method | Auth Required | Description |
| :--- | :---: | :---: | :--- |
| `/api/public/progress` | `GET` | No | List published road-repair progress updates with optional search, status filtering, and pagination. Internal notes and staff names are strictly redacted. |
| `/api/work-orders/<id>/updates` | `GET` | **Staff / Admin** | Get full officer timeline of progress updates and draft milestones for a work order, including internal staff notes and attachments. |
| `/api/work-orders/<id>/updates` | `POST` | **Staff / Admin** | Post an officer progress update to a work order with progress percentage, status transition, milestone summary, and optional JPEG/PNG/PDF attachments. |
| `/api/work-orders/updates/<id>/publish` | `POST` | **Staff / Admin** | Explicitly publish a progress update so it appears on the public main-page feed. |
| `/api/work-orders/updates/<id>/unpublish` | `POST` | **Staff / Admin** | Unpublish an update, returning it to private staff-only visibility. |
| `/api/work-orders/attachments/<id>` | `GET` | Conditional | Download attachment. Public visitors can access only if parent update is published and attachment is approved; staff/admin can access any. |
| `/api/auth/login` | `POST` | No | Authenticate municipal staff with username and password; sets secure session cookie. |
| `/api/auth/logout` | `POST` | No | Clear current user session. |
| `/api/auth/me` | `GET` | No | Return current session profile (`authenticated: bool`, `user: {...}`) without secrets. |
| `/api/work-orders` | `POST` | **Staff / Admin** | Explicitly create maintenance work order(s) from selected report IDs (`report_ids: [1, 2]`). Enforces 1-to-1 mapping; reports duplicates cleanly without re-creating. |
| `/api/work-orders` | `GET` | No | List maintenance work-order queue with optional filters (`status`, `priority_tier`, `q`) and KPI summary statistics. |
| `/api/work-orders/<id>` | `GET` | No | Get detailed work-order data including linked damage report, confirmed severity, priority score, and full chronological status audit timeline. |
| `/api/work-orders/<id>` | `PATCH` | **Staff / Admin** | Update work order assignments, target dates, supervisor notes, or transition lifecycle status with strict state-machine validation. Records operator in audit history. |
| `/api/analytics/priority/compute` | `POST` | **Staff / Admin** | Recompute transparent maintenance priority scores for all reports based on current GIS layers. |
| `/api/export/<fmt>` | `GET` | No | Export maintenance work orders in `pdf`, `csv`, or `geojson` format. Supports `report_ids=1,2,3` or filters (`date_from`, `date_to`, `damage_type`, `severity`). |
| `/api/reports` | `GET` | No | List damage reports with optional query filters (`date_from`, `date_to`, `damage_type`, `severity`). Includes linked work order metadata if present. |
| `/api/reports` | `POST` | No | Submit a new road damage report (field citizens or inspection staff) and automatically calculate initial priority score. |
| `/api/analytics/summary` | `GET` | No | Return aggregated counts (total, severity, priority tier, damage type) for current filters. |
| `/api/analytics/hotspots` | `GET` | No | Compute PostGIS DBSCAN clusters in UTM 43N with minimum report threshold safeguard. |
| `/api/analytics/external-data-status` | `GET` | No | Check record counts for `road_segments`, `junctions`, and `traffic_counts`. |
| `/api/config` | `GET` | No | Safe client configuration (Google Maps API key status, default study area; no secrets). |
| `/api/detect` | `POST` | No | AI road-damage classification and bounding box inference from uploaded JPEG photograph. |
| `/api/geotag` | `POST` | No | Extract GPS coordinates from photograph EXIF metadata. |
| `/api/health` | `GET` | No | System, PostGIS connectivity, persistent photo storage, and pilot readiness health check. |

---

## Troubleshooting

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| `DATABASE_URL is not configured` | `.env` file missing or variable not set | Copy `.env.example` to `.env` and fill in `DATABASE_URL`. |
| `connection to server at "127.0.0.1", port 5432 failed` | PostgreSQL service not running | Start PostgreSQL 18 service in Windows Services (`services.msc`) or pgAdmin. |
| `database "gisdb" does not exist` | Database has not been created | Connect to PostgreSQL in pgAdmin and run `CREATE DATABASE gisdb;`. |
| `Density Threshold Not Reached` | Fewer than 3 reports in database | Add more incident reports on the map to reach the DBSCAN clustering threshold. |
| `Google Live Traffic Setup Required` | `GOOGLE_MAPS_API_KEY` not configured in `.env` | Set your restricted Google Maps JavaScript API key in `.env` and restart Flask. |
| `Missing inputs in priority explanation` | Road network or traffic layers not imported | Run `python import_gis_data.py --sample` to load arterial network and traffic counts for the study area. |

