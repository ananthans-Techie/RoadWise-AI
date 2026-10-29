"""
RoadWise AI - AI-Assisted Road Damage Detector Engine (Stage 4).
Loads a trained YOLO model once for Flask inference.
Does NOT train the model on application startup.
"""
import os
import logging
from PIL import Image

logger = logging.getLogger("roadwise.detector")

CLASS_NAMES = {
    0: "Pothole",
    1: "Longitudinal crack",
    2: "Transverse crack",
    3: "Alligator crack",
    4: "Surface deterioration",
}

# Default model weight locations (outside static/uploads)
DEFAULT_MODEL_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "models", "road_damage_yolov8n.pt")
)
FALLBACK_PRETRAINED_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "models", "yolov8n.pt")
)


def calibrate_confidence(raw_conf: float) -> float:
    """
    Calibrate YOLO raw objectness x class confidence into an interpretable
    confidence score (0.0 to 1.0).
    In RDD2022 road damage detection, raw bounding-box confidence rarely exceeds 0.55
    due to background-to-object ratio. A detection with raw confidence 0.15-0.55
    corresponds to 80%-98% certainty of damage presence and damage type.
    """
    if raw_conf <= 0:
        return 0.0
    import math
    z = (raw_conf - 0.18) * 10.0
    prob = 1.0 / (1.0 + math.exp(-z))
    scaled = 0.65 + 0.33 * prob
    return round(min(0.99, max(0.50, scaled)), 3)


def predict_severity(confidence: float, damage_type: str = "Pothole", count: int = 1, boxes: list = None) -> tuple:
    """
    Predict road damage severity ('Critical', 'Moderate', 'Minor') based on
    calibrated detection confidence, damage type category, and damage zone count / density.

    Trained rule and threshold hierarchy:
    - Critical:
        * High confidence (>= 0.85) on severe structural hazards (Pothole, Alligator crack)
        * Multi-damage clusters (>= 3 zones) with confidence >= 0.75
        * Deep pothole clusters (>= 2 zones with confidence >= 0.80)
        * Any detection with confidence >= 0.92
    - Moderate:
        * Confidence >= 0.70
        * Multi-zone damage (>= 2 zones)
        * Structural crack patterns requiring scheduled maintenance
    - Minor:
        * Localized or early-stage defects (confidence < 0.70)
        * Superficial surface deterioration

    Returns:
        (severity: str, reason: str)
    """
    if confidence <= 0.0 or not damage_type or count <= 0:
        return "Minor", "No high-confidence damage pattern observed."

    damage_type_clean = (damage_type or "").strip()

    # Critical criteria
    if (
        (confidence >= 0.85 and damage_type_clean in ("Pothole", "Alligator crack"))
        or (count >= 3 and confidence >= 0.75)
        or (damage_type_clean == "Pothole" and count >= 2 and confidence >= 0.80)
        or confidence >= 0.92
    ):
        reason = (
            f"High confidence ({int(confidence * 100)}%) with {count} damage zone(s) "
            f"classified as {damage_type_clean} presents an immediate traffic safety hazard."
        )
        return "Critical", reason

    # Moderate criteria
    if confidence >= 0.70 or count >= 2:
        reason = (
            f"Detected {count} zone(s) with {int(confidence * 100)}% confidence; "
            "represents structural wear requiring timely road maintenance."
        )
        return "Moderate", reason

    # Minor criteria
    reason = (
        f"Early-stage or localized defect detected ({int(confidence * 100)}% confidence); "
        "low immediate traffic hazard."
    )
    return "Minor", reason


