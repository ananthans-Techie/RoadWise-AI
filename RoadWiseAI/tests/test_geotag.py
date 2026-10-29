import io
import os
import sys
import unittest
import importlib
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from PIL import Image, ExifTags

# Add project root and RoadWiseAI directory to sys.path so tests can be run from either folder
_current_dir = os.path.dirname(os.path.abspath(__file__))
_roadwise_dir = os.path.abspath(os.path.join(_current_dir, ".."))
_project_dir = os.path.abspath(os.path.join(_roadwise_dir, ".."))
for _p in (_project_dir, _roadwise_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    app_module = importlib.import_module("RoadWiseAI.app")
except ModuleNotFoundError:
    app_module = importlib.import_module("app")

app = app_module.app


class RoadWiseGeotagAndReportTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        original_upload_folder = self.app.config["UPLOAD_FOLDER"]
        self.temp_upload_folder = tempfile.TemporaryDirectory()
        self.app.config["UPLOAD_FOLDER"] = self.temp_upload_folder.name
        self.addCleanup(self.temp_upload_folder.cleanup)
        self.addCleanup(
            self.app.config.__setitem__, "UPLOAD_FOLDER", original_upload_folder
        )
        self.reports = []
        self.save_patch = patch.object(
            app_module, "save_report", side_effect=self.fake_save_report
        )
        self.list_patch = patch.object(
            app_module, "list_reports", side_effect=lambda: list(self.reports)
        )
        self.save_patch.start()
        self.list_patch.start()
        self.addCleanup(self.save_patch.stop)
        self.addCleanup(self.list_patch.stop)

    def fake_save_report(self, road_name, damage_type, severity, description, photo_url, lat, lng):
        latitude = round(lat, 6)
        longitude = round(lng, 6)
        report = {
            "id": len(self.reports) + 1,
            "road_name": road_name,
            "damage_type": damage_type,
            "severity": severity,
            "description": description,
            "latitude": latitude,
            "longitude": longitude,
            "photo_url": photo_url,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "geom_geojson": {
                "type": "Point",
                "coordinates": [longitude, latitude]
            },
            "geom_wkt": f"SRID=4326;POINT({longitude} {latitude})",
            "storage_mode": "postgresql_postgis"
        }
        self.reports.append(report)
        return report

    def create_test_image(self, format="JPEG", with_gps=True, lat=(8.5241, "N"), lon=(76.9366, "E"), corrupt_gps=False):
        img = Image.new("RGB", (100, 100), color=(73, 109, 137))
        buf = io.BytesIO()

        if with_gps:
            exif = img.getexif()
            gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
            
            if corrupt_gps:
                gps_ifd[1] = lat[1]
                gps_ifd[2] = (8.0, 31.0, 26.76)
            else:
                lat_deg = int(abs(lat[0]))
                lat_min = int((abs(lat[0]) - lat_deg) * 60)
                lat_sec = ((abs(lat[0]) - lat_deg) * 60 - lat_min) * 60

                lon_deg = int(abs(lon[0]))
                lon_min = int((abs(lon[0]) - lon_deg) * 60)
                lon_sec = ((abs(lon[0]) - lon_deg) * 60 - lon_min) * 60

                gps_ifd[1] = lat[1]
                gps_ifd[2] = (float(lat_deg), float(lat_min), round(lat_sec, 2))
                gps_ifd[3] = lon[1]
                gps_ifd[4] = (float(lon_deg), float(lon_min), round(lon_sec, 2))

            img.save(buf, format=format, exif=exif)
        else:
            img.save(buf, format=format)

        buf.seek(0)
        return buf

    # --- GEOTAG ENDPOINT TESTS ---
    def test_routes_registered(self):
        """Test that /api/geotag and /api/reports are registered."""
        rules = [rule.rule for rule in self.app.url_map.iter_rules()]
        self.assertIn("/api/geotag", rules)
        self.assertIn("/api/reports", rules)

    def test_health_check(self):
        """Test /api/health endpoint."""
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value={"total": 0})),
            MagicMock(fetchone=MagicMock(return_value={"version": "PostGIS test version"})),
        ]
        with patch.object(app_module, "get_database_connection", return_value=connection):
            res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("study_area"), "Thiruvananthapuram, Kerala")
        self.assertIn("storage", data)
        self.assertEqual(data["storage"]["mode"], "postgresql_postgis")
        self.assertTrue(data["storage"]["postgis_ready"])

    def test_missing_file_geotag(self):
        res = self.client.post("/api/geotag", data={})
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

    def test_empty_filename_geotag(self):
        data = {"photo": (io.BytesIO(b""), "")}
        res = self.client.post("/api/geotag", data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

    def test_non_jpeg_geotag(self):
        data = {"photo": (io.BytesIO(b"fake png data"), "photo.png")}
        res = self.client.post("/api/geotag", data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)
        self.assertIn("error", res.get_json())

    def test_jpeg_without_gps(self):
        img_buf = self.create_test_image(format="JPEG", with_gps=False)
        data = {"photo": (img_buf, "no_gps.jpg")}
        res = self.client.post("/api/geotag", data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 422)
        self.assertIn("No GPS metadata", res.get_json()["error"])

    def test_jpeg_with_valid_gps_north_east(self):
        img_buf = self.create_test_image(format="JPEG", with_gps=True, lat=(8.524139, "N"), lon=(76.936639, "E"))
        data = {"photo": (img_buf, "trivandrum.jpg")}
        res = self.client.post("/api/geotag", data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 200)
        json_data = res.get_json()
        self.assertAlmostEqual(json_data["latitude"], 8.524139, places=2)
        self.assertAlmostEqual(json_data["longitude"], 76.936639, places=2)

    # --- REPORT WORKFLOW ENDPOINT TESTS ---
    def test_create_report_success_without_photo(self):
        """Test submitting a report without a photo succeeds and records PostGIS geometry."""
        payload = {
            "road_name": "MG Road, Trivandrum",
            "damage_type": "Pothole",
            "severity": "Critical",
            "description": "Deep pothole near junction",
            "latitude": "8.524100",
            "longitude": "76.936600"
        }
        res = self.client.post("/api/reports", data=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        report = data["report"]
        self.assertEqual(report["road_name"], "MG Road, Trivandrum")
        self.assertEqual(report["damage_type"], "Pothole")
        self.assertEqual(report["severity"], "Critical")
        self.assertEqual(report["latitude"], 8.5241)
        self.assertEqual(report["longitude"], 76.9366)
        self.assertIsNone(report["photo_url"])
        self.assertIn("geom_geojson", report)
        self.assertEqual(report["geom_geojson"]["coordinates"], [76.9366, 8.5241])
        self.assertIn("SRID=4326;POINT", report["geom_wkt"])

    def test_create_report_success_with_photo(self):
        """Test submitting a report with a valid JPEG attaches and stores photo."""
        img_buf = self.create_test_image(format="JPEG", with_gps=True)
        payload = {
            "road_name": "Palayam Road",
            "damage_type": "Longitudinal crack",
            "severity": "Moderate",
            "description": "Visible cracking along lane marking",
            "latitude": "8.502000",
            "longitude": "76.953000",
            "photo": (img_buf, "crack.jpg")
        }
        res = self.client.post("/api/reports", data=payload, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        report = data["report"]
        self.assertIsNotNone(report["photo_url"])
        self.assertTrue(report["photo_url"].startswith("/static/uploads/report_"))

    def test_create_report_json_payload(self):
        """Test submitting report via application/json payload."""
        payload = {
            "road_name": "MG Road",
            "damage_type": "Transverse crack",
            "severity": "Moderate",
            "description": "Crack across lane",
            "latitude": 8.5241,
            "longitude": 76.9366
        }
        res = self.client.post("/api/reports", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["report"]["road_name"], "MG Road")

    def test_create_report_missing_road_name(self):
        payload = {
            "road_name": "",
            "damage_type": "Pothole",
            "severity": "Critical",
            "latitude": "8.524100",
            "longitude": "76.936600"
        }
        res = self.client.post("/api/reports", data=payload)
        self.assertEqual(res.status_code, 400)
        self.assertIn("Road name is required", res.get_json()["error"])

    def test_create_report_invalid_damage_type(self):
        payload = {
            "road_name": "Kowdiar Road",
            "damage_type": "Alien crater",
            "severity": "Critical",
            "latitude": "8.524100",
            "longitude": "76.936600"
        }
        res = self.client.post("/api/reports", data=payload)
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid damage type", res.get_json()["error"])

    def test_create_report_invalid_severity(self):
        payload = {
            "road_name": "Kowdiar Road",
            "damage_type": "Pothole",
            "severity": "Apocalyptic",
            "latitude": "8.524100",
            "longitude": "76.936600"
        }
        res = self.client.post("/api/reports", data=payload)
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid severity", res.get_json()["error"])

    def test_create_report_missing_coordinates(self):
        """Test submitting without coordinates returns 400 and does NOT fall back to map center."""
        payload = {
            "road_name": "Kowdiar Road",
            "damage_type": "Pothole",
            "severity": "Minor"
        }
        res = self.client.post("/api/reports", data=payload)
        self.assertEqual(res.status_code, 400)
        self.assertIn("coordinates are required", res.get_json()["error"])

    def test_create_report_invalid_coordinate_range(self):
        payload = {
            "road_name": "Kowdiar Road",
            "damage_type": "Pothole",
            "severity": "Minor",
            "latitude": "95.000",
            "longitude": "76.936600"
        }
        res = self.client.post("/api/reports", data=payload)
        self.assertEqual(res.status_code, 400)
        self.assertIn("outside valid geographical limits", res.get_json()["error"])

    def test_create_report_invalid_photo_type(self):
        payload = {
            "road_name": "Bypass Road",
            "damage_type": "Pothole",
            "severity": "Critical",
            "latitude": "8.524100",
            "longitude": "76.936600",
            "photo": (io.BytesIO(b"not an image"), "bad_file.txt")
        }
        res = self.client.post("/api/reports", data=payload, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Photograph must be a JPEG", res.get_json()["error"])

    def test_get_reports_list(self):
        """Test GET /api/reports retrieves saved report records."""
        # Create one report
        self.client.post("/api/reports", data={
            "road_name": "Test Road",
            "damage_type": "Pothole",
            "severity": "Critical",
            "latitude": "8.524100",
            "longitude": "76.936600"
        })
        res = self.client.get("/api/reports")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(len(data["reports"]), 1)
        self.assertEqual(data["reports"][0]["road_name"], "Test Road")
        self.assertIn("storage_notice", data)
        self.assertEqual(data["storage_backend"], "postgresql_postgis")


if __name__ == "__main__":
    unittest.main()
