"""
RoadWise AI — Stage 5 GIS Analytics & Prioritisation Isolated Tests
tests/test_analytics.py

Isolated unit tests for:
  - Query filter builders & parameterized SQL
  - Summary count aggregations
  - Priority scoring engine (weights, tiers, caps, explainability, missing data handling)
  - Hotspot minimum-report threshold behavior
  - External GIS data status detection
  - Public /api/config endpoint safety (no database credentials exposed)
  - Parameter validation on API endpoints

All tests use mocks or unit functions; no data is modified or deleted from
the development PostgreSQL database.
"""

import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

_current_dir = os.path.dirname(os.path.abspath(__file__))
_roadwise_dir = os.path.abspath(os.path.join(_current_dir, ".."))
_project_dir = os.path.abspath(os.path.join(_roadwise_dir, ".."))
for _p in (_project_dir, _roadwise_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import app as app_module
    import analytics as analytics_module
except ModuleNotFoundError:
    from RoadWiseAI import app as app_module
    from RoadWiseAI import analytics as analytics_module


class TestFilterClauses(unittest.TestCase):
    """Test SQL WHERE clause builder for reports filtering."""

    def test_empty_filters(self):
        where, params = analytics_module._build_filter_clause(None, None, None, None)
        self.assertEqual(where, "")
        self.assertEqual(params, [])

    def test_single_filters(self):
        # Date from
        where, params = analytics_module._build_filter_clause("2026-01-01", None, None, None)
        self.assertIn("created_at >= %s", where)
        self.assertEqual(params, ["2026-01-01"])

        # Date to: verifies full-day boundary INTERVAL '1 day'
        where, params = analytics_module._build_filter_clause(None, "2026-01-31", None, None)
        self.assertIn("created_at < (%s::date + INTERVAL '1 day')", where)
        self.assertEqual(params, ["2026-01-31"])

        # Damage type
        where, params = analytics_module._build_filter_clause(None, None, "Pothole", None)
        self.assertIn("damage_type = %s", where)
        self.assertEqual(params, ["Pothole"])

        # Severity
        where, params = analytics_module._build_filter_clause(None, None, None, "Critical")
        self.assertIn("severity = %s", where)
        self.assertEqual(params, ["Critical"])

    def test_combined_filters(self):
        where, params = analytics_module._build_filter_clause(
            "2026-01-01", "2026-01-31", "Longitudinal crack", "Moderate"
        )
        self.assertTrue(where.startswith("WHERE "))
        self.assertIn("created_at >= %s", where)
        self.assertIn("created_at < (%s::date + INTERVAL '1 day')", where)
        self.assertIn("damage_type = %s", where)
        self.assertIn("severity = %s", where)
        self.assertEqual(len(params), 4)
        self.assertEqual(params, ["2026-01-01", "2026-01-31", "Longitudinal crack", "Moderate"])


class TestPriorityScoringEngine(unittest.TestCase):
    """Test transparent priority scoring rules, weights, caps, and factor explanations."""

    def test_tier_thresholds(self):
        self.assertEqual(analytics_module._tier(60.0), "High")
        self.assertEqual(analytics_module._tier(85.5), "High")
        self.assertEqual(analytics_module._tier(30.0), "Medium")
        self.assertEqual(analytics_module._tier(59.9), "Medium")
        self.assertEqual(analytics_module._tier(29.9), "Low")
        self.assertEqual(analytics_module._tier(0.0), "Low")

    def test_scoring_with_all_inputs_available(self):
        """Simulate a report with matched road, high traffic, junctions, and repeats."""
        mock_conn = MagicMock()

        # Mock road segment return
        mock_seg = {"id": 1, "road_class": "National Highway", "dist_m": 15.2}
        # Mock traffic count return
        mock_tc = {"aadt": 55000}
        # Mock junction count
        mock_jct = {"n": 4}
        # Mock repeat count
        mock_rep = {"n": 3}

        def mock_execute(sql, params=None):
            m = MagicMock()
            sql_str = str(sql)
            if "FROM road_segments" in sql_str:
                m.fetchone.return_value = mock_seg
            elif "FROM traffic_counts" in sql_str:
                m.fetchone.return_value = mock_tc
            elif "FROM junctions" in sql_str:
                m.fetchone.return_value = mock_jct
            elif "FROM road_damage_reports" in sql_str:
                m.fetchone.return_value = mock_rep
            else:
                m.fetchone.return_value = None
            return m

        mock_conn.execute.side_effect = mock_execute

        report = {
            "id": 101,
            "latitude": 8.5241,
            "longitude": 76.9366,
            "severity": "Critical",
            "damage_type": "Pothole",
            "created_at": datetime.now(timezone.utc) - timedelta(days=95),
        }

        score = analytics_module.compute_priority_score(mock_conn, report)

        self.assertEqual(score["report_id"], 101)
        self.assertEqual(score["severity_pts"], 40.0)      # Critical = 40
        self.assertEqual(score["road_class_pts"], 30.0)    # National Highway = 30
        self.assertEqual(score["traffic_pts"], 20.0)       # AADT 55,000 >= 50,000 -> 20
        self.assertEqual(score["junction_pts"], 10.0)      # 4 * 2.5 = 10 (cap 15)
        self.assertEqual(score["repeat_pts"], 9.0)         # 3 * 3 = 9 (cap 10)
        self.assertEqual(score["age_pts"], 3.0)            # 95 // 30 = 3 * 1.0 = 3 (cap 10)

        # Raw: 40 + 30 + 20 + 10 + 9 + 3 = 112 -> capped to 100
        self.assertEqual(score["priority_score"], 100.0)
        self.assertEqual(score["priority_tier"], "High")
        self.assertIn("Factor breakdown", score["explanation"])
        self.assertIn("National Highway", score["explanation"])
        self.assertIn("not an official engineering assessment", score["explanation"])

    def test_scoring_with_missing_external_data(self):
        """When external GIS tables are empty, scoring falls back gracefully without errors."""
        mock_conn = MagicMock()

        def mock_execute(sql, params=None):
            m = MagicMock()
            sql_str = str(sql)
            if "FROM road_segments" in sql_str:
                m.fetchone.return_value = None  # No matched road
            elif "FROM traffic_counts" in sql_str:
                m.fetchone.return_value = None  # No traffic station
            elif "FROM junctions" in sql_str:
                m.fetchone.return_value = {"n": 0}
            elif "FROM road_damage_reports" in sql_str:
                m.fetchone.return_value = {"n": 0}
            return m

        mock_conn.execute.side_effect = mock_execute

        report = {
            "id": 202,
            "latitude": 8.5241,
            "longitude": 76.9366,
            "severity": "Minor",
            "damage_type": "Surface deterioration",
            "created_at": datetime.now(timezone.utc),
        }

        score = analytics_module.compute_priority_score(mock_conn, report)

        self.assertEqual(score["severity_pts"], 10.0)  # Minor = 10
        self.assertEqual(score["road_class_pts"], 0.0)
        self.assertEqual(score["traffic_pts"], 0.0)
        self.assertEqual(score["junction_pts"], 0.0)
        self.assertEqual(score["repeat_pts"], 0.0)
        self.assertEqual(score["age_pts"], 0.0)
        self.assertEqual(score["priority_score"], 10.0)
        self.assertEqual(score["priority_tier"], "Low")

        # Must explicitly mention missing inputs in explanation
        self.assertIn("Missing inputs", score["explanation"])
        self.assertIn("road classification", score["explanation"])
        self.assertIn("traffic counts", score["explanation"])


class TestHotspotThresholds(unittest.TestCase):
    """Test PostGIS DBSCAN hotspot clustering threshold behavior."""

    def test_below_minimum_report_threshold(self):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"n": 2}

        # min_reports = 3, but total is 2
        res = analytics_module.hotspot_clusters(mock_conn, min_reports=3, cluster_eps_m=300.0)

        self.assertFalse(res["threshold_met"])
        self.assertEqual(res["total_reports"], 2)
        self.assertEqual(res["included_reports"], 0)
        self.assertEqual(res["clusters"], [])
        self.assertIn("Hotspot view requires at least 3 reports", res["message"])

    def test_above_minimum_report_threshold(self):
        mock_conn = MagicMock()
        # Count check
        mock_conn.execute.return_value.fetchone.return_value = {"n": 5}
        # DBSCAN query result
        mock_rows = [
            {
                "cluster_id": 0,
                "cnt": 3,
                "centroid_lat": 8.524800,
                "centroid_lng": 76.936000,
                "dominant_severity": "Critical",
                "dominant_damage_type": "Pothole",
                "report_ids": [1, 2, 3],
            }
        ]
        mock_conn.execute.return_value.fetchall.return_value = mock_rows

        res = analytics_module.hotspot_clusters(mock_conn, min_reports=3, cluster_eps_m=300.0)

        self.assertTrue(res["threshold_met"])
        self.assertEqual(res["total_reports"], 5)
        self.assertEqual(res["included_reports"], 3)
        self.assertEqual(len(res["clusters"]), 1)
        self.assertEqual(res["clusters"][0]["dominant_severity"], "Critical")
        self.assertEqual(res["clusters"][0]["count"], 3)


