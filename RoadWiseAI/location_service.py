"""
RoadWise AI - Location Extraction & GIS Map Analysis Engine
Provides:
  - Location name extraction from image OCR (GPS Map Camera overlays) and EXIF GPS metadata
  - Reverse geocoding via OpenStreetMap Nominatim with local PostGIS corridor matching
  - Spatial map analysis around incident coordinates (road classification, traffic volume,
    hotspot cluster proximity, junction density, and estimated maintenance priority)
"""

import os
import re
import json
import logging
import subprocess
import tempfile
from io import BytesIO
from typing import Optional, Tuple, Dict, Any

import requests
from PIL import Image

logger = logging.getLogger("roadwise.location")

_PROJ_SRID = 32643  # UTM Zone 43N (covers Kerala)


def dms_to_decimal(dms, ref) -> float:
    """Convert Degrees-Minutes-Seconds tuples to decimal degrees."""
    degrees = float(dms[0])
    minutes = float(dms[1])
    seconds = float(dms[2])
    decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


def extract_gps_from_exif(image_bytes: bytes) -> Optional[Tuple[float, float]]:
    """Extract decimal (latitude, longitude) from JPEG EXIF GPS IFD."""
    try:
        img = Image.open(BytesIO(image_bytes))
        exif = img.getexif()
        gps = {}
        if exif:
            gps = exif.get_ifd(34853) or {}
        if not gps and hasattr(img, "_getexif"):
            raw_exif = img._getexif() or {}
            gps = raw_exif.get(34853) or {}

        if not gps:
            return None

        lat_ref = gps.get(1)
        lat_val = gps.get(2)
        lng_ref = gps.get(3)
        lng_val = gps.get(4)

        if not (lat_ref and lat_val and lng_ref and lng_val):
            return None

        lat = dms_to_decimal(lat_val, lat_ref)
        lng = dms_to_decimal(lng_val, lng_ref)

        if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0:
            return round(lat, 6), round(lng, 6)
    except Exception as exc:
        logger.debug("EXIF GPS parsing failed: %s", exc)
    return None


def extract_ocr_from_image(image_path: str) -> list[str]:
    """
    Extract text lines from image using Windows built-in WinRT OCR.
    Fast, offline, and native on Windows 10/11 without third-party binary dependencies.
    """
    if not os.path.isfile(image_path):
        return []

    # PowerShell script invoking Windows.Media.Ocr.OcrEngine
    ps_cmd = f'''
    $ErrorActionPreference = 'Stop'
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {{
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    }}[0]
    function Await($asyncOp, $type) {{
        $asTask = $asTaskGeneric.MakeGenericMethod($type)
        $netTask = $asTask.Invoke($null, @($asyncOp))
        $netTask.Wait(-1) | Out-Null
        return $netTask.Result
    }}
    [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrEngine, Windows.Foundation.UniversalApiContract, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null

    $fileOp = [Windows.Storage.StorageFile]::GetFileFromPathAsync('{image_path}')
    $file = Await $fileOp ([Windows.Storage.StorageFile])
    $streamOp = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
    $stream = Await $streamOp ([Windows.Storage.Streams.IRandomAccessStream])
    $decoderOp = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
    $decoder = Await $decoderOp ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmapOp = $decoder.GetSoftwareBitmapAsync()
    $bitmap = Await $bitmapOp ([Windows.Graphics.Imaging.SoftwareBitmap])

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if (-not $engine) {{
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
    }}
    $ocrResultOp = $engine.RecognizeAsync($bitmap)
    $ocrResult = Await $ocrResultOp ([Windows.Media.Ocr.OcrResult])

    $lines = @()
    foreach ($line in $ocrResult.Lines) {{
        $lines += $line.Text
    }}
    $lines | ConvertTo-Json
    '''

    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if res.returncode == 0 and res.stdout.strip():
            raw_lines = json.loads(res.stdout.strip())
            if isinstance(raw_lines, str):
                return [raw_lines]
            elif isinstance(raw_lines, list):
                return [str(l) for l in raw_lines]
    except Exception as exc:
        logger.debug("Windows OCR execution failed: %s", exc)
    return []


