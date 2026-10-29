"""
RoadWise AI - Production/Pilot WSGI Server Runner (Stage 7).
Runs Waitress multithreaded WSGI server for Windows pilot deployments
with debug mode strictly disabled and credentials masked.

Usage:
  python run_pilot.py
  python run_pilot.py --host 127.0.0.1 --port 5000 --threads 4
"""
import argparse
import logging
import os
import sys

from app import app, configure_database, initialize_database
import backup_restore


def main():
    parser = argparse.ArgumentParser(description="RoadWise AI - Production WSGI Pilot Runner")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="Binding host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)), help="Binding port (default: 5000)")
    parser.add_argument("--threads", type=int, default=int(os.environ.get("WAITRESS_THREADS", 4)), help="Worker threads (default: 4)")
    args = parser.parse_args()

    # Enforce pilot production settings
    os.environ["FLASK_DEBUG"] = "0"
    app.config["DEBUG"] = False
    app.config["TESTING"] = False

    # Configure and verify database connection
    try:
        dsn = configure_database()
        initialize_database()
        masked_dsn = backup_restore.mask_url(dsn)
        print("=" * 65)
        print("RoadWise AI: Pilot Configuration Loaded")
        print(f"Database:    {masked_dsn}")
        print(f"Debug Mode:  DISABLED")
        print(f"Host/Port:   http://{args.host}:{args.port}")
        print("=" * 65)
    except Exception as exc:
        print(f"FATAL: Database initialization failed ({type(exc).__name__}).", file=sys.stderr)
        print("Please ensure PostgreSQL 18 is running and .env is configured properly.", file=sys.stderr)
        sys.exit(1)

    # Launch via Waitress WSGI or production-safe fallback
    try:
        import waitress
        print(f"Starting Waitress WSGI server ({args.threads} worker threads)...")
        waitress.serve(app, host=args.host, port=args.port, threads=args.threads)
    except ImportError:
        print("NOTICE: Waitress WSGI server is not installed in the current environment.")
        print("Running via local WSGI runner with debug=False. For full multi-threading, install waitress:")
        print("    pip install waitress")
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()

