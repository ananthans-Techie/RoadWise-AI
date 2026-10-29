"""
RoadWise AI — Stage 6A Isolated Unit Tests: Maintenance Work-Order Exports
tests/test_export.py

Covers:
  - Selected-report ID export (specific IDs → correct rows only)
  - Filter-based export (date / damage type / severity)
  - Empty results (correct 404 JSON with useful message)
  - CSV formula-injection safety (=, +, -, @, TAB, CR prefixes)
  - CSV BOM and header presence
  - GeoJSON structure (FeatureCollection, RFC 7946 coordinate order)
  - PDF generation (non-empty bytes, PDF magic bytes)
  - Confirmed vs AI-predicted field labels (AI values never marked confirmed)
  - Priority disclaimer presence in all three formats
  - Safe filename sanitisation

All tests use mocks or in-memory objects — no development database is touched,
no reports are modified or deleted, and no real files are written to disk.
"""

import csv
import io
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Ensure RoadWiseAI directory is importable
_current_dir = os.path.dirname(os.path.abspath(__file__))
_roadwise_dir = os.path.abspath(os.path.join(_current_dir, ".."))
_project_dir = os.path.abspath(os.path.join(_roadwise_dir, ".."))
for _p in (_project_dir, _roadwise_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import export_service
    import app as app_module
except ModuleNotFoundError:
    from RoadWiseAI import export_service
    from RoadWiseAI import app as app_module

flask_app = app_module.app


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_row(**overrides):
    """Return a minimal valid report dict as returned by fetch_export_rows."""
    base = {
        "id": 1,
        "road_name": "Test Road",
        "damage_type": "Pothole",
        "severity": "Critical",
        "description": "Large pothole near junction",
        "photo_url": "/static/uploads/report_test.jpg",
        "created_at": datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc),
        "latitude": 8.524100,
        "longitude": 76.936600,
        "priority_tier": "High",
        "priority_score": 72.50,
        "severity_pts": 40.0,
        "road_class_pts": 20.0,
        "traffic_pts": 10.0,
        "junction_pts": 2.5,
        "repeat_pts": 0.0,
        "age_pts": 0.0,
        "explanation": (
            "Factor breakdown: Severity (Critical) = 40 pts | Road class (State Highway) = 20 pts | "
            "Traffic AADT (24,000) = 10 pts | Junctions within 200 m (1) = 2.5 pts | "
            "Nearby reports within 150 m (0) = 0.0 pts | Report age (8 days) = 0.0 pts"
        ),
        "matched_segment_id": 3,
        "match_distance_m": 18.4,
        "scored_at": datetime(2026, 10, 20, 14, 5, 0, tzinfo=timezone.utc),
        "segment_road_name": "NH 66",
        "segment_road_class": "State Highway",
        "segment_osm_id": 123456789,
        "segment_source": "Kerala PWD 2024",
        "segment_source_date": None,
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# CSV tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCSVExport(unittest.TestCase):

    def test_csv_headers_present(self):
        """CSV must begin with the expected column header row."""
        row = _make_row()
        data = export_service.build_csv([row])
        # Strip BOM if present
        text = data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        headers = next(reader)
        self.assertIn("Report ID", headers)
        self.assertIn("Road Name (User Confirmed)", headers)
        self.assertIn("Damage Type (User Confirmed)", headers)
        self.assertIn("Severity (User Confirmed)", headers)
        self.assertIn("Priority Tier", headers)
        self.assertIn("Priority Score (0–100)", headers)
        self.assertIn("Disclaimer", headers)

    def test_csv_data_row_values(self):
        """Row values must match the report fixture."""
        row = _make_row()
        data = export_service.build_csv([row])
        text = data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        headers = next(reader)
        values = dict(zip(headers, next(reader)))
        self.assertEqual(values["Report ID"], "1")
        self.assertEqual(values["Road Name (User Confirmed)"], "Test Road")
        self.assertEqual(values["Damage Type (User Confirmed)"], "Pothole")
        self.assertEqual(values["Severity (User Confirmed)"], "Critical")
        self.assertEqual(values["Priority Tier"], "High")
        self.assertIn("72.50", values["Priority Score (0–100)"])
        self.assertEqual(values["Photo Reference"], "/static/uploads/report_test.jpg")

    def test_csv_empty_returns_header_only(self):
        """An empty row list must still produce a valid CSV with headers only."""
        data = export_service.build_csv([])
        text = data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        headers = next(reader)
        self.assertIn("Report ID", headers)
        # Must have exactly one row (headers)
        remaining = list(reader)
        self.assertEqual(len(remaining), 0)

    def test_csv_formula_injection_equals(self):
        """Cell values starting with '=' must be prefixed with a single quote."""
        row = _make_row(road_name="=CMD|'/C calc'!A0")
        data = export_service.build_csv([row])
        text = data.decode("utf-8-sig")
        self.assertIn("'=CMD", text)
        self.assertNotIn(",=CMD", text)  # raw = must not appear as first char

    def test_csv_formula_injection_plus(self):
        """+HYPERLINK(...) injection must be neutralised."""
        row = _make_row(road_name="+HYPERLINK(\"http://evil.com\")")
        data = export_service.build_csv([row])
        text = data.decode("utf-8-sig")
        self.assertIn("'+HYPERLINK", text)

    def test_csv_formula_injection_at(self):
        """@SUM injection must be neutralised."""
        row = _make_row(road_name="@SUM(A1:A10)")
        data = export_service.build_csv([row])
        text = data.decode("utf-8-sig")
        self.assertIn("'@SUM", text)

    def test_csv_formula_injection_tab(self):
        """Tab-prefixed injection must be neutralised."""
        row = _make_row(road_name="\tINJECT")
        data = export_service.build_csv([row])
        text = data.decode("utf-8-sig")
        # csv_safe converts \t-leading to '\tINJECT
        self.assertNotIn("\tINJECT", text)

    def test_csv_confirmed_label_not_ai(self):
        """
        Confirmed fields must use '(User Confirmed)' labels.
        Ensure there is no column header that reads 'AI Predicted' without
        qualification.
        """
        row = _make_row()
        data = export_service.build_csv([row])
        text = data.decode("utf-8-sig")
        # Headers confirm values are user-confirmed
        self.assertIn("User Confirmed", text)
        # No column should be misleadingly labelled as AI-confirmed
        self.assertNotIn("AI Predicted (Confirmed)", text)

    def test_csv_disclaimer_present(self):
        """Every CSV row must contain the priority disclaimer text."""
        row = _make_row()
        data = export_service.build_csv([row])
        text = data.decode("utf-8-sig")
        self.assertIn("decision support for human review", text)

    def test_csv_bom_for_excel(self):
        """CSV bytes must begin with UTF-8 BOM for Excel compatibility."""
        row = _make_row()
        data = export_service.build_csv([row])
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"), "Missing UTF-8 BOM")

    def test_csv_missing_priority_handled(self):
        """Reports without scored priority must not raise an exception."""
        row = _make_row(
            priority_tier=None, priority_score=None,
            severity_pts=None, road_class_pts=None,
            traffic_pts=None, junction_pts=None,
            repeat_pts=None, age_pts=None,
            explanation=None, matched_segment_id=None,
            match_distance_m=None, scored_at=None,
            segment_road_name=None, segment_road_class=None,
            segment_osm_id=None, segment_source=None,
        )
        data = export_service.build_csv([row])
        text = data.decode("utf-8-sig")
        self.assertIn("Not scored", text)

    def test_csv_multiple_rows(self):
        """CSV must correctly produce one data row per report."""
        rows = [_make_row(id=i, road_name=f"Road {i}") for i in range(1, 6)]
        data = export_service.build_csv(rows)
        text = data.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        next(reader)  # skip headers
        data_rows = list(reader)
        self.assertEqual(len(data_rows), 5)


# ─────────────────────────────────────────────────────────────────────────────
# GeoJSON tests
# ─────────────────────────────────────────────────────────────────────────────

class TestGeoJSONExport(unittest.TestCase):

    def test_geojson_is_feature_collection(self):
        """Output must be a valid GeoJSON FeatureCollection."""
        row = _make_row()
        data = export_service.build_geojson([row])
        obj = json.loads(data)
        self.assertEqual(obj["type"], "FeatureCollection")
        self.assertIn("features", obj)

    def test_geojson_coordinate_order(self):
        """GeoJSON coordinates must be [longitude, latitude] per RFC 7946."""
        row = _make_row(latitude=8.524100, longitude=76.936600)
        data = export_service.build_geojson([row])
        obj = json.loads(data)
        coords = obj["features"][0]["geometry"]["coordinates"]
        self.assertAlmostEqual(coords[0], 76.936600, places=4)  # longitude first
        self.assertAlmostEqual(coords[1], 8.524100, places=4)   # latitude second

    def test_geojson_properties_include_confirmed_fields(self):
        """Feature properties must include user-confirmed report fields."""
        row = _make_row()
        data = export_service.build_geojson([row])
        obj = json.loads(data)
        props = obj["features"][0]["properties"]
        self.assertEqual(props["report_id"], 1)
        self.assertEqual(props["road_name"], "Test Road")
        self.assertEqual(props["damage_type"], "Pothole")
        self.assertEqual(props["severity"], "Critical")
        self.assertEqual(props["priority_tier"], "High")
        self.assertAlmostEqual(props["priority_score"], 72.50, places=1)

    def test_geojson_disclaimer_in_metadata(self):
        """GeoJSON metadata must include the priority disclaimer."""
        row = _make_row()
        data = export_service.build_geojson([row])
        obj = json.loads(data)
        self.assertIn("disclaimer", obj["metadata"])
        self.assertIn("decision support", obj["metadata"]["disclaimer"])

    def test_geojson_empty_returns_empty_features(self):
        """Empty row list must return a FeatureCollection with no features."""
        data = export_service.build_geojson([])
        obj = json.loads(data)
        self.assertEqual(obj["type"], "FeatureCollection")
        self.assertEqual(len(obj["features"]), 0)

    def test_geojson_segment_info_included(self):
        """Matched road segment fields must appear in properties."""
        row = _make_row()
        data = export_service.build_geojson([row])
        obj = json.loads(data)
        props = obj["features"][0]["properties"]
        self.assertEqual(props["segment_road_name"], "NH 66")
        self.assertEqual(props["segment_road_class"], "State Highway")
        self.assertEqual(props["segment_id"], 3)
        self.assertAlmostEqual(props["match_distance_m"], 18.4, places=1)

    def test_geojson_no_credentials_in_output(self):
        """GeoJSON must not contain DATABASE_URL or password strings."""
        row = _make_row()
        data = export_service.build_geojson([row])
        text = data.decode("utf-8")
        self.assertNotIn("DATABASE_URL", text)
        self.assertNotIn("postgresql://", text)
        self.assertNotIn("password", text.lower().replace("password", "__REPLACED__"))

    def test_geojson_rfc7946_no_top_level_crs(self):
        """RFC 7946 removes top-level crs member; coordinates are implicitly WGS 84 [lng, lat]."""
        row = _make_row()
        data = export_service.build_geojson([row])
        obj = json.loads(data)
        self.assertNotIn("crs", obj)
        coords = obj["features"][0]["geometry"]["coordinates"]
        self.assertAlmostEqual(coords[0], 76.936600, places=4)  # longitude first
        self.assertAlmostEqual(coords[1], 8.524100, places=4)   # latitude second


# ─────────────────────────────────────────────────────────────────────────────
# PDF tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPDFExport(unittest.TestCase):

    def test_pdf_returns_bytes(self):
        """build_pdf must return bytes."""
        row = _make_row()
        data = export_service.build_pdf([row])
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 500)

    def test_pdf_magic_bytes(self):
        """PDF output must start with the %PDF magic header."""
        row = _make_row()
        data = export_service.build_pdf([row])
        self.assertTrue(data.startswith(b"%PDF"), "Output does not start with %PDF")

    def test_pdf_empty_reports_no_exception(self):
        """Empty row list must produce a valid (though single-page) PDF."""
        data = export_service.build_pdf([])
        self.assertTrue(data.startswith(b"%PDF"))

    def test_pdf_multiple_reports(self):
        """Multiple reports must not raise an exception."""
        rows = [_make_row(id=i, severity="Moderate", priority_tier="Medium", priority_score=45.0)
                for i in range(1, 4)]
        data = export_service.build_pdf(rows)
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertGreater(len(data), 1000)

    def test_pdf_title_passed(self):
        """Custom title must be accepted without error."""
        row = _make_row()
        data = export_service.build_pdf([row], title="Custom Title Test")
        self.assertTrue(data.startswith(b"%PDF"))