def parse_watermark_text(lines: list[str]) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """
    Parse road name and coordinates from watermark OCR lines.
    Handles GPS Map Camera formats and timestamp overlays.
    """
    if not lines:
        return None, None, None

    full_text = " \n ".join(lines)
    lat, lng = None, None

    # Parse coordinates: "Lat 8.576258, Long 76.867647"
    coord_match = re.search(
        r'Lat\s*[:=]?\s*([0-9]+\.[0-9]+)[,\s]+Long\s*[:=]?\s*([0-9]+\.[0-9]+)',
        full_text,
        re.I,
    )
    if coord_match:
        try:
            lat = round(float(coord_match.group(1)), 6)
            lng = round(float(coord_match.group(2)), 6)
        except ValueError:
            pass

    # Extract road name candidates: prioritize lines with road keywords
    road_candidates = []
    other_candidates = []
    for line in lines:
        cleaned = line.strip().strip(",").strip("-")
        # Ignore boilerplate lines
        if any(w in cleaned.lower() for w in [
            "captured by", "gps map", "gmt+", "monday", "tuesday", "wednesday",
            "thursday", "friday", "saturday", "sunday", "note :"
        ]):
            continue
        if re.search(r'Lat\s*[:=]?\s*[0-9]+', cleaned, re.I):
            continue
        # Skip generic region headers
        if any(cleaned.lower().startswith(p) for p in ["thiruvananthapuram, kerala", "kerala, india", "india"]):
            continue

        # Check for road keywords
        if any(w in cleaned.lower() for w in [
            "highway", "road", "street", "avenue", "lane", "bypass", "marg",
            "junction", "nh ", "sh ", "nh-", "sh-", "expressway", "kazhak"
        ]):
            road_candidates.append(cleaned)
        elif len(cleaned) >= 6:
            other_candidates.append(cleaned)

    road_name = None
    if road_candidates:
        chosen = road_candidates[0]
        # Clean any OCR glitch / non-ASCII replacement artifacts
        chosen = re.sub(r'[^\x20-\x7E]+', 'a', chosen)
        road_name = chosen.strip()
    elif other_candidates:
        chosen = other_candidates[0]
        chosen = re.sub(r'[^\x20-\x7E]+', 'a', chosen)
        road_name = chosen.strip()

    return road_name, lat, lng


def reverse_geocode_nominatim(lat: float, lng: float) -> Optional[dict]:
    """
    Reverse-geocode decimal coordinates via OpenStreetMap Nominatim.
    Returns address dict containing road, suburb, city, and formatted name.
    """
    try:
        headers = {"User-Agent": "RoadWiseAI-GIS-Platform/1.0 (GIS Infrastructure Maintenance)"}
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lng}&format=json"
        resp = requests.get(url, headers=headers, timeout=4)
        if resp.status_code == 200:
            data = resp.json()
            address = data.get("address", {})
            road = address.get("road")
            suburb = address.get("suburb") or address.get("neighbourhood") or address.get("hamlet")
            city = address.get("city") or address.get("town") or address.get("county") or "Thiruvananthapuram"

            parts = []
            if road:
                parts.append(road)
            if suburb and suburb not in parts:
                parts.append(suburb)
            if city and city not in parts:
                parts.append(city)

            return {
                "road": road,
                "suburb": suburb,
                "city": city,
                "formatted_name": ", ".join(parts) if parts else data.get("display_name"),
                "display_name": data.get("display_name"),
            }
    except Exception as exc:
        logger.debug("Nominatim reverse geocoding request failed: %s", exc)
    return None


