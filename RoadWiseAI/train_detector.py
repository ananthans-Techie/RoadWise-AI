"""
RoadWise AI - AI-Assisted Road Damage Model Training Script (Stage 4).
Trains a lightweight YOLOv8n detector on RDD2022 India data.
Saves the resulting model to models/road_damage_yolov8n.pt (outside static/uploads).
"""
import os
import sys
import shutil
import argparse


def detect_hardware():
    """Detect available hardware (GPU vs CPU) and safe hyperparameters."""
    import torch

    has_cuda = torch.cuda.is_available()
    device = "0" if has_cuda else "cpu"
    gpu_name = torch.cuda.get_device_name(0) if has_cuda else "None (CPU)"
    vram_gb = (
        round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 2)
        if has_cuda
        else 0.0
    )

    # Safe settings for 4GB RTX 3050 or CPU
    batch_size = 8 if has_cuda else 4
    num_workers = 2 if os.name == "nt" else 4

    return {
        "has_cuda": has_cuda,
        "device": device,
        "gpu_name": gpu_name,
        "vram_gb": vram_gb,
        "safe_batch_size": batch_size,
        "safe_workers": num_workers,
    }


def estimate_training_cost(num_images, epochs, has_cuda):
    """Estimate training runtime and disk usage."""
    if has_cuda:
        # RTX 3050 Laptop estimate: ~0.08 - 0.12s per image-epoch at imgsz=640
        time_per_epoch_sec = max(10, int(num_images * 0.10))
    else:
        # Intel i5-13420H CPU estimate: ~0.8 - 1.2s per image-epoch
        time_per_epoch_sec = max(30, int(num_images * 1.0))

    total_sec = time_per_epoch_sec * epochs
    mins = total_sec // 60
    secs = total_sec % 60
    runtime_str = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"

    # Disk use: model checkpoint (~6.5 MB), optimizer state (~13 MB), run logs (~10 MB)
    est_disk_mb = 35 + (num_images * 0.01)

    return runtime_str, round(est_disk_mb, 1)


def train_model(
    data_yaml,
    model_name="yolov8n.pt",
    epochs=5,
    batch=None,
    imgsz=640,
    output_dir="models",
    dry_run=False,
):
    """Run YOLO training on prepared dataset."""
    from ultralytics import YOLO

    hw = detect_hardware()
    batch_size = batch or hw["safe_batch_size"]
    os.makedirs(output_dir, exist_ok=True)

    # Count images in dataset yaml if exists
    num_train_images = 0
    if os.path.isfile(data_yaml):
        with open(data_yaml, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("train:"):
                    rel_p = line.split(":", 1)[1].strip()
                    parent_d = os.path.dirname(data_yaml)
                    train_d = os.path.join(parent_d, rel_p)
                    if os.path.isdir(train_d):
                        num_train_images = len(os.listdir(train_d))
                    break

    est_runtime, est_disk = estimate_training_cost(
        num_train_images or 200, epochs, hw["has_cuda"]
    )

    print("============================================================")
    print("RoadWise AI: Stage 4 YOLO Road Damage Training")
    print("============================================================")
    print(f"Hardware: {'GPU - ' + hw['gpu_name'] if hw['has_cuda'] else 'CPU'}")
    if hw["has_cuda"]:
        print(f"VRAM: {hw['vram_gb']} GB")
    print(f"Target Device: {hw['device']}")
    print(f"Base Pretrained Model: {model_name} (Ultralytics Nano)")
    print(f"Dataset Config: {data_yaml}")
    print(f"Training Images: {num_train_images}")
    print(f"Epochs: {epochs} | Batch Size: {batch_size} | Image Size: {imgsz}")
    print(f"Expected Runtime: ~{est_runtime}")
    print(f"Expected Disk Usage: ~{est_disk} MB")
    print(f"Final Weights Target: {os.path.join(output_dir, 'road_damage_yolov8n.pt')}")
    print("============================================================")

    if dry_run:
        print("[DRY RUN] Hardware checks passed. Exiting without training.")
        return None

    if not os.path.isfile(data_yaml):
        print(f"ERROR: Dataset configuration not found: {data_yaml}")
        print("Run 'python prepare_rdd_dataset.py --pilot-size 200' first to prepare the dataset.")
        sys.exit(1)

    print("\nStarting YOLO training session...")
    model = YOLO(model_name)

    results = model.train(
        data=data_yaml,
        epochs=epochs,
        batch=batch_size,
        imgsz=imgsz,
        device=hw["device"],
        workers=hw["safe_workers"],
        project=os.path.abspath(os.path.join(output_dir, "..", "runs", "train")),
        name="roadwise_ai",
        exist_ok=True,
        verbose=True,
    )

    # Locate and copy best model weights
    save_dir = getattr(results, "save_dir", None) or os.path.join(
        output_dir, "..", "runs", "train", "roadwise_ai"
    )
    best_weights = os.path.join(save_dir, "weights", "best.pt")
    last_weights = os.path.join(save_dir, "weights", "last.pt")

    final_model_path = os.path.abspath(os.path.join(output_dir, "road_damage_yolov8n.pt"))

    source_weights = best_weights if os.path.isfile(best_weights) else last_weights
    if os.path.isfile(source_weights):
        shutil.copy2(source_weights, final_model_path)
        print("\n============================================================")
        print("TRAINING SUCCESSFUL!")
        print(f"Model saved to: {final_model_path}")
        print("This model is now ready for Flask inference (/api/detect).")
        print("============================================================")
        return final_model_path
    else:
        print(f"Warning: Could not find best.pt in {save_dir}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Train lightweight YOLOv8n detector on RDD2022 India data")
    default_yaml = os.path.abspath(os.path.join(os.path.dirname(__file__), "datasets", "rdd2022_yolo", "rdd2022.yaml"))
    default_out = os.path.abspath(os.path.join(os.path.dirname(__file__), "models"))

    parser.add_argument("--data", default=default_yaml, help="Path to rdd2022.yaml dataset configuration")
    parser.add_argument("--model", default="yolov8n.pt", help="Base model architecture (default: yolov8n.pt)")
    parser.add_argument("--epochs", type=int, default=5, help="Number of training epochs (default: 5 for pilot)")
    parser.add_argument("--batch", type=int, default=None, help="Batch size (default: auto 8 for GPU / 4 for CPU)")
    parser.add_argument("--imgsz", type=int, default=640, help="Image resolution for training (default: 640)")
    parser.add_argument("--output-dir", default=default_out, help="Directory to save final model weights")
    parser.add_argument("--dry-run", action="store_true", help="Report hardware and runtime estimates without training")

    args = parser.parse_args()

    train_model(
        data_yaml=args.data,
        model_name=args.model,
        epochs=args.epochs,
        batch=args.batch,
        imgsz=args.imgsz,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