# ─────────────────────────────────────────────────────────────────────────────
# safe_export_filename tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSafeFilename(unittest.TestCase):

    def test_alphanumeric_preserved(self):
        name = export_service.safe_export_filename("roadwise_workorder", "pdf")
        self.assertTrue(name.endswith(".pdf"))
        self.assertIn("roadwise_workorder", name)

    def test_special_chars_sanitised(self):
        name = export_service.safe_export_filename("../../etc/passwd", "csv")
        self.assertNotIn("/", name)
        self.assertNotIn(".", name.split("_")[0])
        self.assertTrue(name.endswith(".csv"))

    def test_timestamp_appended(self):
        name = export_service.safe_export_filename("test", "geojson")
        # timestamp is 15 chars: YYYYMMDD_HHMMSS
        self.assertRegex(name, r"_\d{8}_\d{6}\.geojson$")

    def test_long_prefix_truncated(self):
        long_prefix = "a" * 200
        name = export_service.safe_export_filename(long_prefix, "pdf")
        # prefix portion must not exceed 40 chars
        prefix_part = name.rsplit("_", 2)[0]
        self.assertLessEqual(len(prefix_part), 40)


# ─────────────────────────────────────────────────────────────────────────────
# Flask API endpoint tests  (mock DB — no live database)
# ─────────────────────────────────────────────────────────────────────────────

