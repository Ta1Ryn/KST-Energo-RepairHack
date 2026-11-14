import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Evaluate a selected trained checkpoint on the held-out test set.")
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--device", default="0")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    pointer = json.loads((root / "model.local.json").read_text(encoding="utf-8")) if args.weights is None else None
    weights = args.weights or Path(pointer["weights"])
    if not weights.is_file():
        parser.error("Trained weights not found")
    from check_dataset import check_dataset
    from ultralytics import YOLO
    check_dataset(args.data)
    model = YOLO(str(weights))
    result = model.val(data=str(args.data.resolve()), split="test", device=args.device, imgsz=640, workers=0)
    print({"split": "test", "map50": float(result.box.map50), "map50_95": float(result.box.map)})


if __name__ == "__main__":
    main()
