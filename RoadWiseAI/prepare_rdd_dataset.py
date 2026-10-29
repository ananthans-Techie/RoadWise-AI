"""
RDD2022 Pascal VOC XML to YOLO Object Detection Dataset Converter.
RoadWise AI Stage 4: AI-assisted road-damage detection.

Converts labeled training images and XML annotations from RDD2022 (India subset)
into normalized YOLO format with a reproducible train/validation split.
Leaves original dataset completely untouched.
"""
import os
import sys
import glob
import random
import shutil
import argparse
import xml.etree.ElementTree as ET
from collections import Counter

# Class Mapping: Maps RDD2022 damage labels to RoadWise AI damage types
CLASS_MAP = {
    # 0: Pothole
    "D40": 0,
    # 1: Longitudinal crack
    "D00": 1,
    "D01": 1,
    "D0w0": 1,  # Typo correction in RDD2022 India dataset
    # 2: Transverse crack
    "D10": 2,
    "D11": 2,
    # 3: Alligator crack
    "D20": 3,
    # 4: Surface deterioration
    "D44": 4,  # White line blur
    "D43": 4,  # Cross walk blur
    "D50": 4,  # Other deterioration
}

CLASS_NAMES = {
    0: "Pothole",
    1: "Longitudinal crack",
    2: "Transverse crack",
    3: "Alligator crack",
    4: "Surface deterioration",
}


def voc_to_yolo_bbox(xmin, ymin, xmax, ymax, img_w, img_h):
    """
    Convert Pascal VOC absolute pixel coordinates to normalized YOLO format.
    Validates, clamps, and calculates (x_center, y_center, width, height) in [0, 1].
    Returns (x_center, y_center, w, h) or None if box is degenerate.
    """
    if img_w <= 0 or img_h <= 0:
        return None

    # Clamp coordinates to image boundaries
    clamped_xmin = max(0.0, min(float(xmin), float(img_w)))
    clamped_xmax = max(0.0, min(float(xmax), float(img_w)))
    clamped_ymin = max(0.0, min(float(ymin), float(img_h)))
    clamped_ymax = max(0.0, min(float(ymax), float(img_h)))

    # Degenerate bounding box check
    box_w = clamped_xmax - clamped_xmin
    box_h = clamped_ymax - clamped_ymin
    if box_w <= 1e-4 or box_h <= 1e-4:
        return None

    # Calculate normalized center and dimensions
    x_center = round(((clamped_xmin + clamped_xmax) / 2.0) / float(img_w), 6)
    y_center = round(((clamped_ymin + clamped_ymax) / 2.0) / float(img_h), 6)
    norm_w = round(box_w / float(img_w), 6)
    norm_h = round(box_h / float(img_h), 6)

    # Final range safety check
    if not (0.0 <= x_center <= 1.0 and 0.0 <= y_center <= 1.0):
        return None
    if not (0.0 < norm_w <= 1.0 and 0.0 < norm_h <= 1.0):
        return None

    return x_center, y_center, norm_w, norm_h


def parse_voc_xml(xml_path, default_w=720, default_h=720):
    """
    Parse a single Pascal VOC XML file.
    Extracts image width/height and objects mapped to RoadWise AI classes.
    Returns (img_w, img_h, list_of_yolo_boxes).
    """
    if not os.path.isfile(xml_path):
        return default_w, default_h, []

    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Read image dimensions from XML if available
    size_elem = root.find("size")
    if size_elem is not None:
        try:
            img_w = int(size_elem.find("width").text)
            img_h = int(size_elem.find("height").text)
        except (AttributeError, ValueError, TypeError):
            img_w, img_h = default_w, default_h
    else:
        img_w, img_h = default_w, default_h

    if img_w <= 0:
        img_w = default_w
    if img_h <= 0:
        img_h = default_h

    yolo_boxes = []
    for obj in root.findall("object"):
        name_elem = obj.find("name")
        if name_elem is None or not name_elem.text:
            continue
        raw_name = name_elem.text.strip()
        if raw_name not in CLASS_MAP:
            continue

        class_id = CLASS_MAP[raw_name]
        bnd = obj.find("bndbox")
        if bnd is None:
            continue

        try:
            xmin = float(bnd.find("xmin").text)
            ymin = float(bnd.find("ymin").text)
            xmax = float(bnd.find("xmax").text)
            ymax = float(bnd.find("ymax").text)
        except (AttributeError, ValueError, TypeError):
            continue

        converted = voc_to_yolo_bbox(xmin, ymin, xmax, ymax, img_w, img_h)
        if converted is not None:
            xc, yc, w, h = converted
            yolo_boxes.append((class_id, xc, yc, w, h))

    return img_w, img_h, yolo_boxes