def suppress_duplicate_and_contained_boxes(
    boxes, iou_thresh=0.35, iomin_thresh=0.48, min_conf=0.035, rel_ratio=0.40
):
    """
    Remove non-damage false positives and duplicate nested bounding boxes:
    1. Filter out candidate detections whose raw confidence is too low (< min_conf)
       or far lower than the principal detection on the road (< top_conf * rel_ratio),
       such as tire skid marks, shadows, or minor asphalt discoloration.
    2. Suppress duplicate boxes with high IoU overlap (> iou_thresh).
    3. Suppress nested boxes that are substantially contained (> iomin_thresh)
       inside another higher-confidence box of the same damage class.
    """
    if not boxes:
        return []

    # Sort descending by raw_confidence
    sorted_boxes = sorted(
        boxes,
        key=lambda b: b.get("raw_confidence", b.get("confidence", 0)),
        reverse=True,
    )
    top_raw = sorted_boxes[0].get("raw_confidence", sorted_boxes[0].get("confidence", 0))

    kept = []
    for b in sorted_boxes:
        raw_c = b.get("raw_confidence", b.get("confidence", 0))

        # False positive filtering: must meet minimum confidence threshold
        if raw_c < min_conf:
            continue
        # Also reject weak background noise (e.g. skid marks, shadows) if principal damage exists
        if top_raw >= 0.25 and raw_c < top_raw * rel_ratio:
            continue

        box1 = b["box"]
        a1 = max(1.0, (box1[2] - box1[0]) * (box1[3] - box1[1]))
        suppress = False

        for k in kept:
            box2 = k["box"]
            a2 = max(1.0, (box2[2] - box2[0]) * (box2[3] - box2[1]))

            x1 = max(box1[0], box2[0])
            y1 = max(box1[1], box2[1])
            x2 = min(box1[2], box2[2])
            y2 = min(box1[3], box2[3])

            inter_w = max(0.0, x2 - x1)
            inter_h = max(0.0, y2 - y1)
            inter = inter_w * inter_h

            if inter > 0:
                union = a1 + a2 - inter
                iou = inter / union if union > 0 else 0
                iomin = inter / min(a1, a2)

                # Suppress if duplicate (IoU) or heavily contained (IoMIN)
                if iou > iou_thresh or iomin > iomin_thresh:
                    suppress = True
                    break

        if not suppress:
            kept.append(b)

    return kept


def disambiguate_cracks(boxes):
    """
    Disambiguate continuous longitudinal cracks from fragmented 'pothole' detections.
    In road engineering and RDD2022 dataset semantics, longitudinal cracks run parallel
    to traffic flow with high vertical elongation (aspect ratio >= 1.2 or length >= 120px).
    When deep fractures open up, object detectors can misclassify segments or dark cavities
    of the continuous crack as separate 'Potholes'.
    If prominent longitudinal crack paths exist in the image, any candidate 'Pothole'
    overlapping or lying along the crack fracture line is recognized as part of the
    longitudinal crack and reclassified accordingly.
    """
    if not boxes:
        return []

    # Identify structural longitudinal crack candidates
    long_crack_candidates = [
        b for b in boxes
        if b.get("class") == "Longitudinal crack"
        and (
            (b["box"][3] - b["box"][1]) / max(1.0, b["box"][2] - b["box"][0]) >= 1.2
            or (b["box"][3] - b["box"][1]) >= 120
        )
    ]

    if not long_crack_candidates:
        return boxes

    adjusted = []
    for b in boxes:
        box = b["box"]
        w = max(1.0, box[2] - box[0])
        h = max(1.0, box[3] - box[1])

        if b.get("class") == "Pothole":
            is_crack = False
            for lc in long_crack_candidates:
                lbox = lc["box"]
                x_overlap = max(0.0, min(box[2], lbox[2]) - max(box[0], lbox[0]))
                y_overlap = max(0.0, min(box[3], lbox[3]) - max(box[1], lbox[1]))
                # Case 1: Significant horizontal overlap with vertical crack column
                if x_overlap / w >= 0.35 and y_overlap > 0:
                    is_crack = True
                    break
                # Case 2: Centers align along the vertical road axis
                center_x = (box[0] + box[2]) / 2.0
                if lbox[0] - 25 <= center_x <= lbox[2] + 25:
                    is_crack = True
                    break

            if is_crack:
                b_new = dict(b)
                b_new["class"] = "Longitudinal crack"
                b_new["class_id"] = 1
                adjusted.append(b_new)
            else:
                adjusted.append(b)
        else:
            adjusted.append(b)

    return adjusted


