"""
RoadWise AI - Stage 8: Public Road-Repair Progress Updates Test Suite.
Tests officer update workflow, role-based authorization, state machine transitions,
append-only timeline with corrections, public query filtering with privacy redactions,
and attachment validation/access controls in complete isolation.
Does NOT modify the development database.
"""
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

_current_dir = os.path.dirname(os.path.abspath(__file__))
_roadwise_dir = os.path.abspath(os.path.join(_current_dir, ".."))
_project_dir = os.path.abspath(os.path.join(_roadwise_dir, ".."))
for _p in (_project_dir, _roadwise_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import app as flask_app
import progress_update_service
import backup_restore
from PIL import Image


def _create_sample_jpeg_bytes() -> bytes:
    """Generate minimal genuine JPEG image in memory."""
    buf = io.BytesIO()
    img = Image.new("RGB", (10, 10), color=(255, 0, 0))
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _create_sample_png_bytes() -> bytes:
    """Generate minimal genuine PNG image in memory."""
    buf = io.BytesIO()
    img = Image.new("RGB", (10, 10), color=(0, 255, 0))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_sample_pdf_bytes() -> bytes:
    """Generate minimal genuine PDF document in memory."""
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Officer Authorization & Role Permissions
# ─────────────────────────────────────────────────────────────────────────────

class TestProgressUpdatePermissions(unittest.TestCase):

    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["LOGIN_DISABLED"] = False
        self.client = flask_app.test_client()

    def test_unauthenticated_post_update_returns_401(self):
        """Unauthenticated requests to post progress updates must be rejected with 401."""
        resp = self.client.post("/api/work-orders/1/updates", json={"work_completed": "Patching started"})
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Authentication required", resp.get_json()["error"])

    def test_unauthenticated_publish_endpoint_returns_401(self):
        """Unauthenticated requests to publish an update must be rejected with 401."""
        resp = self.client.post("/api/work-orders/updates/1/publish")
        self.assertEqual(resp.status_code, 401)

    def test_can_user_update_work_order_admin_can_update_any(self):
        """Admin can update any work order regardless of assignment."""
        admin_user = {"id": 1, "username": "admin1", "role": "admin"}
        wo = {"id": 10, "work_order_code": "WO-0010", "assigned_to": "crew_beta"}
        can_update, err = progress_update_service.can_user_update_work_order(admin_user, wo)
        self.assertTrue(can_update)
        self.assertIsNone(err)

    def test_can_user_update_work_order_assigned_staff_allowed(self):
        """Staff assigned to the work order can update it."""
        staff_user = {"id": 2, "username": "officer_pattom", "role": "staff", "full_name": "Pattom Officer"}
        wo = {"id": 10, "work_order_code": "WO-0010", "assigned_to": "officer_pattom"}
        can_update, err = progress_update_service.can_user_update_work_order(staff_user, wo)
        self.assertTrue(can_update)

    def test_can_user_update_work_order_unassigned_staff_rejected(self):
        """Staff NOT assigned to the work order must be rejected."""
        staff_user = {"id": 3, "username": "officer_kowdiar", "role": "staff", "full_name": "Kowdiar Officer"}
        wo = {"id": 10, "work_order_code": "WO-0010", "assigned_to": "officer_pattom"}
        can_update, err = progress_update_service.can_user_update_work_order(staff_user, wo)
        self.assertFalse(can_update)
        self.assertIn("Forbidden", err)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Status Transition & Completion Rules
# ─────────────────────────────────────────────────────────────────────────────

class TestProgressUpdateStatusTransitions(unittest.TestCase):

    def _make_mock_conn(self, wo_status="Open", assigned_to="officer1"):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "id": 1,
            "work_order_code": "WO-0001",
            "report_id": 1,
            "status": wo_status,
            "assigned_to": assigned_to,
            "target_date": None,
        }
        mock_conn.execute.return_value = mock_cursor
        return mock_conn, mock_cursor

    def test_valid_transition_in_progress_update(self):
        """Transition from Open to In Progress succeeds."""
        mock_conn, mock_cursor = self._make_mock_conn(wo_status="Open")
        now = datetime.now(timezone.utc)
        mock_cursor.fetchone.side_effect = [
            # 1. Fetch work order
            {"id": 1, "work_order_code": "WO-0001", "report_id": 1, "status": "Open", "assigned_to": "officer1", "target_date": None},
            # 2. Return from INSERT into work_order_updates
            {
                "id": 101, "work_order_id": 1, "author_user_id": 2, "author_username": "officer1",
                "status": "In Progress", "progress_pct": 50, "work_completed": "Surface excavation done",
                "blockers": None, "next_actions": None, "estimated_completion_date": None,
                "internal_notes": None, "is_published": False, "published_at": None, "published_by": None,
                "is_correction": False, "correction_for_id": None, "created_at": now,
            },
        ]

        user = {"id": 2, "username": "officer1", "role": "staff"}
        update, err = progress_update_service.create_progress_update(
            mock_conn,
            wo_id=1,
            user=user,
            work_completed="Surface excavation done",
            status="In Progress",
            progress_pct=50,
        )
        self.assertIsNone(err)
        self.assertIsNotNone(update)
        self.assertEqual(update["status"], "In Progress")

        # Verify UPDATE on work_orders table occurred
        update_calls = [c for c in mock_conn.execute.call_args_list if "UPDATE work_orders" in str(c[0][0])]
        self.assertTrue(len(update_calls) > 0)

    def test_invalid_transition_rejected(self):
        """Illegal transition (Completed -> In Progress directly) is rejected."""
        mock_conn, _ = self._make_mock_conn(wo_status="Completed")
        user = {"id": 2, "username": "officer1", "role": "staff"}
        update, err = progress_update_service.create_progress_update(
            mock_conn,
            wo_id=1,
            user=user,
            work_completed="Trying to resume without reopening",
            status="In Progress",
        )
        self.assertIsNone(update)
        self.assertIn("Invalid status transition", err)

    def test_completion_requires_detailed_summary(self):
        """Transitioning to Completed requires non-empty completion summary."""
        mock_conn, _ = self._make_mock_conn(wo_status="In Progress")
        user = {"id": 2, "username": "officer1", "role": "staff"}
        update, err = progress_update_service.create_progress_update(
            mock_conn,
            wo_id=1,
            user=user,
            work_completed="Done",  # Too short (<10 chars)
            status="Completed",
        )
        self.assertIsNone(update)
        self.assertIn("completion summary", err.lower())


