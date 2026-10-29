"""
RoadWise AI - Stage 4 Unit Tests: AI-Assisted Road Damage Detection.
Tests Pascal VOC to YOLO conversion, coordinate normalization, clamping,
typo handling, and the Flask /api/detect inference endpoint.
"""
import io
import os
import sys
import tempfile
import unittest
import importlib
from unittest.mock import MagicMock, patch
from PIL import Image

# Ensure project and RoadWiseAI directories are in sys.path
_current_dir = os.path.dirname(os.path.abspath(__file__))
_roadwise_dir = os.path.abspath(os.path.join(_current_dir, ".."))
_project_dir = os.path.abspath(os.path.join(_roadwise_dir, ".."))
for _p in (_project_dir, _roadwise_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from prepare_rdd_dataset import voc_to_yolo_bbox, parse_voc_xml, CLASS_MAP, CLASS_NAMES
import detector
from detector import RoadDamageDetector, get_detector

try:
    app_module = importlib.import_module("RoadWiseAI.app")
except ModuleNotFoundError:
    app_module = importlib.import_module("app")

app = app_module.app


def create_test_jpeg(width=200, height=200, color=(100, 100, 100)):
    """Generate in-memory valid JPEG bytes."""
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=color)
    img.save(buf, format="JPEG")
    buf.seek(0)
    return buf.getvalue()


class TestPascalVocToYoloConversion(unittest.TestCase):
    """Test Pascal VOC XML parsing and YOLO coordinate normalization."""

    def test_exact_center_and_size_normalization(self):
        """Test standard coordinate normalization to [0, 1]."""
        # Image 800x1000, Box from (100, 100) to (300, 500)
        # width = 200, height = 400
        # x_center = 200/800 = 0.25
        # y_center = 300/1000 = 0.3
        # norm_w = 200/800 = 0.25
        # norm_h = 400/1000 = 0.4
        result = voc_to_yolo_bbox(100, 100, 300, 500, img_w=800, img_h=1000)
        self.assertIsNotNone(result)
        xc, yc, w, h = result
        self.assertAlmostEqual(xc, 0.25, places=5)
        self.assertAlmostEqual(yc, 0.30, places=5)
        self.assertAlmostEqual(w, 0.25, places=5)
        self.assertAlmostEqual(h, 0.40, places=5)

    def test_coordinate_clamping(self):
        """Out-of-bounds coordinates should be safely clamped to [0, img_dim]."""
        # Box extends outside 720x720 image: xmin=-50, xmax=800
        # Clamped: xmin=0, xmax=720 -> x_center=0.5, norm_w=1.0
        result = voc_to_yolo_bbox(-50, 100, 800, 300, img_w=720, img_h=720)
        self.assertIsNotNone(result)
        xc, yc, w, h = result
        self.assertAlmostEqual(xc, 0.5, places=5)
        self.assertAlmostEqual(w, 1.0, places=5)

    def test_degenerate_boxes_filtered(self):
        """Zero-area or inverted bounding boxes must return None."""
        # Zero width
        self.assertIsNone(voc_to_yolo_bbox(100, 50, 100, 150, 720, 720))
        # Zero height
        self.assertIsNone(voc_to_yolo_bbox(50, 100, 150, 100, 720, 720))
        # Inverted box (xmin > xmax)
        self.assertIsNone(voc_to_yolo_bbox(200, 50, 100, 150, 720, 720))
        # Zero dimensions for image
        self.assertIsNone(voc_to_yolo_bbox(10, 10, 50, 50, 0, 720))

    def test_parse_voc_xml_with_classes(self):
        """Test parsing VOC XML with diverse RDD2022 damage labels."""
        xml_content = """<annotation>
            <size>
                <width>720</width>
                <height>720</height>
            </size>
            <object>
                <name>D40</name>
                <bndbox>
                    <xmin>100</xmin>
                    <ymin>150</ymin>
                    <xmax>300</xmax>
                    <ymax>350</ymax>
                </bndbox>
            </object>
            <object>
                <name>D20</name>
                <bndbox>
                    <xmin>400</xmin>
                    <ymin>450</ymin>
                    <xmax>600</xmax>
                    <ymax>650</ymax>
                </bndbox>
            </object>
        </annotation>"""
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
            f.write(xml_content)
            temp_path = f.name

        try:
            w, h, boxes = parse_voc_xml(temp_path)
            self.assertEqual(w, 720)
            self.assertEqual(h, 720)
            self.assertEqual(len(boxes), 2)
            # D40 -> 0 (Pothole)
            self.assertEqual(boxes[0][0], 0)
            # D20 -> 3 (Alligator crack)
            self.assertEqual(boxes[1][0], 3)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_typo_handling_d0w0(self):
        """Ensure RDD2022 dataset typo 'D0w0' is correctly mapped to Longitudinal crack (class 1)."""
        self.assertIn("D0w0", CLASS_MAP)
        self.assertEqual(CLASS_MAP["D0w0"], 1)

        xml_content = """<annotation>
            <size><width>720</width><height>720</height></size>
            <object>
                <name>D0w0</name>
                <bndbox>
                    <xmin>50</xmin><ymin>50</ymin><xmax>150</xmax><ymax>250</ymax>
                </bndbox>
            </object>
        </annotation>"""
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as f:
            f.write(xml_content)
            temp_path = f.name

        try:
            _, _, boxes = parse_voc_xml(temp_path)
            self.assertEqual(len(boxes), 1)
            self.assertEqual(boxes[0][0], 1)
            self.assertEqual(CLASS_NAMES[boxes[0][0]], "Longitudinal crack")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


