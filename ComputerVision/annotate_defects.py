"""OpenCV-based manual bounding-box annotation tool for RepairHack images."""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

CLASSES = [
    "corrosion", "leak", "crack", "insulation_damage", "overheating",
    "contamination", "mechanical_damage", "missing_part", "ice", "wear",
]
CLASS_TITLES = [
    "Коррозия / ржавчина", "Течь", "Трещина", "Повреждение изоляции",
    "Перегрев / обугливание", "Загрязнение / отложения", "Механическое повреждение",
    "Отсутствует деталь", "Обмерзание", "Износ / эрозия",
]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
COLORS = [(255, 90, 75), (20, 165, 255), (0, 225, 255), (210, 80, 255),
          (80, 70, 255), (0, 220, 210), (255, 145, 30), (180, 90, 220),
          (255, 230, 0), (100, 200, 80)]


def stable_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    suffix = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:8]
    stem = "_".join(path.relative_to(root).with_suffix("").parts)
    safe_stem = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in stem)
    return f"{safe_stem}_{suffix}{path.suffix.lower()}"


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for font_path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        if Path(font_path).exists():
            try:
                return ImageFont.truetype(font_path, size)
            except OSError:
                continue
    return ImageFont.load_default()


class Annotator:
    def __init__(self, root: Path, output: Path, max_width: int, max_height: int) -> None:
        self.root = root
        self.output = output
        self.images_dir = output / "images"
        self.labels_dir = output / "labels"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.labels_dir.mkdir(parents=True, exist_ok=True)
        equipment_folders = ("Запорная арматура", "Насосные агрегаты", "Трубопровод", "ЭД")
        selected_roots = [root / name for name in equipment_folders if (root / name).is_dir()]
        if not selected_roots:
            selected_roots = [root]
        self.files = sorted(
            path for selected_root in selected_roots for path in selected_root.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            and output not in path.parents
        )
        self.files, self.duplicate_count = self.remove_near_duplicates(self.files)
        if not self.files:
            raise FileNotFoundError(f"В папке {root} не найдены изображения")
        self.index = 0
        self.class_id = 0
        self.annotations: list[tuple[int, float, float, float, float]] = []
        self.drag_start: tuple[int, int] | None = None
        self.drag_end: tuple[int, int] | None = None
        self.scale = 1.0
        self.offset_x = self.offset_y = 0
        self.display_width = self.display_height = 1
        self.original_width = self.original_height = 1
        self.max_width, self.max_height = max_width, max_height
        self.font = load_font(20)
        self.small_font = load_font(16)
        self.status = f"Найдено фото: {len(self.files)}; пропущено похожих дублей: {self.duplicate_count}."
        self.load_annotations()

    @staticmethod
    def perceptual_hash(path: Path) -> int | None:
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            return None
        reduced = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
        coefficients = cv2.dct(reduced)[:8, :8]
        values = coefficients.flatten()[1:]
        median = float(np.median(values))
        result = 0
        for bit in values > median:
            result = (result << 1) | int(bit)
        return result

    @classmethod
    def remove_near_duplicates(cls, files: list[Path]) -> tuple[list[Path], int]:
        kept: list[Path] = []
        hashes: list[int] = []
        duplicate_count = 0
        for path in files:
            image_hash = cls.perceptual_hash(path)
            if image_hash is None:
                continue
            if any((image_hash ^ previous).bit_count() <= 2 for previous in hashes):
                duplicate_count += 1
                continue
            kept.append(path)
            hashes.append(image_hash)
        return kept, duplicate_count

    def label_path(self, image_path: Path) -> Path:
        return self.labels_dir / f"{Path(stable_name(image_path, self.root)).stem}.txt"

    def load_annotations(self) -> None:
        self.annotations = []
        label_path = self.label_path(self.files[self.index])
        if not label_path.exists():
            return
        try:
            for line in label_path.read_text(encoding="utf-8").splitlines():
                values = line.split()
                if len(values) == 5:
                    class_id, *coords = values
                    self.annotations.append((int(class_id), *(float(value) for value in coords)))
        except (ValueError, OSError):
            self.status = f"Не удалось прочитать разметку: {label_path.name}"

    def set_class(self, class_id: int) -> None:
        self.class_id = class_id
        self.status = f"Класс выбран: {CLASS_TITLES[class_id]}. Обведите дефект мышью."

    def image_to_normalized(self, x: int, y: int) -> tuple[float, float]:
        image_x = min(max(x - self.offset_x, 0), self.display_width)
        image_y = min(max(y - self.offset_y, 0), self.display_height)
        return image_x / self.display_width, image_y / self.display_height

    def mouse(self, event: int, x: int, y: int, _flags: int, _param: Any) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            if self.offset_x <= x <= self.offset_x + self.display_width and self.offset_y <= y <= self.offset_y + self.display_height:
                self.drag_start = (x, y)
                self.drag_end = (x, y)
        elif event == cv2.EVENT_MOUSEMOVE and self.drag_start is not None:
            self.drag_end = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and self.drag_start is not None:
            x1, y1 = self.image_to_normalized(*self.drag_start)
            x2, y2 = self.image_to_normalized(x, y)
            self.drag_start = self.drag_end = None
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            if right - left >= 0.008 and bottom - top >= 0.008:
                self.annotations.append((self.class_id, (left + right) / 2, (top + bottom) / 2, right - left, bottom - top))
                self.status = f"Добавлена рамка: {CLASS_TITLES[self.class_id]}. S — сохранить, U — отменить последнюю."

    def render(self) -> np.ndarray:
        path = self.files[self.index]
        bgr = cv2.imread(str(path))
        if bgr is None:
            raise OSError(f"Не удалось открыть: {path}")
        self.original_height, self.original_width = bgr.shape[:2]
        self.scale = min(self.max_width / self.original_width, self.max_height / self.original_height, 1.0)
        self.display_width = max(1, round(self.original_width * self.scale))
        self.display_height = max(1, round(self.original_height * self.scale))
        canvas = np.zeros((self.max_height, self.max_width, 3), dtype=np.uint8)
        self.offset_x = (self.max_width - self.display_width) // 2
        self.offset_y = (self.max_height - self.display_height) // 2
        resized = cv2.resize(bgr, (self.display_width, self.display_height), interpolation=cv2.INTER_AREA)
        canvas[self.offset_y:self.offset_y + self.display_height, self.offset_x:self.offset_x + self.display_width] = resized

        overlay = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(overlay)
        for class_id, cx, cy, width, height in self.annotations:
            x1 = self.offset_x + (cx - width / 2) * self.display_width
            y1 = self.offset_y + (cy - height / 2) * self.display_height
            x2 = self.offset_x + (cx + width / 2) * self.display_width
            y2 = self.offset_y + (cy + height / 2) * self.display_height
            color = COLORS[class_id]
            draw.rectangle((x1, y1, x2, y2), outline=(color[2], color[1], color[0]), width=3)
            draw.text((x1 + 3, y1 + 2), CLASS_TITLES[class_id], font=self.small_font, fill=(color[2], color[1], color[0]), stroke_width=2, stroke_fill=(10, 10, 10))
        if self.drag_start and self.drag_end:
            color = COLORS[self.class_id]
            draw.rectangle((*self.drag_start, *self.drag_end), outline=(color[2], color[1], color[0]), width=3)

        panel_top = self.max_height - 94
        draw.rectangle((0, panel_top, self.max_width, self.max_height), fill=(12, 21, 37))
        relative = self.files[self.index].relative_to(self.root).as_posix()
        lines = [
            f"Фото {self.index + 1}/{len(self.files)}: {relative}",
            f"Класс: {self.class_id + 1 if self.class_id < 9 else 0} — {CLASS_TITLES[self.class_id]}  |  Рамок: {len(self.annotations)}",
            self.status,
            "Клавиши: 1-9/0 класс · S сохранить · N пропустить · ←/→ фото · U отменить · Q выход",
        ]
        for offset, text in enumerate(lines):
            draw.text((10, panel_top + 4 + offset * 21), text, font=self.small_font, fill=(235, 240, 248))
        return cv2.cvtColor(np.asarray(overlay), cv2.COLOR_RGB2BGR)

    def save(self) -> None:
        source = self.files[self.index]
        destination = self.images_dir / stable_name(source, self.root)
        try:
            if not destination.exists():
                shutil.copy2(source, destination)
            rows = [f"{cid} {cx:.6f} {cy:.6f} {width:.6f} {height:.6f}" for cid, cx, cy, width, height in self.annotations]
            self.label_path(source).write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
            self.status = f"Сохранено: {destination.name}"
        except OSError as exc:
            self.status = f"Ошибка сохранения: {exc}"
            return
        self.move(1)

    def move(self, delta: int) -> None:
        next_index = self.index + delta
        if 0 <= next_index < len(self.files):
            self.index = next_index
            self.drag_start = self.drag_end = None
            self.load_annotations()
            self.status = "Фото с пустой разметкой при сохранении считается без видимого дефекта."
        else:
            self.status = "Это последнее фото." if delta > 0 else "Это первое фото."

    def handle_key(self, key: int) -> bool:
        if ord("1") <= key <= ord("9"):
            self.set_class(key - ord("1"))
        elif key == ord("0"):
            self.set_class(9)
        elif key in (ord("s"), ord("S")):
            self.save()
        elif key in (ord("n"), ord("N")):
            self.move(1)
        elif key in (ord("u"), ord("U"), 8, 127):
            if self.annotations:
                self.annotations.pop()
                self.status = "Последняя рамка отменена."
        elif key in (2424832, 63234):
            self.move(-1)
        elif key in (2555904, 63235):
            self.move(1)
        elif key in (ord("q"), ord("Q"), 27):
            return False
        return True

    def run(self) -> None:
        window = "RepairHack — разметка дефектов"
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window, self.max_width, self.max_height)
        cv2.setMouseCallback(window, self.mouse)
        running = True
        while running:
            cv2.imshow(window, self.render())
            running = self.handle_key(cv2.waitKeyEx(20))
        cv2.destroyAllWindows()


def main() -> int:
    parser = argparse.ArgumentParser(description="Разметка фото мышью для обучения defect-only YOLO.")
    parser.add_argument("--images", type=Path, default=Path("."), help="Корневая папка изображений.")
    parser.add_argument("--output", type=Path, default=Path("defect_dataset"), help="Папка создаваемого датасета.")
    parser.add_argument("--width", type=int, default=1400, help="Ширина окна разметчика.")
    parser.add_argument("--height", type=int, default=900, help="Высота окна разметчика.")
    args = parser.parse_args()
    root = args.images.resolve()
    try:
        Annotator(root, args.output.resolve(), args.width, args.height).run()
    except (OSError, cv2.error) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