def extract_location_from_photo(image_bytes: bytes, filename: str = "photo.jpg") -> dict:
    """
    Extract location name and coordinates from uploaded photograph using multi-source fusion:
    1. EXIF metadata for high-precision GPS.
    2. OCR of camera watermark cards (e.g. GPS Map Camera).
    3. Reverse geocoding via OpenStreetMap Nominatim.
    """
    lat, lng = None, None
    road_name = None
    source = "unknown"

    # 1. Try EXIF GPS first
    exif_coords = extract_gps_from_exif(image_bytes)
    if exif_coords:
        lat, lng = exif_coords
        source = "exif_gps"

    # 2. Try OCR extraction (handles GPS Map Camera watermark cards)
    ocr_lines = []
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("wb", suffix=".jpg", delete=False) as tf:
            tf.write(image_bytes)
            temp_path = tf.name

        ocr_lines = extract_ocr_from_image(temp_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    ocr_road, ocr_lat, ocr_lng = parse_watermark_text(ocr_lines)
    if ocr_lat and ocr_lng:
        if not lat or not lng:
            lat, lng = ocr_lat, ocr_lng
            source = "photo_watermark_ocr"

    if ocr_road:
        road_name = ocr_road

    # 3. If we have coordinates, reverse geocode to verify or augment road name
    geo_details = None
    if lat and lng:
        geo_details = reverse_geocode_nominatim(lat, lng)
        if geo_details:
            if not road_name:
                road_name = geo_details.get("formatted_name")
            else:
                city = geo_details.get("city") or "Thiruvananthapuram"
                if city.lower() not in road_name.lower():
                    road_name = f"{road_name}, {city}"

    return {
        "success": bool(road_name or (lat and lng)),
        "road_name": road_name,
        "latitude": lat,
        "longitude": lng,
        "source": source,
        "geo_details": geo_details,
    }


def analyze_map_location(conn, lat: float, lng: float, damage_type: str = "Surface deterioration", severity: str = "Moderate") -> dict:
    """
    Perform deep GIS spatial analysis around an incident location:
    - Road corridor and classification matching from road_segments
    - Nearby damage incidents & hotspot density within inspection buffer
    - Traffic volume and corridor importance
    - Junction count within 200m
    - Real-time estimated maintenance priority score & actionable recommendation
    """
    # 1. Match road segment
    road_class = "District Road"
    matched_road = "Urban Arterial Road"
    dist_m = None

    try:
        seg = conn.execute(
            """
            SELECT road_name, road_class,
                   ST_Distance(
                       ST_Transform(geom, %(srid)s),
                       ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s)
                   ) AS dist_m
            FROM road_segments
            ORDER BY dist_m ASC
            LIMIT 1
            """,
            {"srid": _PROJ_SRID, "lat": lat, "lng": lng},
        ).fetchone()

        if seg:
            matched_road = seg["road_name"]
            road_class = seg["road_class"]
            dist_m = round(float(seg["dist_m"]), 1)
    except Exception as exc:
        logger.debug("Road segment query error: %s", exc)

    # If coordinate is along NH 66 corridor (Kazhakkoottam highway)
    if "nh 66" in matched_road.lower() or "panvel" in matched_road.lower() or (8.54 <= lat <= 8.62 and 76.85 <= lng <= 76.90):
        if road_class != "National Highway":
            road_class = "National Highway"
            matched_road = "NH 66 (Panvel - Kochi - Kanyakumari Highway)"

    # 2. Nearby incidents within inspection buffers (150m, 300m, 500m)
    nearby_150m = 0
    nearby_300m = 0
    nearby_reports_summary = []

    try:
        nearby_rows = conn.execute(
            """
            SELECT id, road_name, damage_type, severity,
                   ST_Distance(
                       ST_Transform(geom, %(srid)s),
                       ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s)
                   ) AS dist_m
            FROM road_damage_reports
            WHERE ST_DWithin(
                ST_Transform(geom, %(srid)s),
                ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s),
                500
            )
            ORDER BY dist_m ASC
            """,
            {"srid": _PROJ_SRID, "lat": lat, "lng": lng},
        ).fetchall()

        for r in nearby_rows:
            d = round(float(r["dist_m"]), 1)
            if d <= 150:
                nearby_150m += 1
            if d <= 300:
                nearby_300m += 1
            nearby_reports_summary.append({
                "id": r["id"],
                "road_name": r["road_name"],
                "damage_type": r["damage_type"],
                "severity": r["severity"],
                "distance_m": d,
            })
    except Exception as exc:
        logger.debug("Nearby reports query error: %s", exc)

    # 3. Traffic volume & AADT estimation
    aadt_val = 48500 if road_class == "National Highway" else (24000 if road_class == "State Highway" else 12500)
    traffic_level = "High Volume Arterial (Heavy freight & daily commuter transit)" if road_class == "National Highway" else "Medium Volume Urban Corridor"

    try:
        tc = conn.execute(
            """
            SELECT aadt
            FROM traffic_counts
            WHERE ST_DWithin(
                ST_Transform(count_point, %(srid)s),
                ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s),
                500
            )
            ORDER BY ST_Distance(
                ST_Transform(count_point, %(srid)s),
                ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s)
            ) ASC
            LIMIT 1
            """,
            {"srid": _PROJ_SRID, "lat": lat, "lng": lng},
        ).fetchone()
        if tc and tc["aadt"]:
            aadt_val = int(tc["aadt"])
    except Exception as exc:
        logger.debug("Traffic count query error: %s", exc)

    # 4. Junction density within 200m
    junction_count = 0
    try:
        jct = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM junctions
            WHERE ST_DWithin(
                ST_Transform(geom, %(srid)s),
                ST_Transform(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(srid)s),
                200
            )
            """,
            {"srid": _PROJ_SRID, "lat": lat, "lng": lng},
        ).fetchone()
        if jct:
            junction_count = int(jct["n"])
    except Exception as exc:
        logger.debug("Junction count query error: %s", exc)

    # 5. Compute Live Estimated Priority Score
    # Base severity: Critical=40, Moderate=25, Minor=10
    sev_map = {"Critical": 40, "Moderate": 25, "Minor": 10}
    base_sev = sev_map.get(severity, 25)

    # Road class: National Highway=30, State Highway=20, District=10, Other=5
    road_map = {"National Highway": 30, "State Highway": 20, "District Road": 10, "Other": 5}
    road_pts = road_map.get(road_class, 10)

    # Traffic points: >=50k -> 20, >=20k -> 15, >=10k -> 10
    traffic_pts = 20 if aadt_val >= 50000 else (15 if aadt_val >= 20000 else 10)

    # Junction points: +2.5 per junction up to 15
    jct_pts = min(15.0, junction_count * 2.5)

    # Repeat / cluster points: nearby reports within 150m up to 10
    repeat_pts = min(10.0, nearby_150m * 3.0)

    raw_score = base_sev + road_pts + traffic_pts + jct_pts + repeat_pts
    priority_score = round(min(100.0, max(0.0, raw_score)), 1)
    priority_tier = "High" if priority_score >= 60 else ("Medium" if priority_score >= 30 else "Low")

    # 6. Actionable engineering recommendation
    if road_class == "National Highway":
        recommendation = (
            f"High-speed multi-lane National Highway corridor ({matched_road}). "
            f"{severity} {damage_type.lower()} presents an immediate hydroplaning and aggregate fly-off risk. "
            "Expedited surface milling and bituminous wearing-course overlay recommended."
        )
    elif road_class == "State Highway":
        recommendation = (
            f"State Highway transit route ({matched_road}). "
            f"Schedule mechanized asphalt patch repair to restore riding quality."
        )
    else:
        recommendation = (
            f"Urban municipal roadway ({matched_road}). "
            "Queue for routine departmental patch maintenance."
        )

    # Hotspot cluster description
    cluster_desc = (
        f"Active density cluster ({nearby_300m} incidents within 300m buffer)"
        if nearby_300m >= 2
        else "Isolated incident (no immediate clustering detected)"
    )

    return {
        "latitude": lat,
        "longitude": lng,
        "road_name": matched_road,
        "road_classification": road_class,
        "matched_distance_m": dist_m,
        "traffic_aadt": aadt_val,
        "traffic_level": traffic_level,
        "junctions_within_200m": junction_count,
        "nearby_incidents_count": len(nearby_reports_summary),
        "cluster_status": cluster_desc,
        "estimated_priority": {
            "score": priority_score,
            "tier": priority_tier,
            "breakdown": {
                "severity_points": base_sev,
                "road_class_points": road_pts,
                "traffic_points": traffic_pts,
                "junction_points": jct_pts,
                "cluster_points": repeat_pts,
            },
        },
        "engineering_recommendation": recommendation,
    }
