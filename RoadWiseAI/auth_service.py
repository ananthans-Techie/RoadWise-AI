"""
RoadWise AI - Authentication and Authorization Service (Stage 7).
Provides pilot-ready, secure user management, role-based access control,
and password hashing using Werkzeug security primitives.
"""
import re
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash

ALLOWED_ROLES = {"admin", "staff"}
USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{3,64}$")
MIN_PASSWORD_LENGTH = 8


def validate_username(username):
    """Validate username format (3-64 chars, alphanumeric, underscores, hyphens, dots)."""
    if not username or not isinstance(username, str):
        return False, "Username is required."
    clean = username.strip()
    if not USERNAME_REGEX.match(clean):
        return False, "Username must be 3-64 characters and contain only letters, numbers, underscores, hyphens, or dots."
    return True, None


def validate_password(password):
    """Validate password strength (minimum length)."""
    if not password or not isinstance(password, str):
        return False, "Password is required."
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return True, None


def hash_password(plain_password):
    """Generate secure salted scrypt or pbkdf2 hash for password."""
    return generate_password_hash(plain_password)


def verify_password(stored_hash, plain_password):
    """Verify plain password against stored salted hash."""
    if not stored_hash or not plain_password:
        return False
    return check_password_hash(stored_hash, plain_password)


def create_user(conn, username, password, role="staff", full_name=None):
    """
    Create a new pilot user with a hashed password.
    Returns (user_dict, error_msg).
    """
    valid_u, err_u = validate_username(username)
    if not valid_u:
        return None, err_u

    valid_p, err_p = validate_password(password)
    if not valid_p:
        return None, err_p

    clean_role = (role or "staff").strip().lower()
    if clean_role not in ALLOWED_ROLES:
        return None, f"Invalid role '{role}'. Allowed roles: {', '.join(sorted(ALLOWED_ROLES))}."

    clean_username = username.strip()
    clean_name = full_name.strip() if full_name else None

    # Check for existing user
    cursor = conn.execute(
        "SELECT id FROM pilot_users WHERE LOWER(username) = LOWER(%s)",
        (clean_username,),
    )
    if cursor.fetchone():
        return None, f"User '{clean_username}' already exists."

    pwd_hash = hash_password(password)

    cursor = conn.execute(
        """
        INSERT INTO pilot_users
            (username, password_hash, role, full_name, is_active)
        VALUES
            (%s, %s, %s, %s, TRUE)
        RETURNING id, username, role, full_name, is_active, created_at, updated_at
        """,
        (clean_username, pwd_hash, clean_role, clean_name),
    )
    row = cursor.fetchone()
    return dict(row), None


def authenticate_user(conn, username, password):
    """
    Authenticate user credentials.
    Returns (user_dict, error_msg). Never returns the password hash.
    """
    if not username or not password:
        return None, "Username and password are required."

    clean_username = str(username).strip()
    cursor = conn.execute(
        """
        SELECT id, username, password_hash, role, full_name, is_active, created_at
        FROM pilot_users
        WHERE LOWER(username) = LOWER(%s)
        """,
        (clean_username,),
    )
    user = cursor.fetchone()
    if not user:
        return None, "Invalid username or password."

    if not user.get("is_active"):
        return None, "Account is disabled. Contact system administrator."

    if not verify_password(user["password_hash"], password):
        return None, "Invalid username or password."

    safe_user = {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "full_name": user.get("full_name"),
        "created_at": user.get("created_at"),
    }
    return safe_user, None


def get_user_by_id(conn, user_id):
    """Retrieve user by ID without exposing password hash."""
    cursor = conn.execute(
        """
        SELECT id, username, role, full_name, is_active, created_at
        FROM pilot_users
        WHERE id = %s
        """,
        (user_id,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def list_users(conn):
    """List all registered users without password hashes."""
    cursor = conn.execute(
        """
        SELECT id, username, role, full_name, is_active, created_at
        FROM pilot_users
        ORDER BY created_at ASC
        """
    )
    return [dict(r) for r in cursor.fetchall()]


def change_user_password(conn, user_id, new_password):
    """Update password for an existing user."""
    valid_p, err_p = validate_password(new_password)
    if not valid_p:
        return False, err_p

    new_hash = hash_password(new_password)
    cursor = conn.execute(
        """
        UPDATE pilot_users
        SET password_hash = %s, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        RETURNING id
        """,
        (new_hash, user_id),
    )
    if not cursor.fetchone():
        return False, f"User with ID {user_id} not found."
    return True, None