class TestExternalDataStatus(unittest.TestCase):
    """Test external GIS dataset status reporting."""

    def test_status_all_present(self):
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=lambda: {"n": 7}),
            MagicMock(fetchone=lambda: {"n": 12}),
            MagicMock(fetchone=lambda: {"n": 4}),
        ]
        status = analytics_module.external_data_status(mock_conn)
        self.assertTrue(status["road_segments"]["available"])
        self.assertEqual(status["road_segments"]["count"], 7)
        self.assertTrue(status["junctions"]["available"])
        self.assertEqual(status["junctions"]["count"], 12)
        self.assertTrue(status["traffic_counts"]["available"])
        self.assertEqual(status["traffic_counts"]["count"], 4)


class TestFlaskAnalyticsRoutesIsolated(unittest.TestCase):
    """Test Flask API endpoints with mocked database connections."""

    def setUp(self):
        self.app = app_module.app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_client_config_endpoint_safe(self):
        """Verify /api/config returns browser settings and never leaks DATABASE_URL."""
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://postgres:secret123@127.0.0.1:5432/gisdb",
            "GOOGLE_MAPS_API_KEY": "AIzaSyFakeKey123",
        }):
            res = self.client.get("/api/config")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["status"], "ok")
            self.assertTrue(data["google_maps_configured"])
            self.assertEqual(data["google_maps_api_key"], "AIzaSyFakeKey123")
            # Ensure database password/URL is never returned
            self.assertNotIn("secret123", str(data))
            self.assertNotIn("DATABASE_URL", data)

    def test_client_config_placeholder_ignored(self):
        """Verify placeholder keys are treated as unconfigured."""
        with patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": "YOUR_GOOGLE_MAPS_API_KEY_HERE"}):
            res = self.client.get("/api/config")
            data = res.get_json()
            self.assertFalse(data["google_maps_configured"])
            self.assertEqual(data["google_maps_api_key"], "")

    def test_reports_filter_validation(self):
        """Verify date and damage type parameter validations."""
        # Invalid date_from
        res = self.client.get("/api/reports?date_from=not-a-date")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid date_from format", res.get_json()["error"])

        # Invalid damage_type
        res = self.client.get("/api/reports?damage_type=AlienCrater")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid damage_type", res.get_json()["error"])

        # Invalid severity
        res = self.client.get("/api/reports?severity=Catastrophic")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid severity", res.get_json()["error"])

    def test_summary_filter_validation(self):
        """Verify /api/analytics/summary rejects malformed filter params."""
        res = self.client.get("/api/analytics/summary?date_to=bad-date")
        self.assertEqual(res.status_code, 400)

        res = self.client.get("/api/analytics/summary?severity=Extreme")
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
