"""Split manually annotated YOLO data and train a defect-only detector."""

from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path

import yaml
from ultralytics import YOLO

CLASSES = [
    "corrosion",
    "leak",
    "crack",
    "insulation_damage",
    "overheating",
    "contamination",
    "mechanical_damage",
    "missing_part",
    "ice",
    "wear",
]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Обучить YOLO только на дефектах из каталога RepairHack.")
    parser.add_argument("--data", type=Path, default=Path("defect_dataset"), help="Данные, созданные annotate_defects.py.")
    parser.add_argument("--base-model", default="yolo11n.pt", help="Стартовые веса YOLO detection.")
    parser.add_argument("--epochs", type=int, default=100, help="Число эпох обучения.")
    parser.add_argument("--image-size", type=int, default=640, help="Размер изображения.")
    parser.add_argument("--device", default=None, help="cpu, mps или cuda:0.")
    parser.add_argument("--val-fraction", type=float, default=0.2, help="Доля валидационных изображений.")
    parser.add_argument("--seed", type=int, default=42, help="Seed разбиения.")
    args = parser.parse_args()

    images_dir = args.data / "images"
    labels_dir = args.data / "labels"
    if not images_dir.is_dir() or not labels_dir.is_dir():
        parser.error("Не найдены папки images/labels. Сначала запустите annotate_defects.py и сохраните разметку.")
    if not 0.05 <= args.val_fraction <= 0.5:
        parser.error("--val-fraction должен быть от 0.05 до 0.5")

    images = sorted(
        path for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        and labels_dir.joinpath(f"{path.stem}.txt").exists()
    )
    if len(images) < 10:
        parser.error(f"Для разбиения нужно хотя бы 10 размеченных изображений; найдено {len(images)}.")

    counts = [0] * len(CLASSES)
    empty_labels = 0
    for image_path in images:
        label_path = labels_dir / f"{image_path.stem}.txt"
        lines = [line.split() for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            empty_labels += 1
        for row in lines:
            if len(row) != 5:
                parser.error(f"Некорректная YOLO-разметка: {label_path}")
            class_id = int(row[0])
            if not 0 <= class_id < len(CLASSES):
                parser.error(f"Класс {class_id} не входит в каталог: {label_path}")
            counts[class_id] += 1

    present = [f"{CLASSES[i]}: {count}" for i, count in enumerate(counts) if count]
    print(f"Размечено изображений: {len(images)}; пустых (без видимого дефекта): {empty_labels}")
    print("Число рамок по классам: " + (", ".join(present) if present else "нет"))
    missing = [CLASSES[i] for i, count in enumerate(counts) if count == 0]
    if missing:
        print("ВНИМАНИЕ: нет ни одной разметки для классов: " + ", ".join(missing))
    rare = [CLASSES[i] for i, count in enumerate(counts) if 0 < count < 10]
    if rare:
        print("ВНИМАНИЕ: мало рамок для классов " + ", ".join(rare) + ". Модель, вероятно, будет ненадёжной.")
    if not any(counts):
        parser.error("В сохранённой разметке нет ни одной рамки дефекта.")

    randomizer = random.Random(args.seed)
    randomizer.shuffle(images)
    val_count = max(1, round(len(images) * args.val_fraction))
    if len(images) - val_count < 1:
        parser.error("Недостаточно данных для train/validation разделения.")
    split_root = args.data / "split"
    if split_root.exists():
        shutil.rmtree(split_root)
    for subset, subset_images in (("train", images[val_count:]), ("val", images[:val_count])):
        target_images = split_root / "images" / subset
        target_labels = split_root / "labels" / subset
        target_images.mkdir(parents=True, exist_ok=True)
        target_labels.mkdir(parents=True, exist_ok=True)
        for image_path in subset_images:
            shutil.copy2(image_path, target_images / image_path.name)
            shutil.copy2(labels_dir / f"{image_path.stem}.txt", target_labels / f"{image_path.stem}.txt")

    data_yaml = split_root / "data.yaml"
    data_config = {
        "path": split_root.resolve().as_posix(),
        "train": "images/train",
        "val": "images/val",
        "names": {index: name for index, name in enumerate(CLASSES)},
    }
    data_yaml.write_text(yaml.safe_dump(data_config, sort_keys=False, allow_unicode=True), encoding="utf-8")

    print("Загружаю базовые веса и начинаю обучение. На Mac можно использовать --device mps, если поддерживается.")
    model = YOLO(args.base_model)
    train_args = {
        "data": str(data_yaml),
        "epochs": args.epochs,
        "imgsz": args.image_size,
        "project": "runs/repairhack",
        "name": "defects",
        "exist_ok": True,
        "patience": 20,
        "plots": True,
        "workers": 0,
    }
    if args.device:
        train_args["device"] = args.device
    model.train(**train_args)
    print("Обучение завершено. Лучшие веса обычно находятся в runs/repairhack/defects/weights/best.pt")
    print("Проверьте результаты на отдельных фото и реальной камере; не используйте как автоматическое заключение о безопасности.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
