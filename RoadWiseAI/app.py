from io import BytesIO
import os
import uuid
import urllib.parse
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, jsonify, Response, session, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from PIL import Image
from dotenv import load_dotenv, find_dotenv
import psycopg
from psycopg.rows import dict_row
from detector import get_detector
import analytics as analytics_module
import location_service
import export_service
import work_order_service
import auth_service
import progress_update_service
from analytics import (
    filtered_reports,
    summary_counts,
    hotspot_clusters,
    compute_all_priorities,
    external_data_status,
    PRIORITY_WEIGHTS,
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB limit
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Stage 8: Protected work-order progress update attachments (outside public static)
app.config["ATTACHMENT_FOLDER"] = os.path.join(app.root_path, "uploads", "work_orders")
os.makedirs(app.config["ATTACHMENT_FOLDER"], exist_ok=True)

# Stage 7: Pilot Security, Signing Secret & Session Cookie Settings
raw_secret = os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SECRET_KEY")
if not raw_secret or "YOUR_" in raw_secret:
    raw_secret = os.environ.setdefault("FLASK_SECRET_KEY", "roadwise_pilot_" + uuid.uuid4().hex)
app.config["SECRET_KEY"] = raw_secret
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "0").lower() in ("1", "true")
app.config["LOGIN_DISABLED"] = False

# Allowed options matching frontend form
ALLOWED_DAMAGE_TYPES = {
    "Pothole",
    "Longitudinal crack",
    "Transverse crack",
    "Alligator crack",
    "Surface deterioration"
}

ALLOWED_SEVERITIES = {"Critical", "Moderate", "Minor"}

# Report details and PostGIS geometry are stored in PostgreSQL.
# The connection string is loaded securely from DATABASE_URL in .env.
DATABASE_DSN = None


def mask_database_url(url):
    """Safely mask password in PostgreSQL connection URL for logging and UI display."""
    if not url or "@" not in url:
        return url
    prefix, rest = url.split("://", 1)
    last_at = rest.rfind("@")
    userinfo = rest[:last_at]
    hostinfo = rest[last_at + 1:]
    if ":" in userinfo:
        user, _ = userinfo.split(":", 1)
        return f"{prefix}://{user}:***@{hostinfo}"
    return f"{prefix}://***@{hostinfo}"


def login_required(f):
    """Decorator requiring an authenticated staff or admin session."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if app.config.get("LOGIN_DISABLED"):
            return f(*args, **kwargs)
        if not session.get("user_id"):
            return jsonify({
                "status": "error",
                "error": "Authentication required. Please log in as municipal staff.",
            }), 401
        return f(*args, **kwargs)
    return decorated_function


def role_required(allowed_roles):
    """Decorator requiring a specific user role."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if app.config.get("LOGIN_DISABLED"):
                return f(*args, **kwargs)
            if not session.get("user_id"):
                return jsonify({
                    "status": "error",
                    "error": "Authentication required. Please log in as municipal staff.",
                }), 401
            user_role = session.get("role", "staff")
            if user_role not in allowed_roles:
                return jsonify({
                    "status": "error",
                    "error": f"Forbidden: Insufficient privileges. Required role: {', '.join(allowed_roles)}.",
                }), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


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


def configure_database():
    """Load DATABASE_URL from .env or environment configuration."""
    global DATABASE_DSN

    # Load .env from project root or current directory, skipping placeholder files
    load_dotenv(find_dotenv(usecwd=True))
    for env_path in [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    ]:
        if os.path.isfile(env_path):
            current_val = os.environ.get("DATABASE_URL") or ""
            should_override = "YOUR_" in current_val or not current_val
            load_dotenv(env_path, override=should_override)

    raw_url = os.environ.get("DATABASE_URL") or os.environ.get("ROADWISE_DATABASE_URL")
    if not raw_url or "YOUR_" in raw_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Please define DATABASE_URL in your .env file "
            "(e.g. DATABASE_URL=postgresql://postgres:<password>@127.0.0.1:5432/gisdb)."
        )
    DATABASE_DSN = normalize_database_url(raw_url)
    return DATABASE_DSN


def get_database_connection():
    global DATABASE_DSN
    if not DATABASE_DSN:
        configure_database()
    return psycopg.connect(DATABASE_DSN, row_factory=dict_row)


def initialize_database():
    """Ensure PostGIS extension and all database tables are ready."""
    with get_database_connection() as connection:
        migrations_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
        if os.path.isdir(migrations_dir):
            for fname in sorted(os.listdir(migrations_dir)):
                if fname.endswith(".sql"):
                    fpath = os.path.join(migrations_dir, fname)
                    with open(fpath, "r", encoding="utf-8") as f:
                        connection.execute(f.read())
        connection.commit()


def serialize_report(row):
    """Convert a database row to the JSON shape expected by the map UI."""
    latitude = round(float(row["latitude"]), 6)
    longitude = round(float(row["longitude"]), 6)
    created_at = row["created_at"]
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at_text = created_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    else:
        created_at_text = str(created_at)

    priority_score = None
    if row.get("priority_score") is not None:
        try:
            priority_score = float(row["priority_score"])
        except (ValueError, TypeError):
            priority_score = None

    work_order = None
    if row.get("work_order_code"):
        work_order = {
            "id": row.get("work_order_id"),
            "code": row.get("work_order_code"),
            "status": row.get("work_order_status"),
        }

    return {
        "id": row["id"],
        "road_name": row["road_name"],
        "damage_type": row["damage_type"],
        "severity": row["severity"],
        "description": row["description"],
        "latitude": latitude,
        "longitude": longitude,
        "photo_url": row["photo_url"],
        "created_at": created_at_text,
        "geom_geojson": {
            "type": "Point",
            "coordinates": [longitude, latitude],
        },
        "geom_wkt": f"SRID=4326;POINT({longitude} {latitude})",
        "storage_mode": "postgresql_postgis",
        "priority_tier": row.get("priority_tier"),
        "priority_score": priority_score,
        "explanation": row.get("explanation"),
        "work_order": work_order,
    }


def list_reports(date_from=None, date_to=None, damage_type=None, severity=None):
    with get_database_connection() as connection:
        rows = filtered_reports(
            connection,
            date_from=date_from,
            date_to=date_to,
            damage_type=damage_type,
            severity=severity,
        )
    return [serialize_report(row) for row in rows]


def save_report(road_name, damage_type, severity, description, photo_url, lat, lng):
    with get_database_connection() as connection:
        row = connection.execute(
            """
            INSERT INTO road_damage_reports
                (road_name, damage_type, severity, description, photo_url, geom)
            VALUES
                (%s, %s, %s, %s, %s,
                 ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            RETURNING id, road_name, damage_type, severity, description, photo_url,
                      ST_Y(geom) AS latitude, ST_X(geom) AS longitude, created_at
            """,
            (road_name, damage_type, severity, description, photo_url, lng, lat),
        ).fetchone()

    return serialize_report(row)