class TestDetectorUnit(unittest.TestCase):
    """Test RoadDamageDetector class behaviors."""

    def test_class_names_match_app(self):
        """Detector classes must exactly match RoadWise AI damage types."""
        expected = {
            0: "Pothole",
            1: "Longitudinal crack",
            2: "Transverse crack",
            3: "Alligator crack",
            4: "Surface deterioration",
        }
        self.assertEqual(detector.CLASS_NAMES, expected)

    def test_unloaded_model_handles_gracefully(self):
        """When model weights are missing, predict() returns graceful failure dict."""
        det = RoadDamageDetector(model_path="non_existent_model_weights.pt")
        img_bytes = create_test_jpeg()
        result = det.predict(img_bytes)
        self.assertFalse(result["success"])
        self.assertEqual(result["detections"], [])
        self.assertIsNone(result["suggested_damage_type"])
        self.assertEqual(result["count"], 0)

    def test_disambiguate_cracks_reclassifies_crack_segments(self):
        """Disambiguate cracks must reclassify pothole chunks lying on a longitudinal crack to Longitudinal crack."""
        from detector import disambiguate_cracks
        boxes = [
            # Longitudinal crack spanning y from 80 to 400 (h=320, w=150, aspect=2.13)
            {"class": "Longitudinal crack", "class_id": 1, "confidence": 0.85, "raw_confidence": 0.20, "box": [450.0, 80.0, 600.0, 400.0]},
            # Pothole candidate inside the crack path
            {"class": "Pothole", "class_id": 0, "confidence": 0.94, "raw_confidence": 0.38, "box": [460.0, 220.0, 590.0, 390.0]},
            # Separate standalone pothole not on the crack path
            {"class": "Pothole", "class_id": 0, "confidence": 0.90, "raw_confidence": 0.30, "box": [50.0, 100.0, 150.0, 200.0]},
        ]
        adjusted = disambiguate_cracks(boxes)
        # The box inside the crack path must become Longitudinal crack
        self.assertEqual(adjusted[1]["class"], "Longitudinal crack")
        self.assertEqual(adjusted[1]["class_id"], 1)
        # The standalone pothole must remain Pothole
        self.assertEqual(adjusted[2]["class"], "Pothole")
        self.assertEqual(adjusted[2]["class_id"], 0)

    def test_disambiguate_surface_deterioration(self):
        """Wide pavement wear spanning across road must be reclassified to Surface deterioration while localized potholes stay Pothole."""
        from detector import disambiguate_surface_deterioration
        boxes = [
            # Broad pavement wear across road (w=995.5, h=290.5, aspect=3.43, span=97%)
            {"class": "Pothole", "class_id": 0, "confidence": 0.76, "raw_confidence": 0.25, "box": [10.0, 268.0, 1005.5, 558.5]},
            # Another wide swath at top (w=794.6, h=158.4, aspect=5.01, span=78%)
            {"class": "Pothole", "class_id": 0, "confidence": 0.75, "raw_confidence": 0.24, "box": [229.4, 0.0, 1024.0, 158.4]},
            # Localized bowl-shaped pothole (w=190, h=190, aspect=1.0, span=19%)
            {"class": "Pothole", "class_id": 0, "confidence": 0.88, "raw_confidence": 0.35, "box": [120.0, 150.0, 310.0, 340.0]},
            # Longitudinal crack (already crack, untouched)
            {"class": "Longitudinal crack", "class_id": 1, "confidence": 0.80, "raw_confidence": 0.22, "box": [450.0, 80.0, 600.0, 400.0]},
        ]
        adjusted = disambiguate_surface_deterioration(boxes, img_w=1024)
        # Broad pavement wear reclassified to Surface deterioration (class 4)
        self.assertEqual(adjusted[0]["class"], "Surface deterioration")
        self.assertEqual(adjusted[0]["class_id"], 4)
        self.assertEqual(adjusted[1]["class"], "Surface deterioration")
        self.assertEqual(adjusted[1]["class_id"], 4)
        # Localized pothole remains Pothole
        self.assertEqual(adjusted[2]["class"], "Pothole")
        self.assertEqual(adjusted[2]["class_id"], 0)
        # Longitudinal crack remains untouched
        self.assertEqual(adjusted[3]["class"], "Longitudinal crack")
        self.assertEqual(adjusted[3]["class_id"], 1)

    def test_disambiguate_surface_deterioration_with_alligator_crack(self):
        """Extensive horizontal pavement wear misclassified as Alligator crack must be reclassified to Surface deterioration."""
        from detector import disambiguate_surface_deterioration
        boxes = [
            # Broad pavement wear across road (w=990.0, h=270.0, aspect=3.66, span=96%)
            {"class": "Alligator crack", "class_id": 3, "confidence": 0.75, "raw_confidence": 0.06, "box": [20.0, 260.0, 1010.0, 530.0]},
            # Localized alligator crack patch (w=150, h=140, aspect=1.07, span=15%)
            {"class": "Alligator crack", "class_id": 3, "confidence": 0.85, "raw_confidence": 0.28, "box": [400.0, 300.0, 550.0, 440.0]},
        ]
        adjusted = disambiguate_surface_deterioration(boxes, img_w=1024)
        self.assertEqual(adjusted[0]["class"], "Surface deterioration")
        self.assertEqual(adjusted[0]["class_id"], 4)
        self.assertGreaterEqual(adjusted[0]["confidence"], 0.82)
        # Localized patch remains Alligator crack
        self.assertEqual(adjusted[1]["class"], "Alligator crack")
        self.assertEqual(adjusted[1]["class_id"], 3)

    def test_watermark_artifact_filtering(self):
        """Watermark card boundaries must be detected and rejected as false crack artifacts."""
        from detector import is_watermark_artifact
        wm_card = [300, 480, 880, 710]
        # Box tracing the bottom edge of the watermark card
        artifact_bottom = [450.0, 675.0, 950.0, 720.0]
        self.assertTrue(is_watermark_artifact(artifact_bottom, wm_card))
        # Box inside the watermark card
        artifact_inside = [350.0, 500.0, 700.0, 680.0]
        self.assertTrue(is_watermark_artifact(artifact_inside, wm_card))
        # Genuine road damage box above the watermark card
        genuine_road_damage = [10.0, 280.0, 600.0, 470.0]
        self.assertFalse(is_watermark_artifact(genuine_road_damage, wm_card))


