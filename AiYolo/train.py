"""Fine-tune YOLOv8 on a reviewed, grouped defect dataset."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="0")
    args = parser.parse_args()
    if args.epochs < 1 or args.batch < 1 or args.imgsz < 64 or args.imgsz % 32:
        parser.error("Use positive epochs/batch and an image size divisible by 32.")
    data = args.data.resolve()
    if not data.is_file() or not (data.parent / "summary.json").is_file():
        parser.error("Prepare the dataset with prepare-dataset.mjs first.")
    summary = json.loads((data.parent / "summary.json").read_text(encoding="utf-8"))
    codes = [c["code"] for c in json.loads((ROOT / "classes.json").read_text(encoding="utf-8"))]
    if summary["classes"] != codes:
        parser.error("Dataset classes do not match the backend contract.")
    weights = ROOT / "weights" / "pretrained" / "yolov8n.pt"
    if not weights.is_file():
        parser.error("Pretrained YOLOv8n weights are missing. Run Setup-Yolo.ps1 first.")

    import torch
    from ultralytics import YOLO
    from check_dataset import check_dataset

    check_dataset(data)

    if args.device != "cpu" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable. Fix the GPU environment or explicitly select --device cpu.")
    print("Training device:", torch.cuda.get_device_name(0) if args.device != "cpu" else "CPU")
    model = YOLO(str(weights))
    name = "defects-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    model.train(
        data=str(data), epochs=args.epochs, imgsz=args.imgsz, batch=args.batch,
        device=args.device, workers=0, project=str(ROOT / "runs"), name=name,
        seed=42, deterministic=True, patience=20, cache=False,
        fliplr=0.0, flipud=0.0, degrees=5.0, mosaic=0.5, close_mosaic=10,
    )
    best = Path(model.trainer.best).resolve()
    if not best.is_file():
        raise RuntimeError("Training ended without a best.pt checkpoint.")
    trained = YOLO(str(best))
    if [trained.names[i] for i in range(len(trained.names))] != codes:
        raise RuntimeError("Checkpoint has unexpected classes.")
    metrics = trained.val(data=str(data), split="val", imgsz=args.imgsz, device=args.device, workers=0)
    metadata = {
        "weights": str(best), "sha256": hashlib.sha256(best.read_bytes()).hexdigest(),
        "classes": codes, "dataset": str(data), "validatedSplit": "val",
        "map50": float(metrics.box.map50), "map50_95": float(metrics.box.map),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    # Keep every run. This pointer selects the latest successfully trained model.
    temporary = ROOT / "model.local.json.tmp"
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(ROOT / "model.local.json")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print("Training and validation finished. The test split was not used. Review per-class precision/recall and error examples before deployment.")


if __name__ == "__main__":
    main()