@app.errorhandler(413)
@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(error):
    return jsonify({
        "error": "The uploaded photograph exceeds the 10 MB size limit."
    }), 413


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/health")
def health():
    try:
        with get_database_connection() as connection:
            report_count = connection.execute(
                "SELECT COUNT(*) AS total FROM road_damage_reports"
            ).fetchone()["total"]
            postgis_version = connection.execute(
                "SELECT PostGIS_Full_Version() AS version"
            ).fetchone()["version"]
    except (psycopg.Error, RuntimeError):
        app.logger.warning("Database health check failed")
        return jsonify({
            "status": "error",
            "project": "RoadWise AI",
            "study_area": "Thiruvananthapuram, Kerala",
            "storage": {
                "mode": "postgresql_postgis",
                "postgis_ready": False,
                "report_count": None,
            },
            "error": "PostgreSQL/PostGIS is not connected. Check the app's database settings.",
        }), 503

    uploads_writable = os.access(app.config["UPLOAD_FOLDER"], os.W_OK)
    return jsonify({
        "status": "ok",
        "project": "RoadWise AI",
        "study_area": "Thiruvananthapuram, Kerala",
        "storage": {
            "mode": "postgresql_postgis",
            "postgis_ready": bool(postgis_version),
            "postgis_version": postgis_version,
            "report_count": report_count,
        },
        "pilot": {
            "persistent_uploads": uploads_writable,
            "debug_mode": bool(app.config.get("DEBUG")),
            "auth_configured": True,
        }
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stage 7 Pilot Authentication Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    """Authenticate municipal staff or supervisor credentials."""
    payload = request.get_json(silent=True) or {}
    username = payload.get("username")
    password = payload.get("password")

    if not username or not password:
        return jsonify({"status": "error", "error": "Username and password are required."}), 400

    try:
        with get_database_connection() as conn:
            user, err = auth_service.authenticate_user(conn, username, password)
            if err:
                return jsonify({"status": "error", "error": err}), 401

            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["full_name"] = user.get("full_name")

            return jsonify({
                "status": "success",
                "message": f"Welcome, {user['username']}.",
                "user": user,
            }), 200

    except (psycopg.Error, RuntimeError) as exc:
        app.logger.warning("Database authentication error: %s", type(exc).__name__)
        return jsonify({"status": "error", "error": "Authentication service unavailable."}), 503


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    """End the current pilot staff session."""
    session.clear()
    return jsonify({"status": "success", "message": "Logged out successfully."}), 200


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    """Return the current user's session profile (safe, no secrets)."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"authenticated": False, "user": None}), 200

    return jsonify({
        "authenticated": True,
        "user": {
            "id": user_id,
            "username": session.get("username"),
            "role": session.get("role", "staff"),
            "full_name": session.get("full_name"),
        }
    }), 200


def dms_to_decimal(coords, ref):
    """Convert EXIF degrees/minutes/seconds to decimal degrees."""
    if not coords or len(coords) < 3:
        raise ValueError("Incomplete coordinate components")

    parts = []
    for part in coords[:3]:
        parts.append(float(part))

    deg, minute, sec = parts
    decimal = deg + (minute / 60.0) + (sec / 3600.0)

    if isinstance(ref, bytes):
        ref = ref.decode("latin1", errors="ignore")
    ref = str(ref).strip().upper()

    if ref in ("S", "W"):
        decimal = -decimal
    elif ref not in ("N", "E"):
        raise ValueError(f"Unknown reference direction: {ref}")

    return decimal


def validate_and_save_photo(uploaded_file):
    """
    Validate that an uploaded file is a genuine JPEG under 10MB
    and save it securely into the static/uploads folder.
    Returns (photo_url, error_message).
    """
    if not uploaded_file or not uploaded_file.filename:
        return None, None

    filename = uploaded_file.filename.strip()
    if not filename.lower().endswith((".jpg", ".jpeg")):
        return None, "Photograph must be a JPEG image (.jpg or .jpeg)."

    try:
        image_bytes = uploaded_file.read()
        if not image_bytes:
            return None, "The uploaded photograph file is empty."

        # Verify image structure
        try:
            image = Image.open(BytesIO(image_bytes))
            if image.format not in ("JPEG", "MPO", "WEBP", "PNG"):
                return None, "The uploaded file is not a supported photograph format."
            image.verify()
        except Exception:
            return None, "The uploaded file could not be decoded as a valid image."

        # Reopen and convert to RGB to save as standardized JPEG
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        unique_name = (
            f"report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_"
            f"{uuid.uuid4().hex[:8]}.jpg"
        )
        saved_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
        image.save(saved_path, format="JPEG", quality=85)

        return f"/static/uploads/{unique_name}", None

    except Exception as exc:
        app.logger.exception("Error saving uploaded report photograph: %s", exc)
        return None, "Could not process the uploaded photograph."


@app.route("/api/geotag", methods=["POST"])
def extract_geotag():
    if "photo" not in request.files:
        return jsonify({"error": "Please select a photograph."}), 400

    uploaded = request.files["photo"]

    if not uploaded or not uploaded.filename:
        return jsonify({"error": "Please select a photograph."}), 400

    if not uploaded.filename.lower().endswith((".jpg", ".jpeg")):
        return jsonify({
            "error": "Please upload a JPEG photograph (.jpg or .jpeg)."
        }), 400

    try:
        image_bytes = uploaded.read()

        if not image_bytes:
            return jsonify({"error": "The uploaded file is empty."}), 400

        # Verify that the uploaded content is a valid JPEG
        try:
            image = Image.open(BytesIO(image_bytes))
            if image.format not in ("JPEG", "MPO"):
                return jsonify({
                    "error": "The uploaded file is not a valid JPEG photograph."
                }), 400
            image.verify()
        except Exception:
            return jsonify({
                "error": "The uploaded file could not be decoded as a valid JPEG image."
            }), 400

        # Reopen image after verify() to inspect EXIF metadata
        image = Image.open(BytesIO(image_bytes))
        exif = image.getexif()

        gps = {}
        if exif:
            gps = exif.get_ifd(34853) or {}

        # Fallback to _getexif() if IFD 34853 was empty
        if not gps and hasattr(image, "_getexif"):
            raw_exif = image._getexif() or {}
            gps = raw_exif.get(34853) or {}

        # Multi-source location & GPS extraction
        loc_res = location_service.extract_location_from_photo(image_bytes, filename=uploaded.filename)
        lat = loc_res.get("latitude")
        lng = loc_res.get("longitude")
        road_name = loc_res.get("road_name")

        if lat is None or lng is None:
            return jsonify({
                "error": (
                    "No GPS metadata found in this photo. Use an original "
                    "geotagged JPEG or select the location manually on the map."
                )
            }), 422

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
            return jsonify({
                "error": "The GPS coordinates are outside the valid geographical range."
            }), 422

        # Optional spatial map analysis around extracted coordinates
        map_analysis = None
        try:
            with get_database_connection() as conn:
                map_analysis = location_service.analyze_map_location(conn, lat, lng)
        except Exception as exc:
            app.logger.debug("Map analysis during geotag skipped: %s", exc)

        return jsonify({
            "latitude": round(lat, 6),
            "longitude": round(lng, 6),
            "road_name": road_name,
            "source": loc_res.get("source"),
            "map_analysis": map_analysis,
            "message": "GPS coordinates and location extracted successfully."
        }), 200

    except Exception as error:
        app.logger.exception("Failed to process uploaded photo: %s", error)
        return jsonify({
            "error": "Could not process this image. Try another JPEG photograph."
        }), 400


@app.route("/api/detect", methods=["POST"])
def detect_damage():
    """
    AI-assisted road-damage detection & location extraction endpoint.
    Accepts an uploaded road photograph and returns:
    - suggested damage type, confidence score, bounding boxes
    - automatically extracted road name / location and GPS coordinates
    - spatial map analysis context for instant decision support
    """
    if "photo" not in request.files:
        return jsonify({"status": "error", "error": "Please provide a photograph for detection."}), 400

    uploaded = request.files["photo"]
    if not uploaded or not uploaded.filename:
        return jsonify({"status": "error", "error": "Please select a photograph."}), 400

    if not uploaded.filename.lower().endswith((".jpg", ".jpeg")):
        return jsonify({"status": "error", "error": "Detection requires a JPEG photograph (.jpg or .jpeg)."}), 400

    try:
        image_bytes = uploaded.read()
        if not image_bytes:
            return jsonify({"status": "error", "error": "The uploaded photograph file is empty."}), 400

        try:
            image = Image.open(BytesIO(image_bytes))
            if image.format not in ("JPEG", "MPO", "WEBP", "PNG"):
                return jsonify({"status": "error", "error": "The uploaded photograph format is not supported."}), 400
            image.verify()
        except Exception:
            return jsonify({"status": "error", "error": "Could not decode photograph as a valid image."}), 400

        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        detector = get_detector()

        # Parse optional confidence threshold
        conf_param = request.form.get("conf") or request.args.get("conf")
        conf_val = 0.08
        if conf_param:
            try:
                parsed_conf = float(conf_param)
                if 0.01 <= parsed_conf <= 1.0:
                    conf_val = parsed_conf
            except (ValueError, TypeError):
                pass

        result = detector.predict(image, conf_threshold=conf_val)

        # Extract location and road name from photo
        loc_res = location_service.extract_location_from_photo(image_bytes, filename=uploaded.filename)
        lat = loc_res.get("latitude")
        lng = loc_res.get("longitude")
        road_name = loc_res.get("road_name")

        # Perform map analysis if coordinates are available
        map_analysis = None
        if lat is not None and lng is not None:
            try:
                with get_database_connection() as conn:
                    map_analysis = location_service.analyze_map_location(
                        conn,
                        lat,
                        lng,
                        damage_type=result.get("suggested_damage_type") or "Surface deterioration",
                        severity=result.get("suggested_severity") or "Moderate",
                    )
            except Exception as exc:
                app.logger.debug("Map analysis during detect skipped: %s", exc)

        if not result.get("success"):
            return jsonify({
                "status": "info",
                "message": result.get("error", "AI detector model is not available. Please select damage type manually."),
                "detections": [],
                "suggested_damage_type": None,
                "suggested_severity": None,
                "severity_reason": None,
                "confidence": 0.0,
                "count": 0,
                "road_name": road_name,
                "latitude": round(lat, 6) if lat is not None else None,
                "longitude": round(lng, 6) if lng is not None else None,
                "location_source": loc_res.get("source"),
                "map_analysis": map_analysis,
            }), 200

        return jsonify({
            "status": "success",
            "detections": result["detections"],
            "suggested_damage_type": result["suggested_damage_type"],
            "suggested_severity": result.get("suggested_severity"),
            "severity_reason": result.get("severity_reason"),
            "confidence": result["confidence"],
            "raw_confidence": result.get("raw_confidence", result["confidence"]),
            "count": result["count"],
            "threshold_used": result.get("threshold_used", conf_val),
            "highest_candidate": result.get("highest_candidate"),
            "road_name": road_name,
            "latitude": round(lat, 6) if lat is not None else None,
            "longitude": round(lng, 6) if lng is not None else None,
            "location_source": loc_res.get("source"),
            "map_analysis": map_analysis,
        }), 200

    except Exception as exc:
        app.logger.exception("Failed to run detection on uploaded photo: %s", exc)
        return jsonify({"status": "error", "error": "Could not process detection on this image."}), 500


@app.route("/api/map/analyze-location", methods=["GET"])
def get_map_location_analysis():
    """
    Analyze map context around given coordinates:
    road corridor, road classification, traffic volume, nearby cluster proximity,
    estimated maintenance priority score, and engineering recommendation.
    """
    lat_raw = request.args.get("lat")
    lng_raw = request.args.get("lng")
    damage_type = request.args.get("damage_type") or "Surface deterioration"
    severity = request.args.get("severity") or "Moderate"

    if not lat_raw or not lng_raw:
        return jsonify({"status": "error", "error": "lat and lng query parameters are required."}), 400

    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "error": "Invalid coordinates format."}), 400

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return jsonify({"status": "error", "error": "Coordinates outside valid geographical limits."}), 400

    geo_info = location_service.reverse_geocode_nominatim(lat, lng)

    try:
        with get_database_connection() as conn:
            analysis = location_service.analyze_map_location(
                conn, lat, lng, damage_type=damage_type, severity=severity
            )
            if geo_info and geo_info.get("formatted_name"):
                analysis["display_road_name"] = geo_info["formatted_name"]
            if geo_info and geo_info.get("road"):
                analysis["street_name"] = geo_info["road"]
    except Exception as exc:
        app.logger.exception("Map analysis failed: %s", exc)
        return jsonify({"status": "error", "error": "Failed to analyze map location."}), 500

    return jsonify({"status": "ok", "analysis": analysis}), 200


@app.route("/api/reports", methods=["GET", "POST"])
def handle_reports():
    """Retrieve existing damage reports (with optional filters) or submit a new report."""
    if request.method == "GET":
        date_from = (request.args.get("date_from") or "").strip() or None
        date_to = (request.args.get("date_to") or "").strip() or None
        damage_type = (request.args.get("damage_type") or "").strip() or None
        severity = (request.args.get("severity") or "").strip() or None

        if date_from:
            try:
                datetime.strptime(date_from, "%Y-%m-%d")
            except ValueError:
                return jsonify({"status": "error", "error": "Invalid date_from format. Expected YYYY-MM-DD."}), 400

        if date_to:
            try:
                datetime.strptime(date_to, "%Y-%m-%d")
            except ValueError:
                return jsonify({"status": "error", "error": "Invalid date_to format. Expected YYYY-MM-DD."}), 400

        if damage_type and damage_type not in ALLOWED_DAMAGE_TYPES:
            return jsonify({
                "status": "error",
                "error": f"Invalid damage_type. Allowed: {', '.join(sorted(ALLOWED_DAMAGE_TYPES))}"
            }), 400

        if severity and severity not in ALLOWED_SEVERITIES:
            return jsonify({
                "status": "error",
                "error": f"Invalid severity. Allowed: {', '.join(sorted(ALLOWED_SEVERITIES))}"
            }), 400

        try:
            if any((date_from, date_to, damage_type, severity)):
                reports = list_reports(
                    date_from=date_from,
                    date_to=date_to,
                    damage_type=damage_type,
                    severity=severity,
                )
            else:
                reports = list_reports()
        except (psycopg.Error, RuntimeError):
            app.logger.exception("Could not read reports from PostgreSQL/PostGIS")
            return jsonify({
                "status": "error",
                "error": "Could not load reports from PostgreSQL/PostGIS. Check the database connection.",
            }), 503

        return jsonify({
            "status": "ok",
            "count": len(reports),
            "reports": reports,
            "filters_applied": {
                "date_from": date_from,
                "date_to": date_to,
                "damage_type": damage_type,
                "severity": severity,
            },
            "storage_backend": "postgresql_postgis",
            "storage_notice": "Report details and locations are stored in PostgreSQL/PostGIS.",
        }), 200

    # POST: Support both JSON bodies and multipart/form-data
    if request.is_json:
        payload = request.get_json() or {}
        road_name = str(payload.get("road_name") or "").strip()
        damage_type = str(payload.get("damage_type") or "").strip()
        severity = str(payload.get("severity") or "").strip()
        description = str(payload.get("description") or "").strip()
        lat_raw = payload.get("latitude")
        lng_raw = payload.get("longitude")
        photo_file = None
    else:
        road_name = (request.form.get("road_name") or "").strip()
        damage_type = (request.form.get("damage_type") or "").strip()
        severity = (request.form.get("severity") or "").strip()
        description = (request.form.get("description") or "").strip()
        lat_raw = request.form.get("latitude")
        lng_raw = request.form.get("longitude")
        photo_file = request.files.get("photo")

    # Validation: Road name
    if not road_name:
        return jsonify({"status": "error", "error": "Road name is required."}), 400
    if len(road_name) > 200:
        return jsonify({"status": "error", "error": "Road name must be 200 characters or fewer."}), 400

    # Validation: Damage type
    if damage_type not in ALLOWED_DAMAGE_TYPES:
        return jsonify({
            "status": "error",
            "error": f"Invalid damage type. Must be one of: {', '.join(sorted(ALLOWED_DAMAGE_TYPES))}."
        }), 400

    # Validation: Severity
    if severity not in ALLOWED_SEVERITIES:
        return jsonify({
            "status": "error",
            "error": f"Invalid severity level. Must be one of: {', '.join(sorted(ALLOWED_SEVERITIES))}."
        }), 400

    # Validation: Description length
    if len(description) > 1000:
        return jsonify({"status": "error", "error": "Description must be 1000 characters or fewer."}), 400

    # Validation: Coordinates (must not be empty, must be valid range)
    if lat_raw is None or lng_raw is None or str(lat_raw).strip() == "" or str(lng_raw).strip() == "":
        return jsonify({
            "status": "error",
            "error": "Incident coordinates are required. Select a location on the map or extract GPS from a photo."
        }), 400

    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "error": "Latitude and longitude must be valid decimal numbers."}), 400

    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return jsonify({"status": "error", "error": "Coordinates are outside valid geographical limits."}), 400

    # Validation & processing: Attached photograph (optional)
    photo_url = None
    if photo_file and photo_file.filename:
        photo_url, photo_err = validate_and_save_photo(photo_file)
        if photo_err:
            return jsonify({"status": "error", "error": photo_err}), 400

    try:
        report = save_report(
            road_name=road_name,
            damage_type=damage_type,
            severity=severity,
            description=description or None,
            photo_url=photo_url,
            lat=lat,
            lng=lng,
        )
        try:
            with get_database_connection() as conn:
                score_data = analytics_module.compute_priority_score(conn, report)
                analytics_module.upsert_priority_score(conn, score_data)
                conn.commit()
                report["priority_tier"] = score_data.get("priority_tier")
                report["priority_score"] = score_data.get("priority_score")
                report["explanation"] = score_data.get("explanation")
        except Exception as exc:
            app.logger.warning("Could not calculate initial priority: %s", exc)
    except (psycopg.Error, RuntimeError) as exc:
        app.logger.error("Could not save report to PostgreSQL/PostGIS: %s", type(exc).__name__)
        if photo_url:
            saved_path = os.path.join(
                app.config["UPLOAD_FOLDER"], os.path.basename(photo_url)
            )
            try:
                os.remove(saved_path)
            except OSError:
                app.logger.warning("Could not remove an unlinked report photo: %s", saved_path)
        return jsonify({
            "status": "error",
            "error": "Could not save the report to PostgreSQL/PostGIS. Check the database connection and try again.",
        }), 503

    return jsonify({
        "status": "success",
        "message": f"Report #{report['id']} added successfully.",
        "report": report
    }), 201


# ─────────────────────────────────────────────────────────────
# Stage 5: GIS Analytics & Maintenance Prioritisation Endpoints
# ─────────────────────────────────────────────────────────────

@app.route("/api/analytics/summary", methods=["GET"])
def get_analytics_summary():
    """
    Return aggregated summary counts for the current filter criteria:
    total reports, counts by damage type, counts by severity, and counts by priority tier.
    """
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    damage_type = (request.args.get("damage_type") or "").strip() or None
    severity = (request.args.get("severity") or "").strip() or None

    if date_from:
        try:
            datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            return jsonify({"status": "error", "error": "Invalid date_from format. Expected YYYY-MM-DD."}), 400

    if date_to:
        try:
            datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            return jsonify({"status": "error", "error": "Invalid date_to format. Expected YYYY-MM-DD."}), 400

    if damage_type and damage_type not in ALLOWED_DAMAGE_TYPES:
        return jsonify({
            "status": "error",
            "error": f"Invalid damage_type. Allowed: {', '.join(sorted(ALLOWED_DAMAGE_TYPES))}"
        }), 400

    if severity and severity not in ALLOWED_SEVERITIES:
        return jsonify({
            "status": "error",
            "error": f"Invalid severity. Allowed: {', '.join(sorted(ALLOWED_SEVERITIES))}"
        }), 400

    try:
        with get_database_connection() as conn:
            counts = summary_counts(
                conn,
                date_from=date_from,
                date_to=date_to,
                damage_type=damage_type,
                severity=severity,
            )
    except (psycopg.Error, RuntimeError):
        app.logger.exception("Could not calculate summary counts from PostgreSQL/PostGIS")
        return jsonify({
            "status": "error",
            "error": "Could not calculate summary counts from database.",
        }), 503

    return jsonify({
        "status": "ok",
        "summary": counts,
        "filters_applied": {
            "date_from": date_from,
            "date_to": date_to,
            "damage_type": damage_type,
            "severity": severity,
        },
    }), 200


@app.route("/api/analytics/hotspots", methods=["GET"])
def get_hotspot_clusters():
    """
    Cluster reports using PostGIS ST_ClusterDBSCAN in projected UTM 43N (SRID 32643).
    Returns density clusters when report count meets the minimum threshold.
    """
    min_reports_raw = request.args.get("min_reports") or "3"
    eps_raw = request.args.get("cluster_eps_m") or "300.0"

    try:
        min_reports = max(2, int(min_reports_raw))
    except (ValueError, TypeError):
        min_reports = 3

    try:
        eps_m = max(50.0, float(eps_raw))
    except (ValueError, TypeError):
        eps_m = 300.0

    try:
        with get_database_connection() as conn:
            data = hotspot_clusters(conn, min_reports=min_reports, cluster_eps_m=eps_m)
    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Could not calculate hotspot clusters: %s", exc)
        return jsonify({
            "status": "error",
            "error": "Could not calculate hotspot clusters from database.",
        }), 503

    return jsonify({"status": "ok", "data": data}), 200


@app.route("/api/analytics/priority/compute", methods=["POST"])
@login_required
def recalculate_priorities():
    """
    Recompute transparent maintenance priority scores for all reports
    incorporating road network, traffic counts, junction density, and repeats.
    """
    try:
        with get_database_connection() as conn:
            scored = compute_all_priorities(conn)
            conn.commit()
    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Could not recompute priority scores: %s", exc)
        return jsonify({
            "status": "error",
            "error": "Could not recompute priority scores from database.",
        }), 503

    return jsonify({
        "status": "ok",
        "message": f"Successfully calculated priority scores for {len(scored)} reports.",
        "count": len(scored),
        "scores": scored,
    }), 200


@app.route("/api/analytics/external-data-status", methods=["GET"])
def get_external_data_status():
    """Check availability of external GIS datasets: road_segments, junctions, traffic_counts."""
    try:
        with get_database_connection() as conn:
            status = external_data_status(conn)
    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Could not fetch external dataset status: %s", exc)
        return jsonify({
            "status": "error",
            "error": "Could not check external data status.",
        }), 503

    return jsonify({"status": "ok", "status_data": status}), 200


@app.route("/api/config", methods=["GET"])
def get_client_config():
    """
    Return safe public browser configuration.
    Never exposes DATABASE_URL, passwords, or backend secrets.
    """
    raw_key = (
        os.environ.get("GOOGLE_MAPS_API_KEY")
        or os.environ.get("ROADWISE_GOOGLE_MAPS_API_KEY")
        or ""
    ).strip()
    is_valid_key = bool(raw_key and "YOUR_" not in raw_key)

    return jsonify({
        "status": "ok",
        "google_maps_configured": is_valid_key,
        "google_maps_api_key": raw_key if is_valid_key else "",
        "min_hotspot_threshold": 3,
        "study_area": {
            "name": "Thiruvananthapuram, Kerala",
            "latitude": 8.5241,
            "longitude": 76.9366,
            "default_zoom": 13,
        },
        "priority_weights": PRIORITY_WEIGHTS,
    }), 200



# ─────────────────────────────────────────────────────────────
# Stage 6A: Maintenance Work-Order Export Endpoints
# ─────────────────────────────────────────────────────────────

EXPORT_FORMATS = {"pdf", "csv", "geojson"}
EXPORT_MAX_REPORT_IDS = 500   # guard against unreasonably large ID lists


def _parse_export_ids(raw: str) -> tuple[list[int] | None, str | None]:
    """
    Parse a comma-separated report_ids query string.
    Returns (list_of_ints, error_message_or_None).
    Returns (None, None) when raw is empty or absent.
    """
    if not raw or not raw.strip():
        return None, None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) > EXPORT_MAX_REPORT_IDS:
        return None, f"Too many report IDs (max {EXPORT_MAX_REPORT_IDS})."
    ids = []
    for p in parts:
        try:
            v = int(p)
            if v <= 0:
                return None, f"Invalid report ID: {p!r}. IDs must be positive integers."
            ids.append(v)
        except ValueError:
            return None, f"Invalid report ID: {p!r}. Only integers are accepted."
    return ids or None, None


def _validate_export_filters(date_from, date_to, damage_type, severity):
    """Reuse the same validation logic as the reports and analytics endpoints."""
    if date_from:
        try:
            datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            return "Invalid date_from format. Expected YYYY-MM-DD."
    if date_to:
        try:
            datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            return "Invalid date_to format. Expected YYYY-MM-DD."
    if damage_type and damage_type not in ALLOWED_DAMAGE_TYPES:
        return f"Invalid damage_type. Allowed: {', '.join(sorted(ALLOWED_DAMAGE_TYPES))}."
    if severity and severity not in ALLOWED_SEVERITIES:
        return f"Invalid severity. Allowed: {', '.join(sorted(ALLOWED_SEVERITIES))}."
    return None


@app.route("/api/export/<fmt>", methods=["GET"])
def export_work_order(fmt: str):
    """
    Stage 6A — Maintenance work-order export.

    Supported formats:
      GET /api/export/pdf
      GET /api/export/csv
      GET /api/export/geojson

    Query parameters (mutually exclusive modes):
      report_ids=1,2,3        — export specific report IDs (comma-separated)
      date_from / date_to     — date range filter  (YYYY-MM-DD)
      damage_type             — filter by damage type
      severity                — filter by severity

    When report_ids is given, filter params are ignored.
    Returns 200 with a file download on success.
    Returns 400 on invalid input, 404 when no reports match, 503 on DB error.

    This endpoint is read-only and never modifies any database row.
    Google Live Traffic data is never included.
    DATABASE_URL and server credentials are never exposed.
    """
    fmt = fmt.lower().strip()
    if fmt not in EXPORT_FORMATS:
        return jsonify({
            "status": "error",
            "error": f"Unsupported format '{fmt}'. Supported: pdf, csv, geojson.",
        }), 400

    # Parse report_ids
    raw_ids = (request.args.get("report_ids") or "").strip()
    report_ids, id_error = _parse_export_ids(raw_ids)
    if id_error:
        return jsonify({"status": "error", "error": id_error}), 400

    # Parse filter params (only used when report_ids is absent)
    date_from   = (request.args.get("date_from")   or "").strip() or None
    date_to     = (request.args.get("date_to")     or "").strip() or None
    damage_type = (request.args.get("damage_type") or "").strip() or None
    severity    = (request.args.get("severity")    or "").strip() or None

    if not report_ids:
        filter_error = _validate_export_filters(date_from, date_to, damage_type, severity)
        if filter_error:
            return jsonify({"status": "error", "error": filter_error}), 400

    # Fetch rows
    try:
        with get_database_connection() as conn:
            rows = export_service.fetch_export_rows(
                conn,
                report_ids=report_ids,
                date_from=date_from,
                date_to=date_to,
                damage_type=damage_type,
                severity=severity,
            )
    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Export DB error: %s", exc)
        return jsonify({
            "status": "error",
            "error": "Could not fetch reports from database. Check the connection.",
        }), 503

    if not rows:
        return jsonify({
            "status": "empty",
            "message": (
                "No reports matched the requested criteria. "
                "Adjust the filters or report IDs and try again."
            ),
        }), 404

    # Determine download filename prefix from applied filters
    if report_ids:
        prefix = f"roadwise_workorder_ids_{len(report_ids)}reports"
    else:
        parts = ["roadwise_workorder"]
        if damage_type:
            parts.append(damage_type.replace(" ", "_"))
        if severity:
            parts.append(severity)
        if date_from or date_to:
            parts.append(f"{date_from or 'start'}_{date_to or 'end'}")
        prefix = "_".join(parts)

    filename = export_service.safe_export_filename(prefix, fmt)

    try:
        if fmt == "csv":
            data = export_service.build_csv(rows)
            mime = "text/csv; charset=utf-8-sig"
        elif fmt == "geojson":
            data = export_service.build_geojson(rows)
            mime = "application/geo+json; charset=utf-8"
        else:  # pdf
            count = len(rows)
            title_parts = ["Maintenance Work Order"]
            if report_ids:
                title_parts.append(f"— {count} Selected Report(s)")
            else:
                if damage_type:
                    title_parts.append(f"— {damage_type}")
                if severity:
                    title_parts.append(f"/ {severity}")
            data = export_service.build_pdf(rows, title=" ".join(title_parts))
            mime = "application/pdf"
    except Exception as exc:
        app.logger.exception("Export generation failed (%s): %s", fmt, exc)
        return jsonify({
            "status": "error",
            "error": f"Could not generate {fmt.upper()} export. See server log.",
        }), 500

    return Response(
        data,
        status=200,
        mimetype=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data)),
            "X-Export-Format": fmt,
            "X-Export-Count": str(len(rows)),
        },
    )


# ─────────────────────────────────────────────────────────────
# Stage 6B: Maintenance Work-Order Tracking Endpoints
# ─────────────────────────────────────────────────────────────

@app.route("/api/work-orders", methods=["POST"])
@login_required
def create_work_orders():
    """
    Stage 6B — Create maintenance work orders for selected road-damage reports.
    Prevents duplicates (at most one work order per report).
    Clearly identifies which reports were already tracked.
    """
    payload = request.get_json(silent=True) or {}
    report_ids = payload.get("report_ids")
    if report_ids is None and "report_id" in payload:
        report_ids = [payload.get("report_id")]

    if not report_ids or not isinstance(report_ids, list):
        return jsonify({
            "status": "error",
            "error": "report_ids must be a non-empty list of positive integers.",
        }), 400

    target_date = payload.get("target_date")
    if target_date and str(target_date).strip():
        try:
            datetime.strptime(str(target_date).strip(), "%Y-%m-%d")
        except ValueError:
            return jsonify({
                "status": "error",
                "error": "Invalid target_date format. Expected YYYY-MM-DD.",
            }), 400

    assigned_to = payload.get("assigned_to")
    notes = payload.get("notes")

    try:
        with get_database_connection() as conn:
            result = work_order_service.create_work_orders(
                conn,
                report_ids=report_ids,
                assigned_to=assigned_to,
                target_date=target_date,
                notes=notes,
                created_by=session.get("username"),
            )
            conn.commit()
    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Failed to create work orders: %s", exc)
        return jsonify({
            "status": "error",
            "error": "Database error while creating work orders.",
        }), 503

    created_cnt = len(result["created"])
    tracked_cnt = len(result["already_tracked"])
    not_found_cnt = len(result["not_found"])

    msg_parts = []
    if created_cnt:
        msg_parts.append(f"Created {created_cnt} work order(s).")
    if tracked_cnt:
        tracked_codes = [f"{t['work_order_code']} ({t['status']})" for t in result["already_tracked"]]
        msg_parts.append(f"{tracked_cnt} report(s) already tracked: {', '.join(tracked_codes)}.")
    if not_found_cnt:
        msg_parts.append(f"{not_found_cnt} report ID(s) not found: {result['not_found']}.")

    status_code = 201 if created_cnt > 0 else 200
    return jsonify({
        "status": "success",
        "message": " ".join(msg_parts) or "No work orders created.",
        "created": result["created"],
        "already_tracked": result["already_tracked"],
        "not_found": result["not_found"],
    }), status_code


@app.route("/api/work-orders", methods=["GET"])
def list_work_orders():
    """
    Stage 6B — Maintenance work-order queue with filters.
    Query filters: status, priority, date_from, date_to, search.
    Returns list of work orders with linked report details and summary counts.
    """
    status = (request.args.get("status") or "").strip() or None
    priority = (request.args.get("priority") or "").strip() or None
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None
    search = (request.args.get("search") or "").strip() or None

    if date_from:
        try:
            datetime.strptime(date_from, "%Y-%m-%d")
        except ValueError:
            return jsonify({"status": "error", "error": "Invalid date_from format. Expected YYYY-MM-DD."}), 400

    if date_to:
        try:
            datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            return jsonify({"status": "error", "error": "Invalid date_to format. Expected YYYY-MM-DD."}), 400

    if priority and priority not in ("High", "Medium", "Low"):
        return jsonify({"status": "error", "error": "Invalid priority tier. Allowed: High, Medium, Low."}), 400

    try:
        with get_database_connection() as conn:
            wos = work_order_service.list_work_orders(
                conn,
                status=status,
                priority_tier=priority,
                date_from=date_from,
                date_to=date_to,
                search=search,
            )

            # Compute queue summary counts by status
            summary_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS n
                FROM work_orders
                GROUP BY status
                """
            ).fetchall()
            summary = {s: 0 for s in work_order_service.ALLOWED_STATUSES}
            for sr in summary_rows:
                summary[sr["status"]] = int(sr["n"])
            summary["total"] = sum(summary.values())

    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Failed to query work orders: %s", exc)
        return jsonify({
            "status": "error",
            "error": "Could not load work orders from database.",
        }), 503

    return jsonify({
        "status": "ok",
        "count": len(wos),
        "work_orders": wos,
        "summary": summary,
        "filters_applied": {
            "status": status,
            "priority": priority,
            "date_from": date_from,
            "date_to": date_to,
            "search": search,
        },
    }), 200


@app.route("/api/work-orders/<int:wo_id>", methods=["GET"])
def get_work_order_detail(wo_id: int):
    """
    Stage 6B — Get detailed work order including linked report and status audit history.
    """
    try:
        with get_database_connection() as conn:
            wo = work_order_service.get_work_order(conn, wo_id=wo_id)
    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Failed to fetch work order %s: %s", wo_id, exc)
        return jsonify({"status": "error", "error": "Database error loading work order."}), 503

    if not wo:
        return jsonify({"status": "error", "error": f"Work order #{wo_id} not found."}), 404

    return jsonify({"status": "ok", "work_order": wo}), 200


@app.route("/api/work-orders/<int:wo_id>", methods=["PATCH"])
@login_required
def update_work_order(wo_id: int):
    """
    Stage 6B — Update work order assignment, target date, notes, or transition status.
    Enforces status transition rules and records an audit history entry.
    """
    payload = request.get_json(silent=True) or {}

    new_status = payload.get("status")
    status_note = payload.get("status_note")
    assigned_to = payload.get("assigned_to")
    target_date = payload.get("target_date")
    notes = payload.get("notes")

    update_assignment = "assigned_to" in payload
    update_target_date = "target_date" in payload
    update_notes = "notes" in payload

    if update_target_date and target_date and str(target_date).strip():
        try:
            datetime.strptime(str(target_date).strip(), "%Y-%m-%d")
        except ValueError:
            return jsonify({"status": "error", "error": "Invalid target_date format. Expected YYYY-MM-DD."}), 400

    try:
        with get_database_connection() as conn:
            updated_wo, err = work_order_service.update_work_order(
                conn,
                wo_id=wo_id,
                new_status=new_status,
                status_note=status_note,
                assigned_to=assigned_to,
                target_date=target_date,
                notes=notes,
                update_assignment=update_assignment,
                update_target_date=update_target_date,
                update_notes=update_notes,
                changed_by=session.get("username"),
            )
            if err:
                status_code = 404 if "not found" in err.lower() else 400
                return jsonify({"status": "error", "error": err}), status_code

            conn.commit()

    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Failed to update work order %s: %s", wo_id, exc)
        return jsonify({"status": "error", "error": "Database error updating work order."}), 503

    return jsonify({
        "status": "success",
        "message": f"Work order {updated_wo.get('work_order_code', f'#{wo_id}')} updated successfully.",
        "work_order": updated_wo,
    }), 200


# ─────────────────────────────────────────────────────────────
# Stage 8: Public Road-Repair Progress Updates & Attachments
# ─────────────────────────────────────────────────────────────

@app.route("/api/work-orders/<int:wo_id>/updates", methods=["POST"])
@login_required
def post_progress_update(wo_id: int):
    """
    Officer/Supervisor action: Post an append-only progress update to a work order.
    Supports status transitions, completion summaries, and file attachments.
    """
    current_user = {
        "id": session.get("user_id"),
        "username": session.get("username"),
        "role": session.get("role", "staff"),
        "full_name": session.get("full_name"),
    }

    if request.is_json:
        payload = request.get_json(silent=True) or {}
    else:
        payload = request.form.to_dict()

    work_completed = payload.get("work_completed")
    status = payload.get("status")
    progress_pct = payload.get("progress_pct", 0)
    blockers = payload.get("blockers")
    next_actions = payload.get("next_actions")
    estimated_completion_date = payload.get("estimated_completion_date")
    internal_notes = payload.get("internal_notes")

    raw_pub = payload.get("is_published", False)
    is_published = str(raw_pub).lower() in ("1", "true", "yes")

    raw_corr = payload.get("is_correction", False)
    is_correction = str(raw_corr).lower() in ("1", "true", "yes")
    correction_for_id = payload.get("correction_for_id")
    if correction_for_id:
        try:
            correction_for_id = int(correction_for_id)
        except (ValueError, TypeError):
            correction_for_id = None

    try:
        with get_database_connection() as conn:
            update_record, err = progress_update_service.create_progress_update(
                conn,
                wo_id=wo_id,
                user=current_user,
                work_completed=work_completed,
                status=status,
                progress_pct=progress_pct,
                blockers=blockers,
                next_actions=next_actions,
                estimated_completion_date=estimated_completion_date,
                internal_notes=internal_notes,
                is_published=is_published,
                is_correction=is_correction,
                correction_for_id=correction_for_id,
            )
            if err:
                status_code = 404 if "not found" in err.lower() else (403 if "forbidden" in err.lower() else 400)
                return jsonify({"status": "error", "error": err}), status_code

            # Handle attachments if present
            uploaded_files = request.files.getlist("attachments")
            if not uploaded_files and "attachment" in request.files:
                uploaded_files = [request.files["attachment"]]

            captions = payload.get("caption") or payload.get("captions") or []
            if isinstance(captions, str):
                captions = [captions]

            saved_attachments = []
            for idx, uf in enumerate(uploaded_files):
                if not uf or not uf.filename:
                    continue
                file_bytes = uf.read()
                file_type, mime_type, file_err = progress_update_service.validate_attachment(file_bytes, uf.filename)
                if file_err:
                    conn.rollback()
                    return jsonify({"status": "error", "error": f"Attachment error: {file_err}"}), 400

                stored_name = progress_update_service.save_attachment_file(
                    app.config["ATTACHMENT_FOLDER"],
                    file_bytes,
                    uf.filename,
                    wo_id=wo_id,
                    update_id=update_record["id"],
                )
                caption = captions[idx] if idx < len(captions) else None
                att_rec = progress_update_service.add_attachment_record(
                    conn,
                    update_id=update_record["id"],
                    wo_id=wo_id,
                    file_type=file_type,
                    mime_type=mime_type,
                    original_filename=uf.filename,
                    stored_filename=stored_name,
                    file_size_bytes=len(file_bytes),
                    caption=caption,
                )
                saved_attachments.append(att_rec)

            conn.commit()
            update_record["attachments"] = saved_attachments

    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Failed to post progress update: %s", exc)
        return jsonify({"status": "error", "error": "Database error while posting progress update."}), 503

    return jsonify({
        "status": "success",
        "message": "Progress update posted successfully.",
        "update": update_record,
    }), 201


@app.route("/api/work-orders/<int:wo_id>/updates", methods=["GET"])
@login_required
def get_work_order_updates(wo_id: int):
    """Officer view: Retrieve all updates (published and drafts) for a work order."""
    try:
        with get_database_connection() as conn:
            updates = progress_update_service.list_work_order_updates(conn, wo_id)
    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Failed to fetch updates for work order %s: %s", wo_id, exc)
        return jsonify({"status": "error", "error": "Database error retrieving updates."}), 503

    return jsonify({
        "status": "ok",
        "work_order_id": wo_id,
        "count": len(updates),
        "updates": updates,
    }), 200


@app.route("/api/work-orders/updates/<int:update_id>/publish", methods=["POST"])
@login_required
def publish_update_endpoint(update_id: int):
    """Officer action: Explicitly publish an update to the public progress page."""
    current_user = {
        "id": session.get("user_id"),
        "username": session.get("username"),
        "role": session.get("role", "staff"),
    }
    try:
        with get_database_connection() as conn:
            res, err = progress_update_service.set_update_published(conn, update_id, is_published=True, user=current_user)
            if err:
                status_code = 404 if "not found" in err.lower() else (403 if "forbidden" in err.lower() else 400)
                return jsonify({"status": "error", "error": err}), status_code
            conn.commit()
    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Failed to publish update %s: %s", update_id, exc)
        return jsonify({"status": "error", "error": "Database error publishing update."}), 503

    return jsonify({"status": "success", "message": "Update published to public progress feed.", "update": res}), 200


@app.route("/api/work-orders/updates/<int:update_id>/unpublish", methods=["POST"])
@login_required
def unpublish_update_endpoint(update_id: int):
    """Officer action: Revert an update to unpublished/draft status."""
    current_user = {
        "id": session.get("user_id"),
        "username": session.get("username"),
        "role": session.get("role", "staff"),
    }
    try:
        with get_database_connection() as conn:
            res, err = progress_update_service.set_update_published(conn, update_id, is_published=False, user=current_user)
            if err:
                status_code = 404 if "not found" in err.lower() else (403 if "forbidden" in err.lower() else 400)
                return jsonify({"status": "error", "error": err}), status_code
            conn.commit()
    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Failed to unpublish update %s: %s", update_id, exc)
        return jsonify({"status": "error", "error": "Database error unpublishing update."}), 503

    return jsonify({"status": "success", "message": "Update unpublished.", "update": res}), 200


@app.route("/api/public/progress", methods=["GET"])
def get_public_progress():
    """
    Public API: List published road repair progress updates.
    Returns only explicitly published updates without sensitive internal notes or usernames.
    """
    status = request.args.get("status")
    search = request.args.get("search") or request.args.get("q")
    limit = request.args.get("limit", 20)
    offset = request.args.get("offset", 0)

    try:
        with get_database_connection() as conn:
            data = progress_update_service.list_public_progress_updates(
                conn,
                status=status,
                search=search,
                limit=limit,
                offset=offset,
            )
    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Failed to fetch public progress: %s", exc)
        return jsonify({"status": "error", "error": "Could not retrieve public progress data."}), 503

    return jsonify({"status": "ok", **data}), 200


@app.route("/api/work-orders/attachments/<int:attachment_id>", methods=["GET"])
def download_attachment(attachment_id: int):
    """
    Secure attachment download endpoint.
    Serves files only if:
      - The requesting user is an authenticated staff member/admin, OR
      - The parent progress update is published AND the attachment is approved.
    """
    is_authenticated = bool(session.get("user_id"))

    try:
        with get_database_connection() as conn:
            rec = progress_update_service.get_attachment_record(conn, attachment_id)
            if not rec:
                return jsonify({"status": "error", "error": "Attachment not found."}), 404

            if not is_authenticated:
                if not rec.get("is_published") or not rec.get("is_approved"):
                    return jsonify({
                        "status": "error",
                        "error": "Attachment is not publicly accessible.",
                    }), 403

            stored_filename = rec["stored_filename"]
            original_filename = rec["original_filename"]
            mime_type = rec["mime_type"]

            file_path = os.path.join(app.config["ATTACHMENT_FOLDER"], stored_filename)
            if not os.path.isfile(file_path):
                return jsonify({"status": "error", "error": "Attachment file not found on disk."}), 404

            return send_from_directory(
                app.config["ATTACHMENT_FOLDER"],
                stored_filename,
                download_name=original_filename,
                mimetype=mime_type,
                as_attachment=False,
            )

    except (psycopg.Error, RuntimeError) as exc:
        app.logger.exception("Failed to retrieve attachment %s: %s", attachment_id, exc)
        return jsonify({"status": "error", "error": "Database error loading attachment."}), 503


if __name__ == "__main__":

    try:
        configure_database()
        initialize_database()
        print("RoadWise AI: PostgreSQL 18 + PostGIS connected. road_damage_reports table ready.")
    except (psycopg.Error, RuntimeError) as error:
        app.logger.error("Could not initialize PostgreSQL/PostGIS: %s", type(error).__name__)
        raise SystemExit(
            "RoadWise AI could not connect to PostgreSQL/PostGIS.\n"
            "Please ensure:\n"
            "1. PostgreSQL 18 is running on 127.0.0.1:5432\n"
            "2. Database 'gisdb' exists and PostGIS extension is available\n"
            "3. DATABASE_URL is set in your .env file:\n"
            "   DATABASE_URL=postgresql://postgres:<password>@127.0.0.1:5432/gisdb"
        ) from None

    # Keep debug mode disabled by default for controlled pilot deployments
    is_debug = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
    app.run(host="127.0.0.1", port=5000, debug=is_debug, use_reloader=False)