# ─────────────────────────────────────────────────────────────────────────────
# 3. Append-Only Timeline & Corrections
# ─────────────────────────────────────────────────────────────────────────────

class TestProgressUpdateTimelineAndCorrections(unittest.TestCase):

    def test_correction_references_parent_update(self):
        """Correction record links to previous update without deleting history."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        now = datetime.now(timezone.utc)
        mock_cursor.fetchone.side_effect = [
            # 1. Fetch work order
            {"id": 1, "work_order_code": "WO-0001", "report_id": 1, "status": "In Progress", "assigned_to": "officer1", "target_date": None},
            # 2. Check parent update exists
            {"id": 50},
            # 3. Return from INSERT
            {
                "id": 51, "work_order_id": 1, "author_user_id": 2, "author_username": "officer1",
                "status": "In Progress", "progress_pct": 60, "work_completed": "Correction: Cold mix asphalt was used, not hot mix.",
                "blockers": None, "next_actions": None, "estimated_completion_date": None,
                "internal_notes": None, "is_published": False, "published_at": None, "published_by": None,
                "is_correction": True, "correction_for_id": 50, "created_at": now,
            },
        ]
        mock_conn.execute.return_value = mock_cursor

        user = {"id": 2, "username": "officer1", "role": "staff"}
        update, err = progress_update_service.create_progress_update(
            mock_conn,
            wo_id=1,
            user=user,
            work_completed="Correction: Cold mix asphalt was used, not hot mix.",
            is_correction=True,
            correction_for_id=50,
        )
        self.assertIsNone(err)
        self.assertTrue(update["is_correction"])
        self.assertEqual(update["correction_for_id"], 50)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Attachment Validation & Storage Security
# ─────────────────────────────────────────────────────────────────────────────

class TestAttachmentValidation(unittest.TestCase):

    def test_valid_jpeg_validation(self):
        """Genuine JPEG bytes validate as 'image'."""
        jpeg_bytes = _create_sample_jpeg_bytes()
        ftype, mime, err = progress_update_service.validate_attachment(jpeg_bytes, "repair_photo.jpg")
        self.assertIsNone(err)
        self.assertEqual(ftype, "image")
        self.assertEqual(mime, "image/jpeg")

    def test_valid_png_validation(self):
        """Genuine PNG bytes validate as 'image'."""
        png_bytes = _create_sample_png_bytes()
        ftype, mime, err = progress_update_service.validate_attachment(png_bytes, "site_plan.png")
        self.assertIsNone(err)
        self.assertEqual(ftype, "image")
        self.assertEqual(mime, "image/png")

    def test_valid_pdf_validation(self):
        """Genuine PDF bytes starting with %PDF- validate as 'pdf'."""
        pdf_bytes = _create_sample_pdf_bytes()
        ftype, mime, err = progress_update_service.validate_attachment(pdf_bytes, "inspection_signoff.pdf")
        self.assertIsNone(err)
        self.assertEqual(ftype, "pdf")
        self.assertEqual(mime, "application/pdf")

    def test_disallowed_extension_rejected(self):
        """Executables or scripts are rejected."""
        ftype, mime, err = progress_update_service.validate_attachment(b"echo 1", "malicious.sh")
        self.assertIsNone(ftype)
        self.assertIn("Unsupported file type", err)

    def test_fake_pdf_magic_bytes_rejected(self):
        """File claiming to be PDF without %PDF- magic bytes is rejected."""
        fake_pdf = b"NOT_A_PDF_DOCUMENT_CONTENT"
        ftype, mime, err = progress_update_service.validate_attachment(fake_pdf, "fake.pdf")
        self.assertIsNone(ftype)
        self.assertIn("Invalid PDF document", err)

    def test_corrupted_image_rejected(self):
        """Corrupted image bytes are rejected."""
        bad_image = b"CORRUPTED_NON_IMAGE_DATA_12345"
        ftype, mime, err = progress_update_service.validate_attachment(bad_image, "bad.jpg")
        self.assertIsNone(ftype)
        self.assertIn("Could not decode", err)

    def test_oversized_file_rejected(self):
        """File exceeding 10 MB limit is rejected."""
        oversized = b"0" * (11 * 1024 * 1024)
        ftype, mime, err = progress_update_service.validate_attachment(oversized, "huge.pdf")
        self.assertIsNone(ftype)
        self.assertIn("size limit", err)

    def test_save_attachment_file_traversal_protection(self):
        """Attachments are stored securely with unique filenames inside folder."""
        with tempfile.TemporaryDirectory() as tmp_folder:
            content = _create_sample_jpeg_bytes()
            stored_name = progress_update_service.save_attachment_file(
                tmp_folder,
                content,
                "photo.jpg",
                wo_id=1,
                update_id=10,
            )
            self.assertTrue(os.path.isfile(os.path.join(tmp_folder, stored_name)))
            self.assertIn("wo_1_up_10_", stored_name)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Public Query Service & Privacy Redaction Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPublicProgressPrivacyAndRedactions(unittest.TestCase):

    def test_public_progress_filters_unpublished_and_redacts_private_fields(self):
        """Public list returns ONLY published updates and strictly omits internal notes and accounts."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        now = datetime.now(timezone.utc)

        # Count fetchone
        mock_cursor.fetchone.return_value = {"total": 1}
        # Results fetchall
        mock_cursor.fetchall.side_effect = [
            # Rows from work_order_updates
            [
                {
                    "id": 1,
                    "work_order_id": 5,
                    "work_order_code": "WO-0005",
                    "road_name": "Pattom Junction",
                    "damage_type": "Pothole",
                    "confirmed_severity": "Critical",
                    "latitude": 8.5241,
                    "longitude": 76.9366,
                    "status": "In Progress",
                    "progress_pct": 75,
                    "work_completed": "Sub-base compacted and tack coat applied.",
                    "blockers": "Minor traffic diversion required",
                    "next_actions": "Wearing course laying scheduled for tomorrow",
                    "estimated_completion_date": "2026-11-01",
                    "published_at": now,
                    "created_at": now,
                    "is_correction": False,
                    "correction_for_id": None,
                }
            ],
            # Attachments fetchall
            [
                {
                    "id": 10,
                    "update_id": 1,
                    "file_type": "image",
                    "original_filename": "subbase.jpg",
                    "file_size_bytes": 102400,
                    "caption": "Sub-base compaction verification",
                }
            ],
        ]
        mock_conn.execute.return_value = mock_cursor

        res = progress_update_service.list_public_progress_updates(mock_conn)
        self.assertEqual(res["total"], 1)
        self.assertEqual(len(res["updates"]), 1)

        pub_update = res["updates"][0]
        # Verify required public fields are present
        self.assertEqual(pub_update["work_order_code"], "WO-0005")
        self.assertEqual(pub_update["road_name"], "Pattom Junction")
        self.assertEqual(pub_update["progress_pct"], 75)
        self.assertEqual(len(pub_update["attachments"]), 1)

        # STRICT PRIVACY REDACTION CHECKS:
        self.assertNotIn("internal_notes", pub_update)
        self.assertNotIn("author_username", pub_update)
        self.assertNotIn("author_user_id", pub_update)
        self.assertNotIn("published_by", pub_update)
        self.assertNotIn("assigned_to", pub_update)
        self.assertNotIn("stored_filename", pub_update["attachments"][0])


