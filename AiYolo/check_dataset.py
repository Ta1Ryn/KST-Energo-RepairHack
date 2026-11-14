import hashlib
import json
from pathlib import Path


def check_dataset(data_file):
    from PIL import Image
    import yaml
    root = Path(data_file).resolve().parent
    config = yaml.safe_load(Path(data_file).read_text(encoding="utf-8"))
    if Path(config["path"]).resolve() != root:
        raise ValueError("Dataset root differs from the prepared directory")
    expected = {0: "oilLeak", 1: "displacement", 2: "corrosion", 3: "geometryDeformation"}
    if config.get("names") != expected:
        raise ValueError("Dataset class ordering differs from the backend contract")
    provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    used_groups, used_hashes = set(), set()
    for split in ("train", "val", "test"):
        if config.get(split) != f"images/{split}":
            raise ValueError("Dataset split paths differ from the prepared structure")
        records = provenance[split]
        groups = {r["group"] for r in records}
        if groups & used_groups:
            raise ValueError("Capture groups overlap across splits")
        used_groups.update(groups)
        ids = set()
        for record in records:
            filename = record["file"]
            if Path(filename).name != filename:
                raise ValueError("Invalid manifest filename")
            image_file = root / "images" / split / filename
            digest = hashlib.sha256(image_file.read_bytes()).hexdigest()
            if digest != record["sha256"] or digest in used_hashes:
                raise ValueError("Images changed after splitting or contain duplicates")
            used_hashes.add(digest)
            with Image.open(image_file) as image:
                if image.format != "JPEG" or min(image.size) < 64 or image.width * image.height > 25_000_000:
                    raise ValueError(f"Invalid training image: {filename}")
                if image.getexif().get(274, 1) != 1:
                    raise ValueError("Training images must have normalized orientation")
                image.verify()
            labels = (root / "labels" / split / (Path(filename).stem + ".txt")).read_text(encoding="utf-8")
            for line in labels.splitlines():
                if not line.strip():
                    continue
                row = [float(v) for v in line.split()]
                if len(row) != 5:
                    raise ValueError("Expected five values per box")
                class_id, x, y, width, height = row
                if not class_id.is_integer() or int(class_id) not in range(4) or not (0 < width <= 1 and 0 < height <= 1 and width / 2 - 1e-6 <= x <= 1 - width / 2 + 1e-6 and height / 2 - 1e-6 <= y <= 1 - height / 2 + 1e-6):
                    raise ValueError("Invalid class or bounding box")
                ids.add(int(class_id))
        if ids != set(range(4)):
            raise ValueError(f"Split {split} must contain all four defect classes")
