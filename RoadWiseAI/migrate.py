"""
Database Migration Script for RoadWise AI.
Applies version-controlled SQL migrations to PostgreSQL/PostGIS.
Safely rerunnable and idempotent (never drops tables or erases reports).
"""
import os
import sys
import urllib.parse
from dotenv import load_dotenv, find_dotenv
import psycopg


def normalize_database_url(url):
    """Safely URL-encode passwords containing reserved URI characters like '@'."""
    if not url or not (url.startswith("postgresql://") or url.startswith("postgres://")):
        return url
    prefix, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    last_at = rest.rfind("@")
    userinfo = rest[:last_at]
    hostinfo = rest[last_at + 1:]
    if ":" in userinfo:
        user, pwd = userinfo.split(":", 1)
        encoded_pwd = urllib.parse.quote(urllib.parse.unquote(pwd), safe="")
        return f"{prefix}://{user}:{encoded_pwd}@{hostinfo}"
    return url


def load_app_env():
    """Load .env file, ignoring placeholders in favor of configured values."""
    load_dotenv(find_dotenv(usecwd=True))
    for env_path in [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    ]:
        if os.path.isfile(env_path):
            current_val = os.environ.get("DATABASE_URL") or ""
            should_override = "YOUR_" in current_val or not current_val
            load_dotenv(env_path, override=should_override)


def run_migration():
    load_app_env()

    raw_url = os.environ.get("DATABASE_URL") or os.environ.get("ROADWISE_DATABASE_URL")
    if not raw_url or "YOUR_" in raw_url:
        print(
            "ERROR: DATABASE_URL environment variable is missing or contains placeholders.\n"
            "Please configure your .env file with your local PostgreSQL credentials:\n"
            "  DATABASE_URL=postgresql://postgres:<password>@127.0.0.1:5432/gisdb",
            file=sys.stderr,
        )
        sys.exit(1)

    database_url = normalize_database_url(raw_url)

    migrations_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "migrations",
    )

    if not os.path.isdir(migrations_dir):
        print(f"ERROR: Migrations directory not found: {migrations_dir}", file=sys.stderr)
        sys.exit(1)

    sql_files = sorted(
        [f for f in os.listdir(migrations_dir) if f.endswith(".sql")]
    )

    if not sql_files:
        print("No migration files found to apply.", file=sys.stderr)
        return

    print("Connecting to PostgreSQL to apply migrations...")
    try:
        with psycopg.connect(database_url) as conn:
            for sql_file in sql_files:
                path = os.path.join(migrations_dir, sql_file)
                with open(path, "r", encoding="utf-8") as f:
                    sql = f.read()
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
                print(f"SUCCESS: Migration '{sql_file}' applied successfully.")
        print("All migrations completed. Database schema is up-to-date.")
    except psycopg.OperationalError as exc:
        print(
            f"ERROR: Could not connect to PostgreSQL ({type(exc).__name__}).\n"
            "Please verify that PostgreSQL 18 is running on 127.0.0.1:5432, "
            "database 'gisdb' exists, and credentials in .env are correct.",
            file=sys.stderr,
        )
        sys.exit(1)
    except psycopg.Error as exc:
        print(f"ERROR applying migration: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_migration()
