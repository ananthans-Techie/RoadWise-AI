"""
RoadWise AI - Pilot User Management CLI (Stage 7).
Enables secure administrative management of municipal staff accounts
without hardcoding default credentials or exposing plaintext secrets.

Usage Examples:
  python manage_users.py create --username officer1 --role staff --full-name "Pattom Field Officer"
  python manage_users.py list
  python manage_users.py passwd --username officer1
  python manage_users.py deactivate --username officer1
"""
import argparse
import getpass
import sys
import psycopg
from psycopg.rows import dict_row

from app import configure_database, get_database_connection
import auth_service


def get_connection():
    try:
        configure_database()
        return get_database_connection()
    except Exception as exc:
        print(f"ERROR: Could not connect to database ({type(exc).__name__}). Check .env configuration.", file=sys.stderr)
        sys.exit(1)


def cmd_create(args):
    username = args.username
    role = args.role or "staff"
    full_name = args.full_name

    password = args.password
    if not password:
        password = getpass.getpass(f"Enter password for user '{username}': ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("ERROR: Passwords do not match.", file=sys.stderr)
            sys.exit(1)

    with get_connection() as conn:
        user, err = auth_service.create_user(
            conn,
            username=username,
            password=password,
            role=role,
            full_name=full_name,
        )
        if err:
            print(f"ERROR: {err}", file=sys.stderr)
            sys.exit(1)
        conn.commit()

    print(f"SUCCESS: User '{user['username']}' created with role '{user['role']}' (ID: {user['id']}).")


def cmd_list(args):
    with get_connection() as conn:
        users = auth_service.list_users(conn)

    if not users:
        print("No pilot users registered yet. Use 'create' to add a municipal staff account.")
        return

    print(f"\n{'ID':<5} {'Username':<20} {'Role':<10} {'Active':<8} {'Full Name':<25} {'Created At'}")
    print("-" * 85)
    for u in users:
        active_str = "Yes" if u.get("is_active") else "No"
        name_str = u.get("full_name") or "-"
        created_str = str(u.get("created_at"))[:19]
        print(f"{u['id']:<5} {u['username']:<20} {u['role']:<10} {active_str:<8} {name_str:<25} {created_str}")
    print()


def cmd_passwd(args):
    username = args.username
    password = args.password
    if not password:
        password = getpass.getpass(f"Enter new password for '{username}': ")
        confirm = getpass.getpass("Confirm new password: ")
        if password != confirm:
            print("ERROR: Passwords do not match.", file=sys.stderr)
            sys.exit(1)

    with get_connection() as conn:
        cursor = conn.execute("SELECT id FROM pilot_users WHERE LOWER(username) = LOWER(%s)", (username.strip(),))
        row = cursor.fetchone()
        if not row:
            print(f"ERROR: User '{username}' not found.", file=sys.stderr)
            sys.exit(1)
        user_id = row["id"]

        ok, err = auth_service.change_user_password(conn, user_id, password)
        if not ok:
            print(f"ERROR: {err}", file=sys.stderr)
            sys.exit(1)
        conn.commit()

    print(f"SUCCESS: Password updated for user '{username}'.")


def cmd_toggle_active(args, is_active):
    username = args.username
    status_str = "activated" if is_active else "deactivated"
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE pilot_users
            SET is_active = %s, updated_at = CURRENT_TIMESTAMP
            WHERE LOWER(username) = LOWER(%s)
            RETURNING id
            """,
            (is_active, username.strip()),
        )
        if not cursor.fetchone():
            print(f"ERROR: User '{username}' not found.", file=sys.stderr)
            sys.exit(1)
        conn.commit()

    print(f"SUCCESS: User '{username}' has been {status_str}.")


def main():
    parser = argparse.ArgumentParser(description="RoadWise AI - Pilot User Account Manager")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # create
    p_create = subparsers.add_parser("create", help="Create a new user account")
    p_create.add_argument("--username", required=True, help="Unique username (3-64 chars)")
    p_create.add_argument("--role", choices=["staff", "admin"], default="staff", help="User role (default: staff)")
    p_create.add_argument("--full-name", help="Optional display or real name")
    p_create.add_argument("--password", help="Plain password (if omitted, prompts securely)")

    # list
    subparsers.add_parser("list", help="List all registered pilot users")

    # passwd
    p_passwd = subparsers.add_parser("passwd", help="Change user password")
    p_passwd.add_argument("--username", required=True, help="Username to update")
    p_passwd.add_argument("--password", help="New plain password (if omitted, prompts securely)")

    # deactivate
    p_deact = subparsers.add_parser("deactivate", help="Disable a user account")
    p_deact.add_argument("--username", required=True, help="Username to deactivate")

    # activate
    p_act = subparsers.add_parser("activate", help="Enable a user account")
    p_act.add_argument("--username", required=True, help="Username to activate")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "create":
        cmd_create(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "passwd":
        cmd_passwd(args)
    elif args.command == "deactivate":
        cmd_toggle_active(args, is_active=False)
    elif args.command == "activate":
        cmd_toggle_active(args, is_active=True)


if __name__ == "__main__":
    main()