class TestApiDetectEndpoint(unittest.TestCase):
    """Test Flask /api/detect endpoint integration."""

    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_detect_no_photo_returns_400(self):
        """Endpoint rejects requests with no photo payload."""
        response = self.client.post("/api/detect", data={})
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data["status"], "error")
        self.assertIn("error", data)

    def test_detect_invalid_file_extension(self):
        """Endpoint rejects non-image files."""
        data = {"photo": (io.BytesIO(b"not an image"), "notes.txt")}
        response = self.client.post(
            "/api/detect", data=data, content_type="multipart/form-data"
        )
        self.assertEqual(response.status_code, 400)
        json_data = response.get_json()
        self.assertEqual(json_data["status"], "error")

    def test_detect_unloaded_model_returns_graceful_info(self):
        """If model is not yet trained/available, endpoint returns 200 with info message."""
        jpeg_bytes = create_test_jpeg()
        data = {"photo": (io.BytesIO(jpeg_bytes), "test.jpg")}

        # Patch detector to simulate offline/unloaded model
        with patch.object(
            app_module.get_detector(),
            "predict",
            return_value={
                "success": False,
                "error": "Model weights not found. Run train_detector.py first.",
                "detections": [],
                "suggested_damage_type": None,
                "confidence": 0.0,
                "count": 0,
            },
        ):
            response = self.client.post(
                "/api/detect", data=data, content_type="multipart/form-data"
            )
            self.assertEqual(response.status_code, 200)
            res_data = response.get_json()
            self.assertEqual(res_data["status"], "info")
            self.assertEqual(res_data["detections"], [])
            self.assertIsNone(res_data["suggested_damage_type"])

    def test_detect_success_with_mocked_yolo_result(self):
        """Test inference returning detected pothole bounding boxes."""
        jpeg_bytes = create_test_jpeg(width=720, height=720)
        data = {"photo": (io.BytesIO(jpeg_bytes), "road_pothole.jpg")}

        mock_result = {
            "success": True,
            "detections": [
                {
                    "class_id": 0,
                    "class": "Pothole",
                    "confidence": 0.88,
                    "box": [120.0, 150.0, 310.0, 340.0],
                }
            ],
            "suggested_damage_type": "Pothole",
            "confidence": 0.88,
            "count": 1,
        }

        with patch.object(
            app_module.get_detector(), "predict", return_value=mock_result
        ):
            response = self.client.post(
                "/api/detect", data=data, content_type="multipart/form-data"
            )
            self.assertEqual(response.status_code, 200)
            res_data = response.get_json()
            self.assertEqual(res_data["status"], "success")
            self.assertEqual(res_data["suggested_damage_type"], "Pothole")
            self.assertEqual(res_data["confidence"], 0.88)
            self.assertEqual(len(res_data["detections"]), 1)
            self.assertEqual(res_data["detections"][0]["box"], [120.0, 150.0, 310.0, 340.0])

    def test_detect_infers_severity_based_on_confidence_without_auto_saving(self):
        """Detection endpoint predicts suggested_severity based on confidence and does not auto-insert reports."""
        jpeg_bytes = create_test_jpeg()
        data = {"photo": (io.BytesIO(jpeg_bytes), "road_photo.jpg")}

        mock_result = {
            "success": True,
            "detections": [
                {
                    "class_id": 0,
                    "class": "Pothole",
                    "confidence": 0.95,
                    "box": [50.0, 50.0, 200.0, 200.0],
                }
            ],
            "suggested_damage_type": "Pothole",
            "suggested_severity": "Critical",
            "confidence": 0.95,
            "count": 1,
        }

        with patch.object(app_module.get_detector(), "predict", return_value=mock_result), \
             patch.object(app_module, "save_report") as mock_save:
            response = self.client.post(
                "/api/detect", data=data, content_type="multipart/form-data"
            )
            self.assertEqual(response.status_code, 200)
            res_data = response.get_json()

            # Verify severity is predicted and returned
            self.assertEqual(res_data["suggested_severity"], "Critical")

            # Verify save_report was NOT called
            mock_save.assert_not_called()

    def test_predict_severity_thresholds(self):
        """Test rule-based severity prediction across confidence and hazard categories."""
        from detector import predict_severity
        # High confidence pothole -> Critical
        sev_crit, _ = predict_severity(confidence=0.92, damage_type="Pothole", count=1)
        self.assertEqual(sev_crit, "Critical")

        # Cluster of multiple potholes with confidence 0.81 -> Critical
        sev_cluster, _ = predict_severity(confidence=0.81, damage_type="Pothole", count=4)
        self.assertEqual(sev_cluster, "Critical")

        # Moderate confidence -> Moderate
        sev_mod, _ = predict_severity(confidence=0.74, damage_type="Longitudinal crack", count=1)
        self.assertEqual(sev_mod, "Moderate")

        # Low confidence -> Minor
        sev_min, _ = predict_severity(confidence=0.62, damage_type="Surface deterioration", count=1)
        self.assertEqual(sev_min, "Minor")


    def test_detect_passes_custom_conf_threshold(self):
        """Endpoint accepts optional conf parameter and passes to detector."""
        jpeg_bytes = create_test_jpeg()
        data = {"photo": (io.BytesIO(jpeg_bytes), "road_photo.jpg"), "conf": "0.15"}

        mock_result = {
            "success": True,
            "detections": [],
            "suggested_damage_type": None,
            "confidence": 0.0,
            "count": 0,
            "threshold_used": 0.15,
            "highest_candidate": {"class": "Pothole", "confidence": 0.11},
        }

        with patch.object(app_module.get_detector(), "predict", return_value=mock_result) as mock_predict:
            response = self.client.post(
                "/api/detect", data=data, content_type="multipart/form-data"
            )
            self.assertEqual(response.status_code, 200)
            res_data = response.get_json()
            self.assertEqual(res_data["threshold_used"], 0.15)
            self.assertEqual(res_data["highest_candidate"]["class"], "Pothole")
            # Verify conf_threshold passed to predict was 0.15
            mock_predict.assert_called_once()
            _, kwargs = mock_predict.call_args
            self.assertAlmostEqual(kwargs.get("conf_threshold"), 0.15)


if __name__ == "__main__":
    unittest.main()