def prepare_dataset(
    dataset_dir,
    output_dir,
    val_ratio=0.2,
    pilot_size=None,
    seed=42,
    copy_images=True,
):
    """
    Convert RDD2022 India training dataset into YOLO layout:
      output_dir/
        images/train/, images/val/
        labels/train/, labels/val/
        rdd2022.yaml
    """
    train_images_dir = os.path.join(dataset_dir, "train", "images")
    train_xmls_dir = os.path.join(dataset_dir, "train", "annotations", "xmls")

    if not os.path.isdir(train_images_dir):
        raise FileNotFoundError(f"Images folder not found: {train_images_dir}")
    if not os.path.isdir(train_xmls_dir):
        raise FileNotFoundError(f"Annotations folder not found: {train_xmls_dir}")

    # Gather matching image and XML stems
    all_images = [f for f in os.listdir(train_images_dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
    all_xmls = [f for f in os.listdir(train_xmls_dir) if f.lower().endswith(".xml")]

    xml_stem_map = {os.path.splitext(f)[0]: f for f in all_xmls}
    matched_pairs = []
    for img_file in all_images:
        stem = os.path.splitext(img_file)[0]
        if stem in xml_stem_map:
            matched_pairs.append((img_file, xml_stem_map[stem]))

    matched_pairs.sort(key=lambda p: p[0])
    rng = random.Random(seed)
    rng.shuffle(matched_pairs)

    # Subsample if a pilot size is requested
    if pilot_size and pilot_size > 0 and pilot_size < len(matched_pairs):
        # Prioritize pairs with objects so the pilot contains diverse damage examples
        pairs_with_objects = []
        pairs_without_objects = []
        for img_f, xml_f in matched_pairs:
            xml_p = os.path.join(train_xmls_dir, xml_f)
            _, _, boxes = parse_voc_xml(xml_p)
            if boxes:
                pairs_with_objects.append((img_f, xml_f))
            else:
                pairs_without_objects.append((img_f, xml_f))

        # 80% damaged, 20% background in pilot for rich learning signal
        num_damaged = min(len(pairs_with_objects), int(pilot_size * 0.85))
        num_bg = min(len(pairs_without_objects), pilot_size - num_damaged)
        selected_pairs = pairs_with_objects[:num_damaged] + pairs_without_objects[:num_bg]
        rng.shuffle(selected_pairs)
        matched_pairs = selected_pairs[:pilot_size]

    total_count = len(matched_pairs)
    val_count = max(1, int(total_count * val_ratio))
    train_count = total_count - val_count

    train_pairs = matched_pairs[:train_count]
    val_pairs = matched_pairs[train_count:]

    # Prepare directories
    for split in ("train", "val"):
        os.makedirs(os.path.join(output_dir, "images", split), exist_ok=True)
        os.makedirs(os.path.join(output_dir, "labels", split), exist_ok=True)

    stats = {
        "total_images": total_count,
        "train_images": len(train_pairs),
        "val_images": len(val_pairs),
        "class_counts": Counter(),
        "total_boxes": 0,
    }

    # Process each split
    for split, pairs in [("train", train_pairs), ("val", val_pairs)]:
        img_out_dir = os.path.join(output_dir, "images", split)
        lbl_out_dir = os.path.join(output_dir, "labels", split)

        for img_f, xml_f in pairs:
            src_img_p = os.path.join(train_images_dir, img_f)
            src_xml_p = os.path.join(train_xmls_dir, xml_f)

            _, _, boxes = parse_voc_xml(src_xml_p)

            # Copy image
            if copy_images:
                dst_img_p = os.path.join(img_out_dir, img_f)
                if not os.path.isfile(dst_img_p):
                    shutil.copy2(src_img_p, dst_img_p)

            # Write YOLO label file (.txt)
            stem = os.path.splitext(img_f)[0]
            dst_lbl_p = os.path.join(lbl_out_dir, f"{stem}.txt")
            with open(dst_lbl_p, "w", encoding="utf-8") as fp:
                for cid, xc, yc, w, h in boxes:
                    fp.write(f"{cid} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")
                    stats["class_counts"][cid] += 1
                    stats["total_boxes"] += 1

    # Write YOLO dataset configuration YAML
    yaml_path = os.path.join(output_dir, "rdd2022.yaml")
    abs_out_path = os.path.abspath(output_dir).replace("\\", "/")
    yaml_content = f"""# RoadWise AI - RDD2022 YOLO Dataset Configuration
path: {abs_out_path}
train: images/train
val: images/val

names:
  0: Pothole
  1: Longitudinal crack
  2: Transverse crack
  3: Alligator crack
  4: Surface deterioration
"""
    with open(yaml_path, "w", encoding="utf-8") as fp:
        fp.write(yaml_content)

    return stats, yaml_path


def main():
    parser = argparse.ArgumentParser(description="Convert RDD2022 Pascal VOC dataset to YOLO format")
    default_dataset = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "RDD2022", "India", "India"))
    default_output = os.path.abspath(os.path.join(os.path.dirname(__file__), "datasets", "rdd2022_yolo"))

    parser.add_argument("--dataset-dir", default=default_dataset, help="Path to RDD2022 India folder")
    parser.add_argument("--output-dir", default=default_output, help="Output folder for YOLO dataset")
    parser.add_argument("--val-ratio", type=float, default=0.2, help="Validation split ratio (default: 0.2)")
    parser.add_argument("--pilot-size", type=int, default=None, help="Prepare a small pilot subset (e.g. 200)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducible split")

    args = parser.parse_args()

    print("============================================================")
    print("RoadWise AI: Preparing RDD2022 YOLO Dataset")
    print("============================================================")
    print(f"Source Dataset: {args.dataset_dir}")
    print(f"Output Directory: {args.output_dir}")
    print(f"Validation Ratio: {args.val_ratio} (Seed: {args.seed})")
    if args.pilot_size:
        print(f"Mode: Small Pilot Dataset ({args.pilot_size} images)")
    else:
        print("Mode: Full Dataset Conversion (7,706 images)")

    stats, yaml_p = prepare_dataset(
        dataset_dir=args.dataset_dir,
        output_dir=args.output_dir,
        val_ratio=args.val_ratio,
        pilot_size=args.pilot_size,
        seed=args.seed,
    )

    print("\nDataset Preparation Completed:")
    print(f"  Total Images Processed: {stats['total_images']}")
    print(f"  Train Set: {stats['train_images']} images")
    print(f"  Validation Set: {stats['val_images']} images")
    print(f"  Total Bounding Boxes: {stats['total_boxes']}")
    print("  Class Distribution:")
    for cid in range(5):
        cname = CLASS_NAMES[cid]
        cnt = stats["class_counts"][cid]
        print(f"    Class {cid} ({cname}): {cnt}")
    print(f"\nYOLO YAML Config written to: {yaml_p}")


if __name__ == "__main__":
    main()
