# RoadWise AI: Road Damage GIS (Stage 8: Public Road-Repair Progress Updates)

RoadWise AI is a Web GIS platform designed for logging, mapping, and prioritising road damage and maintenance incidents across the study area of **Thiruvananthapuram, Kerala, India**.

Stage 8 introduces **Public Road-Repair Progress Updates**, bringing municipal repair transparency to citizens through an authenticated officer update workflow, an append-only repair milestone timeline with validated photo/PDF attachments, and an open, privacy-preserving public progress feed on the main dashboard alongside a verified repaired roads map layer.

---

## Quick Start Commands (PowerShell)

```powershell
# 1. Navigate to RoadWiseAI directory
cd RoadWiseAI

# 2. Copy environment template and configure local PostgreSQL credentials
copy .env.example .env
# Open .env and add your postgres password:
# DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@127.0.0.1:5432/gisdb

# 3. Install dependencies in virtual environment
..\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 4. Run the idempotent database migration (Stages 1–8)
..\.venv\Scripts\python.exe migrate.py

# 5. Run the full test suite (181 tests across 9 suites)
..\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 6. Start the server (Development or Waitress Pilot Runner)
..\.venv\Scripts\python.exe run_pilot.py --port 5000
```

Detailed documentation and guides:
- [RoadWiseAI/README.md](RoadWiseAI/README.md): Complete architecture, features, and API reference
- [RoadWiseAI/PILOT_GUIDE.md](RoadWiseAI/PILOT_GUIDE.md): Supervised field pilot protocol & operational workflows
- [RoadWiseAI/RUNBOOK_WINDOWS.md](RoadWiseAI/RUNBOOK_WINDOWS.md): Windows operations runbook, service verification, backups & disaster recovery
- [RoadWiseAI/DATABASE_SETUP.md](RoadWiseAI/DATABASE_SETUP.md): PostgreSQL 18 + PostGIS setup & migration guide