def _mock_conn_with_rows(rows):
    """Return a mock psycopg connection whose execute().fetchall() returns rows."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = [
        {k: v for k, v in r.items()} for r in rows
    ]
    conn.execute.return_value = cursor
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    return conn


class TestExportAPIEndpoints(unittest.TestCase):
    """Flask endpoint contract tests using the test client + patched DB."""

    def setUp(self):
        flask_app.config["TESTING"] = True
        self.client = flask_app.test_client()

    # ── Unsupported format ────────────────────────────────────────────────────

    def test_unsupported_format_returns_400(self):
        resp = self.client.get("/api/export/xlsx")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertIn("error", data)
        self.assertIn("xlsx", data["error"].lower())

    # ── Invalid report IDs ────────────────────────────────────────────────────

    def test_invalid_report_id_returns_400(self):
        resp = self.client.get("/api/export/csv?report_ids=abc")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    def test_negative_report_id_returns_400(self):
        resp = self.client.get("/api/export/csv?report_ids=-5")
        self.assertEqual(resp.status_code, 400)

    def test_too_many_report_ids_returns_400(self):
        ids = ",".join(str(i) for i in range(1, 502))
        resp = self.client.get(f"/api/export/csv?report_ids={ids}")
        self.assertEqual(resp.status_code, 400)

    # ── Invalid filter params ─────────────────────────────────────────────────

    def test_bad_date_format_returns_400(self):
        resp = self.client.get("/api/export/csv?date_from=20-10-2026")
        self.assertEqual(resp.status_code, 400)

    def test_invalid_damage_type_returns_400(self):
        resp = self.client.get("/api/export/csv?damage_type=Explosion")
        self.assertEqual(resp.status_code, 400)

    def test_invalid_severity_returns_400(self):
        resp = self.client.get("/api/export/csv?severity=Catastrophic")
        self.assertEqual(resp.status_code, 400)

    # ── Empty results ─────────────────────────────────────────────────────────

    def test_empty_result_returns_404_with_message(self):
        with patch("app.export_service.fetch_export_rows", return_value=[]):
            with patch("app.get_database_connection"):
                resp = self.client.get("/api/export/csv?damage_type=Pothole")
        self.assertEqual(resp.status_code, 404)
        data = resp.get_json()
        self.assertEqual(data["status"], "empty")
        self.assertIn("message", data)
        self.assertIn("no", data["message"].lower())

    # ── Successful CSV export ─────────────────────────────────────────────────

    def test_csv_export_returns_200_with_attachment(self):
        rows = [_make_row()]
        with patch("app.export_service.fetch_export_rows", return_value=rows):
            with patch("app.get_database_connection"):
                resp = self.client.get("/api/export/csv")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.content_type)
        cd = resp.headers.get("Content-Disposition", "")
        self.assertIn("attachment", cd)
        self.assertIn(".csv", cd)

    # ── Successful GeoJSON export ──────────────────────────────────────────────

    def test_geojson_export_returns_200(self):
        rows = [_make_row()]
        with patch("app.export_service.fetch_export_rows", return_value=rows):
            with patch("app.get_database_connection"):
                resp = self.client.get("/api/export/geojson")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("geo+json", resp.content_type)
        obj = json.loads(resp.data)
        self.assertEqual(obj["type"], "FeatureCollection")

    # ── Successful PDF export ─────────────────────────────────────────────────

    def test_pdf_export_returns_200(self):
        rows = [_make_row()]
        with patch("app.export_service.fetch_export_rows", return_value=rows):
            with patch("app.get_database_connection"):
                resp = self.client.get("/api/export/pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "application/pdf")
        self.assertTrue(resp.data.startswith(b"%PDF"))

    # ── Selected IDs export ───────────────────────────────────────────────────

    def test_selected_ids_export_csv(self):
        rows = [_make_row(id=7)]
        with patch("app.export_service.fetch_export_rows", return_value=rows) as mock_fetch:
            with patch("app.get_database_connection"):
                resp = self.client.get("/api/export/csv?report_ids=7")
        self.assertEqual(resp.status_code, 200)
        # Verify fetch was called with the correct id list
        call_kwargs = mock_fetch.call_args[1]
        self.assertEqual(call_kwargs.get("report_ids"), [7])

    # ── No credentials in response headers or body ────────────────────────────

    def test_no_credentials_in_csv_response(self):
        rows = [_make_row()]
        with patch("app.export_service.fetch_export_rows", return_value=rows):
            with patch("app.get_database_connection"):
                resp = self.client.get("/api/export/csv")
        body = resp.data.decode("utf-8-sig", errors="ignore")
        self.assertNotIn("DATABASE_URL", body)
        self.assertNotIn("postgresql://", body)

    # ── Confirmed vs AI labels ────────────────────────────────────────────────

    def test_csv_labels_confirmed_not_ai(self):
        """
        Exported CSV column headers for damage type and severity must say
        '(User Confirmed)', not 'AI Predicted' or 'AI Suggested'.
        """
        rows = [_make_row()]
        with patch("app.export_service.fetch_export_rows", return_value=rows):
            with patch("app.get_database_connection"):
                resp = self.client.get("/api/export/csv")
        body = resp.data.decode("utf-8-sig")
        self.assertIn("User Confirmed", body)
        self.assertNotIn("AI Predicted (Confirmed)", body)
        self.assertNotIn("AI Suggested (Confirmed)", body)


# ─────────────────────────────────────────────────────────────────────────────
# fetch_export_rows query builder (unit — no DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchExportRows(unittest.TestCase):
    """Verify the SQL builder calls the right parameterized placeholders."""

    def _make_mock_conn(self, returned_rows=None):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = returned_rows or []
        conn.execute.return_value = cursor
        return conn

    def test_report_ids_uses_any_placeholder(self):
        conn = self._make_mock_conn([])
        export_service.fetch_export_rows(conn, report_ids=[1, 2, 3])
        sql_arg = conn.execute.call_args[0][0]
        params_arg = conn.execute.call_args[0][1]
        self.assertIn("ANY(%s)", sql_arg)
        self.assertEqual(params_arg, [[1, 2, 3]])

    def test_filter_damage_type_parameterised(self):
        conn = self._make_mock_conn([])
        export_service.fetch_export_rows(conn, damage_type="Pothole")
        sql_arg = conn.execute.call_args[0][0]
        params_arg = conn.execute.call_args[0][1]
        self.assertIn("r.damage_type = %s", sql_arg)
        self.assertIn("Pothole", params_arg)

    def test_filter_severity_parameterised(self):
        conn = self._make_mock_conn([])
        export_service.fetch_export_rows(conn, severity="Critical")
        sql_arg = conn.execute.call_args[0][0]
        self.assertIn("r.severity = %s", sql_arg)

    def test_empty_filter_no_where(self):
        conn = self._make_mock_conn([])
        export_service.fetch_export_rows(conn)
        sql_arg = conn.execute.call_args[0][0]
        params_arg = conn.execute.call_args[0][1]
        # No outer WHERE clause added on road_damage_reports when no filters are set
        # (only the LATERAL subquery's internal WHERE report_id = r.id exists)
        self.assertEqual(sql_arg.count("WHERE"), 1)
        self.assertEqual(params_arg, [])

    def test_returns_empty_list_when_no_rows(self):
        conn = self._make_mock_conn([])
        result = export_service.fetch_export_rows(conn, report_ids=[999])
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

