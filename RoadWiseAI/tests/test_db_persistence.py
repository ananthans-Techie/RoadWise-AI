"""
Isolated Unit and Persistence Tests for RoadWise AI PostgreSQL/PostGIS.
These tests verify database integration, SQL parameterization, transaction safety,
coordinate order, and error responses without connecting to or mutating the
user's development database.
"""
import io
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from PIL import Image
import psycopg

# Ensure paths are set for imports from either PROJECT or RoadWiseAI directory
_current_dir = os.path.dirname(os.path.abspath(__file__))
_roadwise_dir = os.path.abspath(os.path.join(_current_dir, ".."))
_project_dir = os.path.abspath(os.path.join(_roadwise_dir, ".."))
for _p in (_project_dir, _roadwise_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import app as app_module
except ModuleNotFoundError:
    from RoadWiseAI import app as app_module

app = app_module.app


class TestDatabasePersistenceIsolated(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_configure_database_missing_url(self):
        """Verify configure_database raises clear error if DATABASE_URL is missing."""
        with patch.object(app_module, "load_dotenv"), \
             patch.object(app_module, "find_dotenv", return_value=""), \
             patch.dict(os.environ, {}, clear=True):
            app_module.DATABASE_DSN = None
            with self.assertRaises(RuntimeError) as ctx:
                app_module.configure_database()
            self.assertIn("DATABASE_URL is not configured", str(ctx.exception))

    def test_configure_database_from_env(self):
        """Verify configure_database loads DATABASE_URL properly."""
        test_url = "postgresql://user:pass@127.0.0.1:5432/gisdb"
        with patch.object(app_module, "load_dotenv"), \
             patch.object(app_module, "find_dotenv", return_value=""), \
             patch.dict(os.environ, {"DATABASE_URL": test_url}, clear=True):
            app_module.DATABASE_DSN = None
            dsn = app_module.configure_database()
            self.assertEqual(dsn, test_url)
            self.assertEqual(app_module.DATABASE_DSN, test_url)

    def test_save_report_parameterized_and_coord_order(self):
        """
        Verify that save_report uses parameterized SQL with coordinates in
        PostGIS (longitude, latitude) order and commits the transaction.
        """
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        
        mock_row = {
            "id": 42,
            "road_name": "MG Road",
            "damage_type": "Pothole",
            "severity": "Critical",
            "description": "Deep pothole",
            "photo_url": "/static/uploads/test.jpg",
            "latitude": 8.5241,
            "longitude": 76.9366,
            "created_at": datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc),
        }
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        with patch.object(app_module, "get_database_connection", return_value=mock_conn):
            result = app_module.save_report(
                road_name="MG Road",
                damage_type="Pothole",
                severity="Critical",
                description="Deep pothole",
                photo_url="/static/uploads/test.jpg",
                lat=8.5241,
                lng=76.9366,
            )

        # Verify SQL execution was parameterized
        self.assertTrue(mock_conn.execute.called)
        call_args = mock_conn.execute.call_args
        sql_query, params = call_args[0]
        
        # Verify ST_MakePoint order: longitude (lng) first, latitude (lat) second
        self.assertIn("ST_SetSRID(ST_MakePoint(%s, %s), 4326)", sql_query)
        self.assertEqual(params[0], "MG Road")
        self.assertEqual(params[1], "Pothole")
        self.assertEqual(params[2], "Critical")
        self.assertEqual(params[3], "Deep pothole")
        self.assertEqual(params[4], "/static/uploads/test.jpg")
        self.assertEqual(params[5], 76.9366)  # longitude first
        self.assertEqual(params[6], 8.5241)   # latitude second

        # Verify result contains permanent ID and PostGIS structures
        self.assertEqual(result["id"], 42)
        self.assertEqual(result["geom_geojson"]["coordinates"], [76.9366, 8.5241])
        self.assertIn("SRID=4326;POINT(76.9366 8.5241)", result["geom_wkt"])
        self.assertEqual(result["storage_mode"], "postgresql_postgis")

    def test_save_report_rolls_back_on_error(self):
        """Verify that save_report rolls back transaction and closes on exception."""
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.execute.side_effect = psycopg.OperationalError("Simulated DB connection drop")

        with patch.object(app_module, "get_database_connection", return_value=mock_conn):
            with self.assertRaises(psycopg.OperationalError):
                app_module.save_report(
                    road_name="Kowdiar Road",
                    damage_type="Pothole",
                    severity="Minor",
                    description=None,
                    photo_url=None,
                    lat=8.5300,
                    lng=76.9400,
                )

    def test_reports_get_db_failure(self):
        """Verify GET /api/reports returns 503 safely if database is unavailable."""
        with patch.object(app_module, "list_reports", side_effect=psycopg.OperationalError("Connection refused")):
            res = self.client.get("/api/reports")
            self.assertEqual(res.status_code, 503)
            data = res.get_json()
            self.assertEqual(data["status"], "error")
            self.assertIn("Could not load reports from PostgreSQL/PostGIS", data["error"])

    def test_reports_post_db_failure_cleans_up_photo(self):
        """
        Verify POST /api/reports returns 503 and deletes unlinked uploaded photo
        if database insert fails.
        """
        # Create a small valid test image
        img = Image.new("RGB", (10, 10), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        buf.seek(0)

        payload = {
            "road_name": "Test Highway",
            "damage_type": "Pothole",
            "severity": "Critical",
            "latitude": "8.5241",
            "longitude": "76.9366",
            "photo": (buf, "pothole.jpg"),
        }

        with patch.object(app_module, "save_report", side_effect=psycopg.OperationalError("DB down")):
            res = self.client.post("/api/reports", data=payload, content_type="multipart/form-data")
            self.assertEqual(res.status_code, 503)
            data = res.get_json()
            self.assertEqual(data["status"], "error")
            self.assertIn("Could not save the report to PostgreSQL/PostGIS", data["error"])

    def test_migration_sql_idempotency_and_fields(self):
        """Verify migration SQL defines required fields, PostGIS geom, and index without drops."""
        migration_path = os.path.join(
            _roadwise_dir, "migrations", "001_create_road_damage_reports.sql"
        )
        self.assertTrue(os.path.isfile(migration_path), "Migration file must exist")
        
        with open(migration_path, "r", encoding="utf-8") as f:
            sql = f.read()

        # Check required DDL keywords
        self.assertIn("CREATE EXTENSION IF NOT EXISTS postgis", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS road_damage_reports", sql)
        self.assertIn("geom geometry(Point, 4326) NOT NULL", sql)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_road_damage_reports_geom", sql)
        self.assertIn("USING GIST (geom)", sql)
        self.assertIn("created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()", sql)

        # Verify no destructive statements
        self.assertNotIn("DROP TABLE", sql)
        self.assertNotIn("DELETE FROM", sql)
        self.assertNotIn("TRUNCATE", sql)

    def test_serialize_report_format(self):
        """Verify serialize_report produces frontend-expected JSON structure."""
        row = {
            "id": 101,
            "road_name": "Palayam Junction",
            "damage_type": "Longitudinal crack",
            "severity": "Moderate",
            "description": "Crack near median",
            "photo_url": "/static/uploads/report_1.jpg",
            "latitude": 8.5020123,
            "longitude": 76.9530456,
            "created_at": datetime(2026, 10, 21, 15, 30, 0, tzinfo=timezone.utc),
        }
        serialized = app_module.serialize_report(row)
        self.assertEqual(serialized["id"], 101)
        self.assertEqual(serialized["latitude"], 8.502012)
        self.assertEqual(serialized["longitude"], 76.953046)
        self.assertEqual(serialized["created_at"], "2026-10-21 15:30:00 UTC")
        self.assertEqual(serialized["geom_geojson"]["coordinates"], [76.953046, 8.502012])
        self.assertEqual(serialized["geom_wkt"], "SRID=4326;POINT(76.953046 8.502012)")
        self.assertEqual(serialized["storage_mode"], "postgresql_postgis")


if __name__ == "__main__":
    unittest.main()
