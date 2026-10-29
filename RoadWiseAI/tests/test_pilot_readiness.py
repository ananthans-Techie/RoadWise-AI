"""
RoadWise AI - Stage 7: Field-Pilot Readiness Test Suite.
Tests access control, pilot authentication, role enforcement, secret protection,
health check diagnostics, and backup/restore utilities in complete isolation.
Does NOT connect to or alter the development database.
"""
import os
import sys
import io
import zipfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_current_dir = os.path.dirname(os.path.abspath(__file__))
_roadwise_dir = os.path.abspath(os.path.join(_current_dir, ".."))
_project_dir = os.path.abspath(os.path.join(_roadwise_dir, ".."))
for _p in (_project_dir, _roadwise_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import app as flask_app, mask_database_url
import auth_service
import backup_restore


# ─────────────────────────────────────────────────────────────────────────────
# 1. Access Control & Role Enforcement Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPilotAccessControl(unittest.TestCase):

    def setUp(self):
        flask_app.config["TESTING"] = True
        flask_app.config["LOGIN_DISABLED"] = False
        self.client = flask_app.test_client()

    def test_unauthenticated_post_work_orders_returns_401(self):
        """Unauthenticated requests to create work orders must be rejected with 401."""
        resp = self.client.post("/api/work-orders", json={"report_ids": [1]})
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertEqual(data["status"], "error")
        self.assertIn("Authentication required", data["error"])

    def test_unauthenticated_patch_work_order_returns_401(self):
        """Unauthenticated requests to update work orders must be rejected with 401."""
        resp = self.client.patch("/api/work-orders/1", json={"status": "In Progress"})
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertIn("Authentication required", data["error"])

    def test_unauthenticated_priority_compute_returns_401(self):
        """Unauthenticated requests to recompute priorities must be rejected with 401."""
        resp = self.client.post("/api/analytics/priority/compute")
        self.assertEqual(resp.status_code, 401)
        data = resp.get_json()
        self.assertIn("Authentication required", data["error"])

    def test_authenticated_staff_can_post_work_orders(self):
        """Authenticated staff can create work orders."""
        mock_result = {
            "created": [{"id": 1, "work_order_code": "WO-0001", "report_id": 1, "status": "Open"}],
            "already_tracked": [],
            "not_found": [],
        }
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "officer_pattom"
            sess["role"] = "staff"

        with patch("app.work_order_service.create_work_orders", return_value=mock_result):
            with patch("app.get_database_connection"):
                resp = self.client.post("/api/work-orders", json={"report_ids": [1]})
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")

    def test_authenticated_staff_can_patch_work_order(self):
        """Authenticated staff can update work order lifecycle."""
        mock_wo = {"id": 1, "work_order_code": "WO-0001", "status": "In Progress"}
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "officer_pattom"
            sess["role"] = "staff"

        with patch("app.work_order_service.update_work_order", return_value=(mock_wo, None)):
            with patch("app.get_database_connection"):
                resp = self.client.patch("/api/work-orders/1", json={"status": "In Progress"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")

    def test_auth_login_missing_fields(self):
        """Login requires both username and password."""
        resp = self.client.post("/api/auth/login", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Username and password are required", resp.get_json()["error"])

    def test_auth_login_invalid_credentials(self):
        """Invalid credentials return 401."""
        with patch("app.auth_service.authenticate_user", return_value=(None, "Invalid username or password.")):
            with patch("app.get_database_connection"):
                resp = self.client.post("/api/auth/login", json={"username": "fake", "password": "bad"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.get_json()["status"], "error")

    def test_auth_login_success_sets_session(self):
        """Successful login returns user info and initializes session."""
        mock_user = {
            "id": 1,
            "username": "officer1",
            "role": "staff",
            "full_name": "Field Officer 1",
        }
        with patch("app.auth_service.authenticate_user", return_value=(mock_user, None)):
            with patch("app.get_database_connection"):
                resp = self.client.post("/api/auth/login", json={"username": "officer1", "password": "correct_password"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["user"]["username"], "officer1")

        # Verify session state via /api/auth/me
        me_resp = self.client.get("/api/auth/me")
        self.assertEqual(me_resp.status_code, 200)
        me_data = me_resp.get_json()
        self.assertTrue(me_data["authenticated"])
        self.assertEqual(me_data["user"]["username"], "officer1")

    def test_auth_logout_clears_session(self):
        """Logout clears the session and returns unauthenticated state."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "officer1"
            sess["role"] = "staff"

        resp = self.client.post("/api/auth/logout")
        self.assertEqual(resp.status_code, 200)

        me_resp = self.client.get("/api/auth/me")
        self.assertFalse(me_resp.get_json()["authenticated"])

    def test_auth_me_unauthenticated(self):
        """Unauthenticated user calling /api/auth/me receives authenticated: false."""
        resp = self.client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["authenticated"])
        self.assertIsNone(data["user"])


# ─────────────────────────────────────────────────────────────────────────────
# 2. Authentication Service & Password Hashing Unit Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPilotAuthService(unittest.TestCase):

    def test_password_hashing_and_verification(self):
        """Password hashing generates salted hash and verifies accurately."""
        plain = "SuperSecurePassword2026!"
        h = auth_service.hash_password(plain)
        self.assertNotEqual(h, plain)
        self.assertTrue(auth_service.verify_password(h, plain))
        self.assertFalse(auth_service.verify_password(h, "WrongPassword"))

    def test_validate_username_formats(self):
        """Validate acceptable and unacceptable username characters."""
        self.assertTrue(auth_service.validate_username("officer1")[0])
        self.assertTrue(auth_service.validate_username("pwd.engineer_01")[0])
        self.assertFalse(auth_service.validate_username("ab")[0])  # too short (<3)
        self.assertFalse(auth_service.validate_username("user with spaces")[0])
        self.assertFalse(auth_service.validate_username("")[0])

    def test_validate_password_strength(self):
        """Password must meet minimum length requirements."""
        self.assertTrue(auth_service.validate_password("12345678")[0])
        self.assertFalse(auth_service.validate_password("short")[0])
        self.assertFalse(auth_service.validate_password("")[0])

    def test_create_user_saves_hashed_password(self):
        """Creating a user passes hashed password to SQL INSERT."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # Mock SELECT checking duplicate user
        mock_cursor.fetchone.side_effect = [
            None,  # No duplicate
            {
                "id": 1,
                "username": "officer_tvm",
                "role": "staff",
                "full_name": "Trivandrum Officer",
                "is_active": True,
                "created_at": "2026-10-28 10:00:00",
                "updated_at": "2026-10-28 10:00:00",
            },
        ]
        mock_conn.execute.return_value = mock_cursor

        user, err = auth_service.create_user(
            mock_conn,
            username="officer_tvm",
            password="SecurePassword2026",
            role="staff",
            full_name="Trivandrum Officer",
        )
        self.assertIsNone(err)
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], "officer_tvm")

        # Verify SQL parameters never contained the plain password
        insert_calls = [c for c in mock_conn.execute.call_args_list if "INSERT INTO pilot_users" in str(c[0][0])]
        self.assertEqual(len(insert_calls), 1)
        params = insert_calls[0][0][1]
        self.assertNotIn("SecurePassword2026", params)
        # Verify the second param is a valid hash
        self.assertTrue(auth_service.verify_password(params[1], "SecurePassword2026"))

    def test_authenticate_user_rejects_inactive_account(self):
        """Disabled user account cannot log in even with correct password."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        pwd = "ValidPassword123"
        hashed = auth_service.hash_password(pwd)
        mock_cursor.fetchone.return_value = {
            "id": 2,
            "username": "disabled_user",
            "password_hash": hashed,
            "role": "staff",
            "is_active": False,
        }
        mock_conn.execute.return_value = mock_cursor

        user, err = auth_service.authenticate_user(mock_conn, "disabled_user", pwd)
        self.assertIsNone(user)
        self.assertIn("disabled", err.lower())

    def test_list_users_masks_password_hash(self):
        """User listing excludes password_hash from result."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "username": "officer1", "role": "staff", "full_name": "Officer One", "is_active": True},
        ]
        mock_conn.execute.return_value = mock_cursor

        users = auth_service.list_users(mock_conn)
        self.assertEqual(len(users), 1)
        self.assertNotIn("password_hash", users[0])


# ─────────────────────────────────────────────────────────────────────────────
# 3. Secret Protection & Sanitization Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPilotSecretHandling(unittest.TestCase):

    def setUp(self):
        flask_app.config["TESTING"] = True
        self.client = flask_app.test_client()

    def test_mask_database_url_masks_password(self):
        """Database connection strings have passwords safely masked."""
        url = "postgresql://postgres:SecretPassword123@127.0.0.1:5432/gisdb"
        masked = mask_database_url(url)
        self.assertNotIn("SecretPassword123", masked)
        self.assertIn("postgres:***", masked)
        self.assertIn("127.0.0.1:5432/gisdb", masked)

    def test_config_endpoint_never_exposes_database_url_or_secret_key(self):
        """Client /api/config response must never contain DATABASE_URL or SECRET_KEY."""
        resp = self.client.get("/api/config")
        self.assertEqual(resp.status_code, 200)
        raw_text = resp.get_data(as_text=True)
        self.assertNotIn("DATABASE_URL", raw_text)
        self.assertNotIn("SECRET_KEY", raw_text)
        self.assertNotIn("postgresql://", raw_text)

    def test_session_cookie_security_attributes(self):
        """Session cookies are configured with HttpOnly and SameSite=Lax."""
        self.assertTrue(flask_app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(flask_app.config["SESSION_COOKIE_SAMESITE"], "Lax")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Health Check Endpoint Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPilotHealthCheck(unittest.TestCase):

    def setUp(self):
        flask_app.config["TESTING"] = True
        self.client = flask_app.test_client()

    def test_health_check_healthy_db(self):
        """Healthy database returns status ok and pilot readiness indicators."""
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value={"total": 5})),
            MagicMock(fetchone=MagicMock(return_value={"version": "POSTGIS=3.4.0"})),
        ]
        with patch("app.get_database_connection", return_value=mock_conn):
            resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("pilot", data)
        self.assertIn("persistent_uploads", data["pilot"])
        self.assertIn("auth_configured", data["pilot"])
        self.assertTrue(data["pilot"]["auth_configured"])

    def test_health_check_db_failure_no_secret_leak(self):
        """Database failure returns 503 without leaking connection strings or passwords."""
        with patch("app.get_database_connection", side_effect=RuntimeError("Connection refused to postgresql://postgres:Secret123@127.0.0.1:5432/gisdb")):
            resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 503)
        raw = resp.get_data(as_text=True)
        self.assertNotIn("Secret123", raw)
        self.assertNotIn("postgresql://", raw)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Backup & Recovery Utility Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPilotBackupRestore(unittest.TestCase):

    def test_backup_and_restore_photos(self):
        """Verify archiving uploads to zip and restoring into an isolated folder."""
        with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as backup_dir, tempfile.TemporaryDirectory() as restore_dir:
            # Create sample photo files
            f1 = os.path.join(src_dir, "report_001.jpg")
            with open(f1, "wb") as f:
                f.write(b"SAMPLE_JPEG_DATA_1")

            f2 = os.path.join(src_dir, "report_002.jpg")
            with open(f2, "wb") as f:
                f.write(b"SAMPLE_JPEG_DATA_2")

            # Perform backup
            res = backup_restore.backup_photos(src_dir, backup_dir)
            self.assertEqual(res["file_count"], 2)
            self.assertTrue(os.path.isfile(res["zip_path"]))

            # Perform restore into isolated directory
            count = backup_restore.restore_photos(res["zip_path"], restore_dir)
            self.assertEqual(count, 2)
            self.assertTrue(os.path.isfile(os.path.join(restore_dir, "report_001.jpg")))
            self.assertTrue(os.path.isfile(os.path.join(restore_dir, "report_002.jpg")))

    def test_restore_photos_rejects_nonempty_directory_without_overwrite(self):
        """Restore refuses to extract into a non-empty directory without allow_overwrite."""
        with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as backup_dir, tempfile.TemporaryDirectory() as restore_dir:
            f1 = os.path.join(src_dir, "photo.jpg")
            with open(f1, "wb") as f:
                f.write(b"DATA")

            res = backup_restore.backup_photos(src_dir, backup_dir)

            # Put existing file in restore dir
            with open(os.path.join(restore_dir, "existing.txt"), "w") as f:
                f.write("existing")

            with self.assertRaises(RuntimeError) as ctx:
                backup_restore.restore_photos(res["zip_path"], restore_dir, allow_overwrite=False)
            self.assertIn("not empty", str(ctx.exception))

    def test_restore_database_refuses_gisdb_without_force(self):
        """Restore function refuses to overwrite primary 'gisdb' without explicit force=True."""
        with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as f:
            f.write(b"-- sample dump\n")
            sql_path = f.name

        try:
            with self.assertRaises(ValueError) as ctx:
                backup_restore.restore_database(
                    sql_path,
                    "postgresql://postgres:pwd@127.0.0.1:5432/gisdb",
                    force=False,
                )
            self.assertIn("SAFETY VIOLATION", str(ctx.exception))
            self.assertIn("gisdb", str(ctx.exception))
        finally:
            if os.path.isfile(sql_path):
                os.remove(sql_path)

    def test_mask_url_formats(self):
        """Verify URL masking across various database URLs."""
        self.assertEqual(
            backup_restore.mask_url("postgresql://postgres:pass@localhost:5432/gisdb"),
            "postgresql://postgres:***@localhost:5432/gisdb",
        )
        self.assertEqual(
            backup_restore.mask_url("postgresql://user:p%40ss@127.0.0.1:5432/gisdb"),
            "postgresql://user:***@127.0.0.1:5432/gisdb",
        )


if __name__ == "__main__":
    unittest.main()