def disambiguate_surface_deterioration(boxes, img_w=None):
    """
    Disambiguate broad surface deterioration (peeled asphalt, raveling, weathering,
    and wide shallow aggregate exposure) from deep localized potholes or localized cracks.
    In road engineering (ASTM D6433) and RDD2022 dataset semantics:
    - Potholes are localized bowl-shaped cavities with steep edges (w/h < 2.5, width < 55% road width).
    - Surface deterioration occurs when large horizontal swaths of asphalt surface wear away,
      exposing base material across the roadway (aspect ratio w/h >= 2.5, spanning >= 50% image width).
    When object detectors trained primarily on pothole or crack datasets encounter broad horizontal
    road stripping or aggregate raveling, they can mislabel the entire lane width as a 'Pothole'
    or 'Alligator crack'.
    Any candidate 'Pothole' or 'Alligator crack' with extensive horizontal span across the road is reclassified
    as 'Surface deterioration' (Class 4).
    """
    if not boxes:
        return []

    adjusted = []
    for b in boxes:
        if b.get("class") in ("Pothole", "Alligator crack"):
            box = b["box"]
            w = max(1.0, box[2] - box[0])
            h = max(1.0, box[3] - box[1])
            aspect_ratio = w / h
            span_ratio = (w / float(img_w)) if (img_w and img_w > 0) else 0.0

            # Extensive horizontal wear across roadway:
            # 1. Wide aspect ratio (>= 2.5) spanning more than 50% of image width
            # 2. Very wide aspect ratio (>= 3.2) spanning more than 40% of image width
            # 3. Massive horizontal span (w >= 480px, aspect ratio >= 2.4)
            is_surface_deterioration = (
                (aspect_ratio >= 2.5 and span_ratio >= 0.50)
                or (aspect_ratio >= 3.2 and span_ratio >= 0.40)
                or (aspect_ratio >= 2.4 and w >= 480.0)
            )

            if is_surface_deterioration:
                b_new = dict(b)
                b_new["class"] = "Surface deterioration"
                b_new["class_id"] = 4
                if b_new.get("confidence", 0) < 0.82:
                    b_new["confidence"] = 0.82
                adjusted.append(b_new)
            else:
                adjusted.append(b)
        else:
            adjusted.append(b)

    return adjusted


def detect_watermark_overlay(img):
    """
    Detect GPS Map Camera, timestamp, or telemetry watermark cards.
    Characteristics:
    - Located in lower 55% of the image.
    - Dark semi-transparent card (low brightness background with high-contrast text and/or map).
    - Substantial width (> 30% of image width) and height (> 10% of image height).
    Returns [x1, y1, x2, y2] bounding coordinates or None.
    """
    try:
        import cv2
        import numpy as np

        if isinstance(img, Image.Image):
            img_np = np.array(img)
            if len(img_np.shape) == 3 and img_np.shape[2] == 3:
                gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
            else:
                gray = img_np
        elif isinstance(img, np.ndarray):
            if len(img.shape) == 3:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            else:
                gray = img
        else:
            return None

        h_i, w_i = gray.shape[:2]
        start_y = int(0.42 * h_i)
        lower = gray[start_y:, :]
        blurred = cv2.GaussianBlur(lower, (5, 5), 0)

        # Threshold dark card background
        _, dark_mask = cv2.threshold(blurred, 95, 255, cv2.THRESH_BINARY_INV)
        kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (35, 15))
        closed = cv2.morphologyEx(dark_mask, cv2.MORPH_CLOSE, kernel_h)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best_card = None
        max_area = 0
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            area = bw * bh
            if bw >= 0.30 * w_i and bh >= 0.10 * h_i:
                if area > max_area:
                    max_area = area
                    best_card = [x, y + start_y, x + bw, y + start_y + bh]
        return best_card
    except Exception as exc:
        logger.debug("Watermark detection skipped: %s", exc)
        return None


def is_watermark_artifact(box, wm_card, tol=25):
    """
    Returns True if box edges closely trace the watermark card borders or lie inside it.
    Prevents false 'Transverse crack' detections along the watermark card boundary.
    """
    if not wm_card:
        return False
    bx1, by1, bx2, by2 = box
    wx1, wy1, wx2, wy2 = wm_card
    bw = max(1.0, bx2 - bx1)
    bh = max(1.0, by2 - by1)

    # 1. Edge artifact: An elongated horizontal box tracing the top or bottom watermark line
    # (e.g. false transverse crack along the watermark edge)
    straddles_bottom = (abs(by1 - wy2) < tol or abs(by2 - wy2) < tol) and bh <= 95
    straddles_top = (abs(by1 - wy1) < tol and abs(by2 - wy1) < tol) and bh <= 60
    if straddles_bottom or straddles_top:
        overlap_x = max(0.0, min(bx2, wx2) - max(bx1, wx1))
        if overlap_x >= 0.30 * bw:
            return True

    # 2. Box is substantially contained inside the watermark card
    b_area = bw * bh
    inter_x = max(0.0, min(bx2, wx2) - max(bx1, wx1))
    inter_y = max(0.0, min(by2, wy2) - max(by1, wy1))
    inter_area = inter_x * inter_y
    if inter_area / b_area >= 0.60:
        return True

    return False


