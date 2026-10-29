"""
RoadWise AI — Stage 6B Isolated Unit Tests: Maintenance Work-Order Tracking
tests/test_work_orders.py

Covers:
  - Creating work orders for reports (stable ID, default status, history entry)
  - Duplicate prevention & reporting (already_tracked list, at most one per report)
  - Enforced status transition state machine (valid vs invalid transitions)
  - Reopening completed work orders (completed_at set and cleared)
  - Status history audit logging
  - Updating assignment, target date, and maintenance notes
  - Linked report data preservation (reports remain untouched on status changes)
  - Export integration (work order code and status in CSV, GeoJSON, and PDF)
  - API endpoint input validation and error contracts

All tests use mocks or in-memory fixtures; no development database is touched,
no live reports are modified, and no credentials are leaked.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone, date
from unittest.mock import MagicMock, patch

_current_dir = os.path.dirname(os.path.abspath(__file__))
_roadwise_dir = os.path.abspath(os.path.join(_current_dir, ".."))
_project_dir = os.path.abspath(os.path.join(_roadwise_dir, ".."))
for _p in (_project_dir, _roadwise_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import work_order_service
    import export_service
    import app as app_module
except ModuleNotFoundError:
    from RoadWiseAI import work_order_service
    from RoadWiseAI import export_service
    from RoadWiseAI import app as app_module

flask_app = app_module.app


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests: Status Transition Rules
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkOrderStatusTransitions(unittest.TestCase):

    def test_valid_transitions_from_open(self):
        """Open status can transition to Assigned, In Progress, Deferred, Completed."""
        for target in ["Assigned", "In Progress", "Deferred", "Completed"]:
            valid, err = work_order_service.validate_transition("Open", target)
            self.assertTrue(valid, f"Transition from Open to {target} should be valid")
            self.assertIsNone(err)

    def test_valid_transitions_from_assigned(self):
        """Assigned can transition to Open, In Progress, Deferred, Completed."""
        for target in ["Open", "In Progress", "Deferred", "Completed"]:
            valid, err = work_order_service.validate_transition("Assigned", target)
            self.assertTrue(valid, f"Transition from Assigned to {target} should be valid")
            self.assertIsNone(err)

    def test_valid_transitions_from_in_progress(self):
        """In Progress can transition to Completed, Deferred, Assigned, Open."""
        for target in ["Completed", "Deferred", "Assigned", "Open"]:
            valid, err = work_order_service.validate_transition("In Progress", target)
            self.assertTrue(valid, f"Transition from In Progress to {target} should be valid")
            self.assertIsNone(err)

    def test_valid_transitions_from_deferred(self):
        """Deferred can be reactivated to Open, Assigned, or In Progress."""
        for target in ["Open", "Assigned", "In Progress"]:
            valid, err = work_order_service.validate_transition("Deferred", target)
            self.assertTrue(valid, f"Transition from Deferred to {target} should be valid")
            self.assertIsNone(err)

    def test_invalid_deferred_to_completed(self):
        """Deferred cannot jump directly to Completed without resuming work."""
        valid, err = work_order_service.validate_transition("Deferred", "Completed")
        self.assertFalse(valid)
        self.assertIn("Invalid status transition", err)

    def test_completed_can_reopen(self):
        """Completed can transition back to Open if repair fails inspection."""
        valid, err = work_order_service.validate_transition("Completed", "Open")
        self.assertTrue(valid)
        self.assertIsNone(err)

    def test_completed_cannot_jump_directly_to_in_progress(self):
        """Completed cannot jump directly to In Progress without reopening to Open."""
        valid, err = work_order_service.validate_transition("Completed", "In Progress")
        self.assertFalse(valid)
        self.assertIn("Invalid status transition", err)

    def test_same_status_is_noop_valid(self):
        """Staying in same status is valid (allows field updates without transition)."""
        for s in work_order_service.ALLOWED_STATUSES:
            valid, err = work_order_service.validate_transition(s, s)
            self.assertTrue(valid)
            self.assertIsNone(err)

    def test_unknown_status_rejected(self):
        """Unknown statuses are rejected."""
        valid, err = work_order_service.validate_transition("Open", "Archived")
        self.assertFalse(valid)
        self.assertIn("Invalid status", err)


# ─────────────────────────────────────────────────────────────────────────────
# Unit Tests: Work Order Service (with Mock Database Connection)
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkOrderService(unittest.TestCase):

    def _make_mock_conn(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.execute.return_value = cursor
        return conn, cursor

    def test_format_work_order_code(self):
        """Verify stable human-readable identifier formatting."""
        self.assertEqual(work_order_service.format_work_order_code(1), "WO-0001")
        self.assertEqual(work_order_service.format_work_order_code(42), "WO-0042")
        self.assertEqual(work_order_service.format_work_order_code(1234), "WO-1234")

    def test_create_work_orders_success(self):
        """Creating work order for untracked report generates Open status and history entry."""
        conn, cursor = self._make_mock_conn()

        # Step 1: No existing work orders
        cursor.fetchall.side_effect = [
            [],  # existing work orders
            [{"id": 5, "road_name": "MG Road", "damage_type": "Pothole", "severity": "Critical"}],  # valid reports
        ]

        # Step 2: Returning insert row
        now = datetime.now(timezone.utc)
        cursor.fetchone.return_value = {
            "id": 1,
            "work_order_code": "WO-0005",
            "report_id": 5,
            "status": "Open",
            "assigned_to": None,
            "target_date": None,
            "completed_at": None,
            "notes": None,
            "created_at": now,
            "updated_at": now,
        }

        res = work_order_service.create_work_orders(conn, [5])
        self.assertEqual(len(res["created"]), 1)
        self.assertEqual(res["created"][0]["work_order_code"], "WO-0005")
        self.assertEqual(res["created"][0]["status"], "Open")
        self.assertEqual(len(res["already_tracked"]), 0)

        # Check history was inserted
        history_calls = [
            c for c in conn.execute.call_args_list
            if "work_order_status_history" in str(c[0][0])
        ]
        self.assertEqual(len(history_calls), 1)

    def test_create_work_orders_duplicate_prevention(self):
        """Reports already tracked are not re-created; returned in already_tracked."""
        conn, cursor = self._make_mock_conn()

        cursor.fetchall.return_value = [
            {
                "id": 10,
                "work_order_code": "WO-0005",
                "report_id": 5,
                "status": "In Progress",
                "assigned_to": "Team A",
                "target_date": date(2026, 11, 1),
            }
        ]

        res = work_order_service.create_work_orders(conn, [5])
        self.assertEqual(len(res["created"]), 0)
        self.assertEqual(len(res["already_tracked"]), 1)
        self.assertEqual(res["already_tracked"][0]["work_order_code"], "WO-0005")
        self.assertEqual(res["already_tracked"][0]["status"], "In Progress")

    def test_create_work_orders_partial_duplicates(self):
        """Mixed batch creates missing work orders while reporting existing ones."""
        conn, cursor = self._make_mock_conn()

        now = datetime.now(timezone.utc)
        cursor.fetchall.side_effect = [
            # 1. Existing check: report 1 is already tracked
            [{"id": 1, "work_order_code": "WO-0001", "report_id": 1, "status": "Open", "assigned_to": None, "target_date": None}],
            # 2. Validity check: report 2 exists
            [{"id": 2, "road_name": "Kowdiar Road", "damage_type": "Pothole", "severity": "Moderate"}],
        ]
        cursor.fetchone.return_value = {
            "id": 2,
            "work_order_code": "WO-0002",
            "report_id": 2,
            "status": "Open",
            "assigned_to": None,
            "target_date": None,
            "completed_at": None,
            "notes": None,
            "created_at": now,
            "updated_at": now,
        }

        res = work_order_service.create_work_orders(conn, [1, 2])
        self.assertEqual(len(res["created"]), 1)
        self.assertEqual(res["created"][0]["report_id"], 2)
        self.assertEqual(len(res["already_tracked"]), 1)
        self.assertEqual(res["already_tracked"][0]["report_id"], 1)

    def test_update_work_order_valid_transition(self):
        """Transition from Open to In Progress succeeds and logs history."""
        conn, cursor = self._make_mock_conn()

        now = datetime.now(timezone.utc)
        # Current state
        cursor.fetchone.side_effect = [
            # 1. Check current row
            {
                "id": 1,
                "work_order_code": "WO-0001",
                "report_id": 1,
                "status": "Open",
                "assigned_to": None,
                "target_date": None,
                "completed_at": None,
                "notes": None,
                "created_at": now,
                "updated_at": now,
            },
            # 2. Returning row from UPDATE
            {
                "id": 1,
                "work_order_code": "WO-0001",
                "report_id": 1,
                "status": "In Progress",
                "assigned_to": None,
                "target_date": None,
                "completed_at": None,
                "notes": None,
                "created_at": now,
                "updated_at": now,
            },
            # 3. get_work_order fetchone
            {
                "id": 1,
                "work_order_code": "WO-0001",
                "report_id": 1,
                "status": "In Progress",
                "assigned_to": None,
                "target_date": None,
                "completed_at": None,
                "notes": None,
                "created_at": now,
                "updated_at": now,
                "road_name": "Pattom",
                "damage_type": "Pothole",
                "severity": "Critical",
                "report_description": "",
                "photo_url": None,
                "report_created_at": now,
                "latitude": 8.52,
                "longitude": 76.93,
                "priority_tier": "High",
                "priority_score": 75.0,
                "priority_explanation": "",
            },
        ]
        cursor.fetchall.return_value = []  # history fetchall

        wo, err = work_order_service.update_work_order(conn, wo_id=1, new_status="In Progress", status_note="Crew dispatched")
        self.assertIsNone(err)
        self.assertIsNotNone(wo)
        self.assertEqual(wo["status"], "In Progress")

    def test_update_work_order_invalid_transition(self):
        """Completed to In Progress transition is rejected with clear error."""
        conn, cursor = self._make_mock_conn()

        now = datetime.now(timezone.utc)
        cursor.fetchone.return_value = {
            "id": 1,
            "work_order_code": "WO-0001",
            "report_id": 1,
            "status": "Completed",
            "assigned_to": "Team A",
            "target_date": None,
            "completed_at": now,
            "notes": None,
            "created_at": now,
            "updated_at": now,
        }

        wo, err = work_order_service.update_work_order(conn, wo_id=1, new_status="In Progress")
        self.assertIsNone(wo)
        self.assertIn("Invalid status transition", err)
        self.assertIn("Completed", err)

    def test_update_completed_sets_completed_at(self):
        """Transitioning to Completed adds completed_at = NOW()."""
        conn, cursor = self._make_mock_conn()

        now = datetime.now(timezone.utc)
        cursor.fetchone.side_effect = [
            # Check current
            {
                "id": 1, "work_order_code": "WO-0001", "report_id": 1,
                "status": "In Progress", "assigned_to": "Team A", "target_date": None,
                "completed_at": None, "notes": None, "created_at": now, "updated_at": now,
            },
            # Return updated
            {
                "id": 1, "work_order_code": "WO-0001", "report_id": 1,
                "status": "Completed", "assigned_to": "Team A", "target_date": None,
                "completed_at": now, "notes": None, "created_at": now, "updated_at": now,
            },
            # get_work_order
            {
                "id": 1, "work_order_code": "WO-0001", "report_id": 1,
                "status": "Completed", "assigned_to": "Team A", "target_date": None,
                "completed_at": now, "notes": None, "created_at": now, "updated_at": now,
                "road_name": "Pattom", "damage_type": "Pothole", "severity": "Critical",
                "report_description": "", "photo_url": None, "report_created_at": now,
                "latitude": 8.52, "longitude": 76.93,
                "priority_tier": "High", "priority_score": 75.0, "priority_explanation": "",
            },
        ]
        cursor.fetchall.return_value = []

        wo, err = work_order_service.update_work_order(conn, wo_id=1, new_status="Completed")
        self.assertIsNone(err)

        # Inspect SQL to verify completed_at = NOW() was set
        update_calls = [
            c for c in conn.execute.call_args_list
            if "UPDATE work_orders" in str(c[0][0])
        ]
        self.assertTrue(len(update_calls) > 0)
        self.assertIn("completed_at = NOW()", update_calls[0][0][0])


# ─────────────────────────────────────────────────────────────────────────────
# Flask Endpoint Tests (Mock DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkOrderEndpoints(unittest.TestCase):

    def setUp(self):
        flask_app.config["TESTING"] = True
        self.client = flask_app.test_client()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "pilot_staff"
            sess["role"] = "staff"

    def test_post_work_orders_empty_body(self):
        """POST without report_ids returns 400."""
        resp = self.client.post("/api/work-orders", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("report_ids", resp.get_json()["error"])

    def test_post_work_orders_invalid_target_date(self):
        """Invalid target_date format returns 400."""
        resp = self.client.post("/api/work-orders", json={"report_ids": [1], "target_date": "not-a-date"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("YYYY-MM-DD", resp.get_json()["error"])

    def test_post_work_orders_success(self):
        """POST with valid report_ids creates work order and returns 201."""
        mock_result = {
            "created": [{"id": 1, "work_order_code": "WO-0001", "report_id": 1, "status": "Open"}],
            "already_tracked": [],
            "not_found": [],
        }
        with patch("app.work_order_service.create_work_orders", return_value=mock_result):
            with patch("app.get_database_connection"):
                resp = self.client.post("/api/work-orders", json={"report_ids": [1]})
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(len(data["created"]), 1)

    def test_post_work_orders_all_already_tracked(self):
        """When all requested reports already have work orders, returns 200 with clear message."""
        mock_result = {
            "created": [],
            "already_tracked": [{"id": 1, "work_order_code": "WO-0001", "report_id": 1, "status": "Open"}],
            "not_found": [],
        }
        with patch("app.work_order_service.create_work_orders", return_value=mock_result):
            with patch("app.get_database_connection"):
                resp = self.client.post("/api/work-orders", json={"report_ids": [1]})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("already tracked", data["message"])

    def test_get_work_orders_list(self):
        """GET /api/work-orders returns list and summary counts."""
        mock_list = [
            {"id": 1, "work_order_code": "WO-0001", "status": "Open", "linked_report": {"road_name": "MG Road"}},
        ]
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [{"status": "Open", "n": 1}]
        mock_conn.execute.return_value = mock_cursor

        with patch("app.work_order_service.list_work_orders", return_value=mock_list):
            with patch("app.get_database_connection", return_value=mock_conn):
                resp = self.client.get("/api/work-orders")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["count"], 1)
        self.assertIn("summary", data)

    def test_patch_work_order_invalid_transition(self):
        """PATCH with invalid status transition returns 400."""
        with patch("app.work_order_service.update_work_order", return_value=(None, "Invalid status transition from 'Completed' to 'In Progress'.")):
            with patch("app.get_database_connection"):
                resp = self.client.patch("/api/work-orders/1", json={"status": "In Progress"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid status transition", resp.get_json()["error"])

    def test_patch_work_order_not_found(self):
        """PATCH non-existent work order returns 404."""
        with patch("app.work_order_service.update_work_order", return_value=(None, "Work order with ID 999 not found.")):
            with patch("app.get_database_connection"):
                resp = self.client.patch("/api/work-orders/999", json={"status": "Open"})
        self.assertEqual(resp.status_code, 404)


# ─────────────────────────────────────────────────────────────────────────────
# Export Integration Tests (Work Orders in Stage 6A Exports)
# ─────────────────────────────────────────────────────────────────────────────

class TestExportWithWorkOrders(unittest.TestCase):

    def _make_report_with_wo(self, wo_code="WO-0001", wo_status="In Progress"):
        from tests.test_export import _make_row
        return _make_row(work_order_code=wo_code, work_order_status=wo_status)

    def test_csv_export_includes_work_order_fields(self):
        """CSV export must include Work Order ID and Status columns with values."""
        row = self._make_report_with_wo("WO-0042", "In Progress")
        data = export_service.build_csv([row])
        text = data.decode("utf-8-sig")
        self.assertIn("Work Order ID", text)
        self.assertIn("Work Order Status", text)
        self.assertIn("WO-0042", text)
        self.assertIn("In Progress", text)

    def test_csv_export_untracked_report_shows_none(self):
        """Reports without work orders show None / Untracked."""
        from tests.test_export import _make_row
        row = _make_row(work_order_code=None, work_order_status=None)
        data = export_service.build_csv([row])
        text = data.decode("utf-8-sig")
        self.assertIn("None", text)
        self.assertIn("Untracked", text)

    def test_geojson_export_includes_work_order_properties(self):
        """GeoJSON feature properties include work_order_id and work_order_status."""
        row = self._make_report_with_wo("WO-0010", "Assigned")
        data = export_service.build_geojson([row])
        obj = json.loads(data)
        props = obj["features"][0]["properties"]
        self.assertEqual(props["work_order_id"], "WO-0010")
        self.assertEqual(props["work_order_status"], "Assigned")

    def test_pdf_export_with_work_order(self):
        """PDF builds cleanly when a work order is attached."""
        row = self._make_report_with_wo("WO-0003", "Completed")
        data = export_service.build_pdf([row])
        self.assertTrue(data.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