# ─────────────────────────────────────────────────────────────────────────────
# 6. Attachment Access Control & Backup Coverage
# ─────────────────────────────────────────────────────────────────────────────

class TestAttachmentAccessAndBackupCoverage(unittest.TestCase):

    def setUp(self):
        flask_app.config["TESTING"] = True
        self.client = flask_app.test_client()

    def test_unauthenticated_cannot_download_unpublished_attachment(self):
        """Unauthenticated user receives 403 on unpublished attachment."""
        mock_rec = {
            "id": 1,
            "is_published": False,
            "is_approved": True,
            "stored_filename": "test.jpg",
            "original_filename": "test.jpg",
            "mime_type": "image/jpeg",
        }
        with patch("app.progress_update_service.get_attachment_record", return_value=mock_rec):
            with patch("app.get_database_connection"):
                resp = self.client.get("/api/work-orders/attachments/1")
        self.assertEqual(resp.status_code, 403)

    def test_backup_and_restore_attachments(self):
        """backup_attachments and restore_attachments preserve all files safely."""
        with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as backup_dir, tempfile.TemporaryDirectory() as restore_dir:
            f1 = os.path.join(src_dir, "att_001.pdf")
            with open(f1, "wb") as f:
                f.write(_create_sample_pdf_bytes())

            res = backup_restore.backup_attachments(src_dir, backup_dir)
            self.assertEqual(res["file_count"], 1)
            self.assertTrue(os.path.isfile(res["zip_path"]))

            count = backup_restore.restore_attachments(res["zip_path"], restore_dir)
            self.assertEqual(count, 1)
            self.assertTrue(os.path.isfile(os.path.join(restore_dir, "att_001.pdf")))


if __name__ == "__main__":
    unittest.main()