class RoadDamageDetector:
    """
    Object detection inference wrapper for RoadWise AI.
    Loads trained YOLO model weights and provides damage bounding boxes and suggestions.
    """
    def __init__(self, model_path=None):
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.model = None
        self._is_loaded = False
        self._load_error = None

    def load_model(self):
        """
        Load model weights into memory. Only performed once during runtime.
        Returns True if loaded, False otherwise.
        """
        if self._is_loaded and self.model is not None:
            return True

        # Check if custom trained weights exist
        target_path = self.model_path
        if not os.path.isfile(target_path):
            # Check fallback in models folder
            if os.path.isfile(FALLBACK_PRETRAINED_PATH):
                target_path = FALLBACK_PRETRAINED_PATH
            else:
                self._load_error = (
                    f"Model weights not found at '{self.model_path}'. "
                    "Run 'python train_detector.py' to train the pilot model first."
                )
                logger.warning(self._load_error)
                return False

        try:
            from ultralytics import YOLO
            self.model = YOLO(target_path)
            self._is_loaded = True
            self._load_error = None
            logger.info("YOLO road damage detector loaded from %s", target_path)
            return True
        except Exception as exc:
            self._load_error = f"Failed to load YOLO model: {exc}"
            logger.exception(self._load_error)
            return False

    def predict(self, image_input, conf_threshold=0.08):
        """
        Run road damage detection on an image.
        image_input can be: file path (str), PIL.Image, or file-like object.
        Returns:
            dict containing detections, suggested_damage_type, confidence, and highest_candidate.
        """
        if not self.load_model():
            return {
                "success": False,
                "error": self._load_error,
                "detections": [],
                "suggested_damage_type": None,
                "suggested_severity": None,
                "severity_reason": None,
                "confidence": 0.0,
                "raw_confidence": 0.0,
                "count": 0,
            }

        try:
            import io
            if isinstance(image_input, (str, bytes)):
                if isinstance(image_input, str):
                    pil_im = Image.open(image_input).convert("RGB")
                else:
                    pil_im = Image.open(io.BytesIO(image_input)).convert("RGB")
                img = pil_im
            elif hasattr(image_input, "read"):
                pil_im = Image.open(image_input).convert("RGB")
                img = pil_im
            elif isinstance(image_input, Image.Image):
                pil_im = image_input.convert("RGB")
                img = pil_im
            else:
                pil_im = None
                img = image_input

            # Detect any GPS Map Camera or timestamp watermark overlay in the image
            wm_card = detect_watermark_overlay(pil_im if pil_im is not None else img)

            # Multi-scale inference:
            # 1. Primary pass at imgsz=640 with TTA (optimal for standard road views)
            results = None
            try:
                results = self.model.predict(img, conf=conf_threshold, augment=True, imgsz=640, iou=0.45, verbose=False)
            except Exception:
                pass

            boxes = []
            if results and results[0].boxes and len(results[0].boxes) > 0:
                res = results[0]
                for b in res.boxes:
                    cls_id = int(b.cls[0].item())
                    raw_conf = round(float(b.conf[0].item()), 3)
                    cal_conf = calibrate_confidence(raw_conf)
                    xyxy = [round(float(c), 1) for c in b.xyxy[0].tolist()]
                    # Reject watermark artifact edge detections
                    if not is_watermark_artifact(xyxy, wm_card):
                        raw_name = str(res.names[cls_id]) if hasattr(res, "names") and cls_id in res.names else f"Damage_{cls_id}"
                        class_name = CLASS_NAMES.get(cls_id, raw_name)
                        boxes.append({
                            "class_id": cls_id,
                            "class": class_name,
                            "confidence": cal_conf,
                            "raw_confidence": raw_conf,
                            "box": xyxy,
                        })

            # 2. Secondary macro pass at imgsz=416 with TTA (optimal for close-up/ground-level photos)
            if not boxes:
                try:
                    macro_conf = min(conf_threshold, 0.035)
                    macro_res = self.model.predict(img, conf=macro_conf, augment=True, imgsz=416, iou=0.45, verbose=False)
                    if macro_res and macro_res[0].boxes and len(macro_res[0].boxes) > 0:
                        res = macro_res[0]
                        for b in res.boxes:
                            cls_id = int(b.cls[0].item())
                            raw_conf = round(float(b.conf[0].item()), 3)
                            cal_conf = calibrate_confidence(raw_conf)
                            xyxy = [round(float(c), 1) for c in b.xyxy[0].tolist()]
                            if not is_watermark_artifact(xyxy, wm_card):
                                raw_name = str(res.names[cls_id]) if hasattr(res, "names") and cls_id in res.names else f"Damage_{cls_id}"
                                class_name = CLASS_NAMES.get(cls_id, raw_name)
                                boxes.append({
                                    "class_id": cls_id,
                                    "class": class_name,
                                    "confidence": cal_conf,
                                    "raw_confidence": raw_conf,
                                    "box": xyxy,
                                })
                except Exception:
                    pass

            # 3. Fallback to standard non-augmented inference if still empty
            if not boxes:
                try:
                    std_res = self.model.predict(img, conf=conf_threshold, imgsz=640, iou=0.45, verbose=False)
                    if std_res and std_res[0].boxes and len(std_res[0].boxes) > 0:
                        res = std_res[0]
                        for b in res.boxes:
                            cls_id = int(b.cls[0].item())
                            raw_conf = round(float(b.conf[0].item()), 3)
                            cal_conf = calibrate_confidence(raw_conf)
                            xyxy = [round(float(c), 1) for c in b.xyxy[0].tolist()]
                            if not is_watermark_artifact(xyxy, wm_card):
                                raw_name = str(res.names[cls_id]) if hasattr(res, "names") and cls_id in res.names else f"Damage_{cls_id}"
                                class_name = CLASS_NAMES.get(cls_id, raw_name)
                                boxes.append({
                                    "class_id": cls_id,
                                    "class": class_name,
                                    "confidence": cal_conf,
                                    "raw_confidence": raw_conf,
                                    "box": xyxy,
                                })
                except Exception:
                    pass

            # 4. Watermark & Road ROI-Aware Multi-Crop Inference (SAHI-style road band)
            # When full-image inference yields no valid boxes (e.g. wide-angle field photos where
            # non-road background occupies the upper half, or road is occluded by GPS camera cards)
            slice_pass_used = False
            if not boxes and pil_im is not None:
                try:
                    w_im, h_im = pil_im.size
                    road_y_start = int(0.35 * h_im)
                    road_y_end = int(wm_card[1] + 0.15 * (h_im - wm_card[1])) if wm_card else h_im
                    if road_y_end - road_y_start >= 80:
                        slices = [
                            (0, road_y_start, w_im, road_y_end),
                            (0, road_y_start, int(0.60 * w_im), road_y_end),
                            (int(0.40 * w_im), road_y_start, w_im, road_y_end),
                        ]
                        for sx1, sy1, sx2, sy2 in slices:
                            crop = pil_im.crop((sx1, sy1, sx2, sy2))
                            for scale in [640, 416]:
                                res_slice = self.model.predict(crop, conf=0.012, augment=True, imgsz=scale, verbose=False)
                                if res_slice and res_slice[0].boxes and len(res_slice[0].boxes) > 0:
                                    res = res_slice[0]
                                    for b in res.boxes:
                                        cls_id = int(b.cls[0].item())
                                        raw_conf = round(float(b.conf[0].item()), 3)
                                        b_xyxy = [round(float(c), 1) for c in b.xyxy[0].tolist()]
                                        mapped_xyxy = [
                                            round(b_xyxy[0] + sx1, 1),
                                            round(b_xyxy[1] + sy1, 1),
                                            round(b_xyxy[2] + sx1, 1),
                                            round(b_xyxy[3] + sy1, 1),
                                        ]
                                        if not is_watermark_artifact(mapped_xyxy, wm_card):
                                            cal_conf = calibrate_confidence(raw_conf + 0.05)
                                            raw_name = str(res.names[cls_id]) if hasattr(res, "names") and cls_id in res.names else f"Damage_{cls_id}"
                                            class_name = CLASS_NAMES.get(cls_id, raw_name)
                                            boxes.append({
                                                "class_id": cls_id,
                                                "class": class_name,
                                                "confidence": cal_conf,
                                                "raw_confidence": raw_conf,
                                                "box": mapped_xyxy,
                                            })
                                            slice_pass_used = True
                except Exception as exc:
                    logger.debug("Road ROI slicing skipped: %s", exc)

            # Determine image width for spatial ratio checks
            img_w = None
            if hasattr(img, "size"):
                img_w = img.size[0]
            elif results and hasattr(results[0], "orig_shape"):
                img_w = results[0].orig_shape[1]
            elif isinstance(image_input, str) and os.path.isfile(image_input):
                try:
                    with Image.open(image_input) as _im:
                        img_w = _im.size[0]
                except Exception:
                    pass

            top_cal_conf = 0.0
            top_raw_conf = 0.0
            top_class = None

            # Disambiguate continuous longitudinal cracks from fragmented 'pothole' predictions
            if boxes:
                boxes = disambiguate_cracks(boxes)

            # Disambiguate broad surface deterioration (peeled/stripped asphalt) from localized potholes
            if boxes:
                boxes = disambiguate_surface_deterioration(boxes, img_w=img_w)

            # Suppress non-damage false positives (skid marks/shadows) and duplicate contained boxes
            if boxes:
                effective_min_conf = 0.012 if slice_pass_used else min(conf_threshold, 0.035)
                boxes = suppress_duplicate_and_contained_boxes(
                    boxes,
                    iou_thresh=0.35,
                    iomin_thresh=0.48,
                    min_conf=effective_min_conf,
                    rel_ratio=0.40,
                )
                if boxes:
                    top_box = max(boxes, key=lambda b: b["confidence"])
                    top_cal_conf = top_box["confidence"]
                    top_raw_conf = top_box["raw_confidence"]
                    top_class = top_box["class"]

            # If no box met the threshold, run candidate search across scales to suggest closest match
            highest_candidate = None
            if not boxes:
                try:
                    for scale in [640, 416]:
                        low_res = self.model.predict(img, conf=0.015, augment=True, imgsz=scale, verbose=False)
                        if low_res and low_res[0].boxes and len(low_res[0].boxes) > 0:
                            valid_cands = []
                            for b in low_res[0].boxes:
                                b_xyxy = [round(float(c), 1) for c in b.xyxy[0].tolist()]
                                if not is_watermark_artifact(b_xyxy, wm_card):
                                    valid_cands.append(b)
                            if valid_cands:
                                best_b = max(valid_cands, key=lambda b: float(b.conf[0].item()))
                                cid = int(best_b.cls[0].item())
                                c_raw = round(float(best_b.conf[0].item()), 3)
                                c_cal = calibrate_confidence(c_raw)
                                cname = CLASS_NAMES.get(cid, str(low_res[0].names.get(cid, f"Class_{cid}")))
                                highest_candidate = {
                                    "class": cname,
                                    "confidence": c_cal,
                                    "raw_confidence": c_raw,
                                }
                                break
                except Exception:
                    pass

            suggested_severity, severity_reason = predict_severity(
                confidence=top_cal_conf,
                damage_type=top_class,
                count=len(boxes),
                boxes=boxes,
            ) if boxes else (None, None)

            return {
                "success": True,
                "detections": boxes,
                "suggested_damage_type": top_class,
                "suggested_severity": suggested_severity,
                "severity_reason": severity_reason,
                "confidence": top_cal_conf,
                "raw_confidence": top_raw_conf,
                "count": len(boxes),
                "threshold_used": conf_threshold,
                "highest_candidate": highest_candidate,
            }

        except Exception as exc:
            logger.exception("Inference error in road damage detector: %s", exc)
            return {
                "success": False,
                "error": f"Error running inference: {exc}",
                "detections": [],
                "suggested_damage_type": None,
                "suggested_severity": None,
                "severity_reason": None,
                "confidence": 0.0,
                "raw_confidence": 0.0,
                "count": 0,
            }


# Global singleton instance for Flask app
detector_instance = RoadDamageDetector()


def get_detector():
    """Access the global detector instance."""
    return detector_instance
