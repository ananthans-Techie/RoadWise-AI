import io
import os
import sys
import unittest
import importlib
from unittest.mock import MagicMock, patch
from PIL import Image, ExifTags

# Add project root and RoadWiseAI directory to sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_roadwise_dir = os.path.abspath(os.path.join(_current_dir, ".."))
_project_dir = os.path.abspath(os.path.join(_roadwise_dir, ".."))
for _p in (_project_dir, _roadwise_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from RoadWiseAI import location_service
    import RoadWiseAI.app as app_module
except ModuleNotFoundError:
    import location_service
    import app as app_module

app = app_module.app


class LocationServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_parse_watermark_text(self):
        lines = [
            "Thiruvananthapuram, Kerala, India",
            "Panvel Kochi Kanyakumari Highway, Kazhakuttom",
            "Lat 8.576258, Long 76.867647",
            "20/11/2025 08:32 AM GMT+05:30"
        ]
        road_name, lat, lng = location_service.parse_watermark_text(lines)
        self.assertIsNotNone(lat)
        self.assertAlmostEqual(lat, 8.576258, places=5)
        self.assertAlmostEqual(lng, 76.867647, places=5)
        self.assertIn("Panvel Kochi Kanyakumari Highway", road_name)

    def test_parse_watermark_without_coords(self):
        lines = [
            "Mahatma Gandhi Road, Palayam",
            "Thiruvananthapuram",
        ]
        road_name, lat, lng = location_service.parse_watermark_text(lines)
        self.assertIsNone(lat)
        self.assertIn("Mahatma Gandhi Road", road_name)

    def test_analyze_map_location_scoring_and_recommendation(self):
        mock_conn = MagicMock()

        # Mock segment query: returns a National Highway
        mock_seg = MagicMock()
        mock_seg.fetchone.return_value = {
            "road_name": "NH 66 Bypass",
            "road_class": "National Highway",
            "dist": 12.0
        }

        # Mock incidents query: returns 1 report
        mock_incidents = MagicMock()
        mock_incidents.fetchall.return_value = [{"id": 1, "road_name": "NH 66", "damage_type": "Pothole", "severity": "Moderate", "dist": 80.0}]

        # Mock traffic query: returns aadt 45000
        mock_traffic = MagicMock()
        mock_traffic.fetchone.return_value = {"aadt": 45000}

        # Mock junctions query: returns 2
        mock_jct = MagicMock()
        mock_jct.fetchone.return_value = {"n": 2}

        # Return mock cursor sequentially on conn.execute
        mock_conn.execute.side_effect = [mock_seg, mock_incidents, mock_traffic, mock_jct]

        analysis = location_service.analyze_map_location(
            mock_conn,
            lat=8.576258,
            lng=76.867647,
            damage_type="Surface deterioration",
            severity="Moderate"
        )

        self.assertEqual(analysis["road_classification"], "National Highway")
        self.assertEqual(analysis["traffic_aadt"], 45000)
        self.assertEqual(analysis["estimated_priority"]["tier"], "High")
        self.assertGreaterEqual(analysis["estimated_priority"]["score"], 60.0)
        self.assertIn("engineering_recommendation", analysis)
        self.assertIn("milling", analysis["engineering_recommendation"].lower())

    def test_api_analyze_map_location_endpoint(self):
        mock_conn = MagicMock()
        mock_seg = MagicMock()
        mock_seg.fetchone.return_value = {
            "road_name": "MG Road",
            "road_class": "State Highway",
            "dist": 5.0
        }
        mock_inc = MagicMock()
        mock_inc.fetchall.return_value = []
        mock_tc = MagicMock()
        mock_tc.fetchone.return_value = {"aadt": 25000}
        mock_jct = MagicMock()
        mock_jct.fetchone.return_value = {"n": 1}
        mock_conn.execute.side_effect = [mock_seg, mock_inc, mock_tc, mock_jct]

        # Context manager for get_database_connection
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_conn

        with patch.object(app_module, "get_database_connection", return_value=mock_ctx):
            response = self.client.get("/api/map/analyze-location?lat=8.5241&lng=76.9366&damage_type=Pothole&severity=Critical")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["status"], "ok")
            self.assertIn("analysis", data)
            self.assertEqual(data["analysis"]["road_name"], "MG Road")
            self.assertIn("estimated_priority", data["analysis"])

    def test_api_analyze_map_location_invalid_coords(self):
        response = self.client.get("/api/map/analyze-location?lat=invalid&lng=76.9366")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
