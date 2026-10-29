# RoadWise AI: Road Damage GIS (Stage 4: AI-Assisted Road-Damage Detection)

RoadWise AI is a Web GIS platform designed for logging, mapping, and prioritising road damage and maintenance incidents across the study area of **Thiruvananthapuram, Kerala, India**.

Stage 4 integrates local deep learning object detection (Ultralytics YOLOv8n trained on RDD2022 India data) to automatically detect road damage zones in uploaded photos and suggest damage categories for user review, backed by PostgreSQL 18 + PostGIS.

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

# 4. Run the idempotent database migration
..\.venv\Scripts\python.exe migrate.py

# 5. Prepare RDD2022 pilot dataset (200 images)
..\.venv\Scripts\python.exe prepare_rdd_dataset.py --pilot-size 200

# 6. Check training estimates (dry run)
..\.venv\Scripts\python.exe train_detector.py --dry-run

# 7. Run the test suite (37 tests)
..\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 8. Start the Flask server
..\.venv\Scripts\python.exe app.py
```

Detailed documentation and troubleshooting steps are available in [RoadWiseAI/README.md](file:///c:/Users/sanan/OneDrive/Documents/Web%20And%20Mobile%20GIS/PROJECT/RoadWiseAI/README.md) and [RoadWiseAI/DATABASE_SETUP.md](file:///c:/Users/sanan/OneDrive/Documents/Web%20And%20Mobile%20GIS/PROJECT/RoadWiseAI/DATABASE_SETUP.md).

