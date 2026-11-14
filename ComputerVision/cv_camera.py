"""Live camera prototype for visible industrial-defect candidates.

Hybrid pipeline:
  1) YOLO-World proposes candidate regions at a low threshold;
  2) every cue is filtered into ACCEPTED components using physical
     properties (size, shape, texture, context, brightness contrast,
     motion for smoke);
  3) YOLO proposals are verified ONLY against accepted cues;
  4) accepted cues also produce standalone detections;
  5) an equipment mask restricts everything to industrial machinery;
  6) in live mode a candidate must repeat in two consecutive passes.

Class balance:
  - smoke must be a bright, soft, vertically elongated plume that is
    brighter than its surroundings (painted panels fail this);
  - liquid must be truly smooth with its own specular highlight;
  - corrosion is detected from dense rust cores with widened rust mask;
  - a drip inside a corrosion zone keeps both boxes.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import Future, ThreadPoolExecutor
from copy import copy
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch

try:
    import cv2
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Не найдены OpenCV или Pillow. Установите зависимости из requirements.txt.", file=sys.stderr)
    raise SystemExit(2)

try:
    from ultralytics import YOLO
except ImportError:
    print("Не найден Ultralytics. Установите зависимости из requirements.txt.", file=sys.stderr)
    raise SystemExit(2)


PROMPTS = [
    ("corrosion", "rust"),
    ("corrosion", "rusty metal"),
    ("corrosion", "corrosion"),
    ("corrosion", "flaking paint"),
    ("leak", "liquid leak"),
    ("leak", "water dripping"),
    ("leak", "oil leak"),
    ("crack", "crack metal"),
    ("crack", "fracture line"),
    ("insulation_damage", "exposed wire"),
    ("insulation_damage", "damaged cable"),
    ("overheating", "burnt wire"),
    ("overheating", "smoke from machine"),
    ("overheating", "charred connector"),
    ("contamination", "dust dirt"),
    ("contamination", "dirty surface"),
    ("mechanical_damage", "bent metal"),
    ("mechanical_damage", "broken part"),
    ("mechanical_damage", "bent fins"),
    ("mechanical_damage", "broken fan blade"),
    ("mechanical_damage", "dented pipe"),
    ("missing_part", "missing component"),
    ("ice", "ice frost"),
    ("wear", "worn metal"),
    ("wear", "pitting"),
    ("wear", "eroded impeller"),
]
PROMPT_CLASS_KEYS = [item[0] for item in PROMPTS]

DEFECT_INFO = {
    "corrosion": ("подозрение на коррозию", 2,
                  "Осмотреть участок; для оценки утонения выполнить контроль толщины."),
    "leak": ("возможная течь", 3,
             "Проверить герметичность и безопасно локализовать утечку."),
    "crack": ("возможная трещина", 3,
              "Подтвердить осмотром и неразрушающим контролем."),
    "insulation_damage": ("повреждение изоляции", 3,
                          "Не касаться; поручить проверку специалисту."),
    "overheating": ("перегрев / обугливание / дым", 3,
                    "Требуется срочная проверка специалистом."),
    "contamination": ("загрязнение / отложения", 1,
                      "Проверить каналы охлаждения и очистить по регламенту."),
    "mechanical_damage": ("механическое повреждение", 2,
                          "Оценить размер и влияние на работу узла."),
    "missing_part": ("возможное отсутствие детали", 2,
                     "Сверить комплектность с паспортом оборудования."),
    "ice": ("обмерзание", 1,
            "Проверить температуру, изоляцию и режим работы."),
    "wear": ("износ / эрозия", 2,
             "Проверить узел инструментально."),
}

DEFECT_ALIASES = {
    "rust": "corrosion", "corroded": "corrosion", "oxidation": "corrosion",
    "leakage": "leak", "dripping": "leak", "seepage": "leak",
    "cracks": "crack", "fracture": "crack",
    "burning": "overheating", "burnt": "overheating", "charred": "overheating",
    "smoke": "overheating", "scorched": "overheating",
    "damaged_insulation": "insulation_damage", "exposed_wire": "insulation_damage",
    "dirt": "contamination", "dust": "contamination",
    "dent": "mechanical_damage", "missing": "missing_part",
    "erosion": "wear", "pitting": "wear", "frost": "ice",
}

SEVERITY_COLORS = {1: (255, 200, 0), 2: (0, 165, 255), 3: (50, 50, 255)}
STATUS_COLORS = {"green": (80, 220, 80), "yellow": (60, 210, 255), "red": (60, 60, 255)}

EQUIPMENT_OVERLAP = {
    "corrosion": 0.35, "leak": 0.30, "crack": 0.30, "insulation_damage": 0.25,
    "overheating": 0.20, "contamination": 0.35, "mechanical_damage": 0.35,
    "missing_part": 0.25, "ice": 0.30, "wear": 0.35,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Поиск видимых промышленных дефектов только на оборудовании."
    )
    parser.add_argument("--model", default="yolov8s-world.pt",
                        help="YOLO-World или путь к своим обученным весам.")
    parser.add_argument("--camera", type=int, default=0, help="Индекс камеры.")
    parser.add_argument("--image", nargs="*", type=Path, default=None,
                        help="Файлы или папки с фото для проверки без камеры.")
    parser.add_argument("--confidence", type=float, default=0.01,
                        help="Нижняя граница сырых предложений YOLO.")
    parser.add_argument("--image-size", type=int, default=800, help="Размер входа модели.")
    parser.add_argument("--tiles", type=int, default=2, choices=(1, 2, 3), help="Детализация 1/2/3.")
    parser.add_argument("--interval", type=float, default=0.5, help="Период анализа, сек.")
    parser.add_argument("--device", default=None, help="cpu, mps или cuda:0.")
    parser.add_argument("--output", type=Path, default=Path("inspection_output"),
                        help="Папка снимков и журнала.")
    parser.add_argument("--width", type=int, default=1280, help="Ширина захвата.")
    parser.add_argument("--height", type=int, default=720, help="Высота захвата.")
    parser.add_argument("--strictness", type=float, default=1.0,
                        help=">1 строже верификация, <1 чувствительнее.")
    parser.add_argument("--no-equipment-filter", action="store_true",
                        help="Отключить фильтр 'только на оборудовании' (отладка).")
    parser.add_argument("--no-stability", action="store_true",
                        help="Отключить требование повторной детекции в live-режиме.")
    parser.add_argument("--debug-equipment", action="store_true",
                        help="Сохранять маску оборудования (отладка).")
    args = parser.parse_args()
    if not 0.0 <= args.confidence <= 1.0:
        parser.error("--confidence должен быть 0..1")
    if args.image_size < 160:
        parser.error("--image-size должен быть >= 160")
    if args.interval < 0.1:
        parser.error("--interval должен быть >= 0.1")
    return args


def normalized_name(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                pass
    return ImageFont.load_default()


def draw_ui_text(image: Image.Image, text: str, position: tuple[int, int],
                 color: tuple[int, int, int], font: ImageFont.ImageFont) -> None:
    draw = ImageDraw.Draw(image)
    x, y = position
    rgb = (color[2], color[1], color[0])
    draw.text((x + 1, y + 1), text, font=font, fill=(5, 8, 15), stroke_width=2)
    draw.text((x, y), text, font=font, fill=rgb, stroke_width=0)


def defect_key(name: str) -> str | None:
    key = normalized_name(name)
    if key in DEFECT_INFO:
        return key
    for alias, canonical in DEFECT_ALIASES.items():
        if alias in key:
            return canonical
    return None


def make_tiles(image: Any, grid: int) -> list[tuple[Any, int, int]]:
    height, width = image.shape[:2]
    if grid == 1:
        return [(image, 0, 0)]
    tile_width = min(width, max(1, int(width / grid * 1.2)))
    tile_height = min(height, max(1, int(height / grid * 1.2)))
    starts_x = [round(i * (width - tile_width) / max(1, grid - 1)) for i in range(grid)]
    starts_y = [round(i * (height - tile_height) / max(1, grid - 1)) for i in range(grid)]
    return [(image[y:y + tile_height, x:x + tile_width], x, y) for y in starts_y for x in starts_x]


def intersection_over_union(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if not inter:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(area_a + area_b - inter, 1)


def intersection_area(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    return max(0, x2 - x1) * max(0, y2 - y1)


def box_area(box: tuple[int, int, int, int]) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def box_of(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
    return (candidate["x1"], candidate["y1"], candidate["x2"], candidate["y2"])


def match_stable(prev: list[dict[str, Any]], cur: list[dict[str, Any]],
                 iou: float = 0.25) -> list[dict[str, Any]]:
    kept = []
    for c in cur:
        if any(p["class"] == c["class"] and intersection_over_union(box_of(p), box_of(c)) >= iou
               for p in prev):
            kept.append(c)
    return kept


# ---------------------------------------------------------------------------
# Equipment mask
# ---------------------------------------------------------------------------

def compute_equipment_mask(frame: Any) -> np.ndarray:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gf = gray.astype(np.float32)

    gx = cv2.Sobel(gf, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gf, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    edge_density = cv2.blur(grad, (25, 25))

    mean = cv2.blur(gf, (25, 25))
    sq_mean = cv2.blur(gf * gf, (25, 25))
    stddev = np.sqrt(np.maximum(sq_mean - mean * mean, 0.0))

    grey_metal = (s <= 70) & (v >= 25) & (v <= 200)
    blue_paint = (h >= 90) & (h <= 130) & (s >= 30) & (s <= 180) & (v >= 30) & (v <= 200)
    dark_metal = (v >= 20) & (v <= 130)
    metal_color = (grey_metal | blue_paint | dark_metal).astype(np.uint8)
    textured = ((edge_density > 18) & (stddev > 14)).astype(np.uint8)

    edges = cv2.Canny(gray, 60, 160)
    height, width = gray.shape
    min_r = max(8, min(height, width) // 60)
    max_r = max(min_r + 1, min(height, width) // 6)
    circles = cv2.HoughCircles(edges, cv2.HOUGH_GRADIENT, dp=1.2,
                               minDist=min_r, param1=160, param2=28,
                               minRadius=min_r, maxRadius=max_r)
    circle_mask = np.zeros_like(gray, dtype=np.uint8)
    if circles is not None:
        for cx, cy, r in np.round(circles[0]).astype(int):
            cv2.circle(circle_mask, (cx, cy), int(r * 1.4), 255, -1)

    equipment = ((metal_color & textured) | (circle_mask > 0)).astype(np.uint8) * 255
    equipment = cv2.morphologyEx(equipment, cv2.MORPH_CLOSE,
                                 cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (35, 35)))
    equipment = cv2.morphologyEx(equipment, cv2.MORPH_OPEN,
                                 cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))

    frame_area = max(1, height * width)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(equipment, 8)
    clean = np.zeros_like(equipment)
    for i in range(1, num):
        if stats[i, cv2.CC_STAT_AREA] >= 0.01 * frame_area:
            clean[labels == i] = 255
    return cv2.dilate(clean, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (45, 45)))


def on_equipment(equipment_mask: np.ndarray | None, key: str,
                 x1: int, y1: int, x2: int, y2: int) -> bool:
    if equipment_mask is None:
        return True
    region = equipment_mask[y1:y2, x1:x2]
    if region.size == 0:
        return False
    return float((region > 0).mean()) >= EQUIPMENT_OVERLAP.get(key, 0.30)


# ---------------------------------------------------------------------------
# Cue extraction with physical acceptance rules
# ---------------------------------------------------------------------------

def _top_components(mask: np.ndarray, limit: int = 14):
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    order = sorted(range(1, num), key=lambda i: stats[i, cv2.CC_STAT_AREA], reverse=True)
    return labels, stats, order[:limit]


def analyze_cues(image: Any, prev_gray: np.ndarray | None):
    """Return (ratio_masks, fallback_boxes) using only ACCEPTED components."""
    height, width = image.shape[:2]
    frame_area = max(1, height * width)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gf = gray.astype(np.float32)

    gx = cv2.Sobel(gf, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gf, cv2.CV_32F, 0, 1, ksize=3)
    grad_fine = np.sqrt(gx * gx + gy * gy)
    grad = cv2.blur(grad_fine, (7, 7))

    mean9 = cv2.blur(gf, (9, 9))
    sq9 = cv2.blur(gf * gf, (9, 9))
    stddev9 = np.sqrt(np.maximum(sq9 - mean9 * mean9, 0.0))

    # Widened rust range catches pale rust under flaking paint.
    rust_raw = (((h <= 28) | (h >= 168)) & (s >= 45) & (v >= 30) & (v <= 230)).astype(np.uint8)
    rust = cv2.morphologyEx(rust_raw, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    rust_dil = cv2.dilate(rust, np.ones((15, 15), np.uint8))

    liquid_raw = ((h >= 3) & (h <= 32) & (s >= 160) & (v >= 100) & (stddev9 < 15)).astype(np.uint8)
    liquid_raw = cv2.morphologyEx(liquid_raw, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    highlight = (v >= 235).astype(np.uint8)
    crust_raw = ((s <= 70) & (v >= 170) & (v <= 232)).astype(np.uint8)
    crust_raw = cv2.morphologyEx(crust_raw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    smoke_raw = ((s <= 60) & (v >= 140) & (grad < 14)).astype(np.uint8)
    smoke_raw = cv2.morphologyEx(smoke_raw, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    char_raw = (v <= 55).astype(np.uint8)
    copper = ((h >= 5) & (h <= 20) & (s >= 130) & (v >= 140) & (stddev9 < 15)).astype(np.uint8)
    ice = ((s <= 70) & (v >= 200)).astype(np.uint8)
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (17, 17)))
    lines = (blackhat >= 42).astype(np.uint8)

    context = ((rust > 0) | (crust_raw > 0) | (char_raw > 0)).astype(np.uint8)
    bright_near = cv2.dilate(((copper > 0) | (highlight > 0)).astype(np.uint8),
                             np.ones((9, 9), np.uint8))
    motion = None
    if prev_gray is not None and prev_gray.shape == gray.shape:
        motion = cv2.absdiff(gray, prev_gray)

    k3 = np.ones((3, 3), np.uint8)
    k5 = np.ones((5, 5), np.uint8)
    fallback: list[tuple[str, int, int, int, int, int]] = []

    # --- corrosion: dense rust cores, so spread rust still gets compact boxes ---
    cores = cv2.erode(rust, np.ones((25, 25), np.uint8))
    labels, stats, order = _top_components(cores, limit=8)
    added_cor = 0
    for i in order:
        x, y, w, hh, area = stats[i]
        if area < 0.0012 * frame_area or added_cor >= 6:
            continue
        ex, ey = int(0.20 * w), int(0.20 * hh)
        x0, y0 = max(0, x - ex), max(0, y - ey)
        w0 = min(width - x0, w + 2 * ex)
        h0 = min(height - y0, hh + 2 * ey)
        fallback.append(("corrosion", x0, y0, w0, h0, area))
        added_cor += 1
    if added_cor == 0:
        labels, stats, order = _top_components(rust, limit=8)
        for i in order:
            x, y, w, hh, area = stats[i]
            if area < 0.003 * frame_area or added_cor >= 6:
                continue
            if (w * hh) > 0.90 * frame_area:
                continue
            fallback.append(("corrosion", x, y, w, hh, area))
            added_cor += 1

    # --- accepted liquid (drips / streams): smooth, saturated, own highlight ---
    liquid_acc = np.zeros_like(liquid_raw)
    labels, stats, order = _top_components(liquid_raw)
    leaks = 0
    for i in order:
        x, y, w, hh, area = stats[i]
        if not (0.0012 * frame_area <= area <= 0.04 * frame_area) or leaks >= 2:
            continue
        comp = (labels == i).astype(np.uint8)
        comp_bool = comp > 0
        hl_inside = float(highlight[comp_bool].sum()) / area
        fill = area / max(1, w * hh)
        hue_std = float(h[comp_bool].astype(np.float32).std()) if area else 0.0
        comp_dil = cv2.dilate(comp, k3) > 0
        ctx = float(context[comp_dil].sum()) / area
        if hl_inside > 0.002 and (hh >= 0.8 * w or fill > 0.4) and hue_std > 5 and ctx > 0.08:
            liquid_acc |= comp
            fallback.append(("leak", x, y, w, hh, area))
            leaks += 1

    # --- accepted salt crust: small grainy light patch EMBEDDED in rust ---
    crust_acc = np.zeros_like(crust_raw)
    labels, stats, order = _top_components(crust_raw)
    for i in order:
        x, y, w, hh, area = stats[i]
        if not (0.0008 * frame_area <= area <= 0.015 * frame_area):
            continue
        comp_bool = labels == i
        embedded = float(rust_dil[comp_bool].sum()) / area
        fill = area / max(1, w * hh)
        grain = float(stddev9[comp_bool].mean())
        mean_v = float(v[comp_bool].mean())
        if embedded > 0.6 and fill > 0.35 and 6.0 < grain < 60.0 and 170 < mean_v < 232:
            crust_acc |= comp_bool.astype(np.uint8)
            fallback.append(("leak", x, y, w, hh, area))

    # --- accepted smoke / steam plumes: bright, soft, tall, brighter than ring ---
    smoke_acc = np.zeros_like(smoke_raw)
    labels, stats, order = _top_components(smoke_raw)
    smokes = 0
    for i in order:
        x, y, w, hh, area = stats[i]
        if not (0.002 * frame_area <= area <= 0.20 * frame_area) or smokes >= 2:
            continue
        if hh < 1.0 * w:
            continue
        if x <= 1 and x + w >= width - 2:
            continue
        comp = (labels == i).astype(np.uint8)
        comp_bool = comp > 0
        interior_grad = float(grad[comp_bool].mean())
        ring = (cv2.dilate(comp, k5) > 0) & ~comp_bool
        ring_grad = float(grad[ring].mean()) if ring.any() else 99.0
        mean_s = float(s[comp_bool].mean())
        mean_v_comp = float(v[comp_bool].mean())
        mean_v_ring = float(v[ring].mean()) if ring.any() else 0.0
        brighter = (mean_v_comp - mean_v_ring) > 12.0
        strong = interior_grad < 7 and ring_grad < 12 and mean_s < 35 and brighter
        moves = True
        if motion is not None:
            moves = float(motion[comp_bool].mean()) > 6.0
        if (interior_grad < 10 and ring_grad < 18 and mean_s < 45
                and mean_v_comp > 150 and brighter and (strong or moves)):
            smoke_acc |= comp
            fallback.append(("overheating", x, y, w, hh, area))
            smokes += 1

    # --- accepted burn / char ---
    burn_acc = np.zeros_like(char_raw)
    labels, stats, order = _top_components(char_raw)
    burns = 0
    for i in order:
        x, y, w, hh, area = stats[i]
        if not (0.001 * frame_area <= area <= 0.05 * frame_area) or burns >= 2:
            continue
        comp = (labels == i).astype(np.uint8)
        comp_bool = comp > 0
        grain = float(stddev9[comp_bool].mean())
        comp_dil = cv2.dilate(comp, np.ones((9, 9), np.uint8)) > 0
        near_bright = float(bright_near[comp_dil].sum()) / area
        if grain > 12 and near_bright > 0.03:
            burn_acc |= comp
            fallback.append(("overheating", x, y, w, hh, area))
            burns += 1

    masks = {
        "rust": rust,
        "liquid": liquid_acc,
        "highlight": highlight,
        "crust": crust_acc,
        "smoke": smoke_acc,
        "char": char_raw,
        "copper": copper,
        "burn": burn_acc,
        "lines": lines,
        "ice": ice,
    }
    return masks, fallback


def box_ratios(masks: dict[str, np.ndarray], x1: int, y1: int, x2: int, y2: int) -> dict[str, float]:
    out = {}
    for key in ("rust", "liquid", "highlight", "crust", "smoke", "char", "copper", "burn", "lines", "ice"):
        region = masks[key][y1:y2, x1:x2]
        out[key] = float(region.mean()) if region.size else 0.0
    return out


def verify_candidate(key: str, ratios: dict[str, float], box_frac: float, strictness: float) -> bool:
    def th(value: float) -> float:
        return value * strictness

    if key == "corrosion":
        return ratios["rust"] > th(0.15)
    if key == "leak":
        liquid_ok = ratios["liquid"] > th(0.08) and ratios["highlight"] > th(0.002) and box_frac < 0.10
        crust_ok = ratios["crust"] > th(0.15) and ratios["rust"] > th(0.15) and box_frac < 0.08
        return liquid_ok or crust_ok
    if key == "crack":
        return ratios["lines"] > th(0.04)
    if key == "insulation_damage":
        return ratios["burn"] > th(0.06) or (ratios["char"] > th(0.30) and ratios["copper"] > th(0.08))
    if key == "overheating":
        return ratios["smoke"] > th(0.15) or ratios["burn"] > th(0.08)
    if key == "contamination":
        return (ratios["crust"] > th(0.12) and ratios["rust"] < 0.20) or \
               (ratios["rust"] > th(0.08) and ratios["char"] > th(0.04))
    if key == "mechanical_damage":
        return ratios["rust"] > th(0.10) or ratios["lines"] > th(0.05)
    if key == "wear":
        return ratios["rust"] > th(0.10) or ratios["copper"] > th(0.08)
    if key == "ice":
        return ratios["ice"] > th(0.20)
    if key == "missing_part":
        return True
    return False


def suppress_duplicate_boxes(candidates: list[dict[str, Any]], iou_threshold: float = 0.30) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for c in sorted(candidates, key=lambda i: i["confidence"], reverse=True):
        if any(c["class"] == k["class"] and intersection_over_union(box_of(c), box_of(k)) > iou_threshold
               for k in kept):
            continue
        kept.append(c)
    return kept


def suppress_cross_class(candidates: list[dict[str, Any]], iou_threshold: float = 0.55) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for c in sorted(candidates, key=lambda i: i["confidence"], reverse=True):
        cb = box_of(c)
        conflict = False
        for k in kept:
            kb = box_of(k)
            if intersection_over_union(cb, kb) <= iou_threshold:
                continue
            pair = {c["class"], k["class"]}
            if pair == {"leak", "corrosion"}:
                leak_box = cb if c["class"] == "leak" else kb
                inter = intersection_area(cb, kb)
                if inter / max(1, box_area(leak_box)) > 0.6:
                    continue  # drip sitting on rust: keep both boxes
            conflict = True
            break
        if not conflict:
            kept.append(c)
    return kept


def filter_by_area_and_shape(candidates: list[dict[str, Any]], width: int, height: int) -> list[dict[str, Any]]:
    frame_area = max(1, width * height)
    kept = []
    for c in candidates:
        w = c["x2"] - c["x1"]
        h = c["y2"] - c["y1"]
        area = w * h
        min_frac = 0.003 if c["class"] == "corrosion" else 0.004
        max_frac = 0.90 if c["class"] == "corrosion" else 0.70
        if area < min_frac * frame_area or area > max_frac * frame_area:
            continue
        aspect = max(w, h) / max(min(w, h), 1)
        if c["class"] != "crack" and aspect > 8:
            continue
        kept.append(c)
    return kept


FALLBACK_CONF = {
    "corrosion": (0.55, 8.0),
    "leak": (0.60, 20.0),
    "overheating": (0.60, 10.0),
}


def infer_candidates(model: Any, frame: Any, selected_roi: tuple[int, int, int, int] | None,
                     args: argparse.Namespace, uses_text_prompts: bool, names: Any,
                     prev_gray_full: np.ndarray | None) -> list[dict[str, Any]]:
    frame_height, frame_width = frame.shape[:2]
    if selected_roi:
        rx1, ry1, rx2, ry2 = selected_roi
        analysis_image = frame[ry1:ry2, rx1:rx2]
        origin_x, origin_y = rx1, ry1
        prev_gray = prev_gray_full[ry1:ry2, rx1:rx2] if prev_gray_full is not None else None
    else:
        analysis_image = frame
        origin_x = origin_y = 0
        prev_gray = prev_gray_full
    ah, aw = analysis_image.shape[:2]

    filter_equipment = not args.no_equipment_filter
    equipment_mask = compute_equipment_mask(analysis_image) if filter_equipment else None

    masks, fallback_boxes = analyze_cues(analysis_image, prev_gray)
    candidates: list[dict[str, Any]] = []

    tiles = make_tiles(analysis_image, args.tiles)
    inference_args: dict[str, Any] = {
        "source": [tile for tile, _, _ in tiles],
        "conf": args.confidence,
        "imgsz": args.image_size,
        "verbose": False,
    }
    if args.device:
        inference_args["device"] = args.device
    try:
        results = model.predict(**inference_args)
    except Exception as exc:
        if getattr(args, "raise_inference_errors", False):
            raise
        print(f"Ошибка инференса: {exc}", file=sys.stderr)
        results = []

    for (tile, tile_x, tile_y), result in zip(tiles, results):
        if result.boxes is None:
            continue
        for box in result.boxes:
            class_id = int(box.cls[0].item())
            confidence = float(box.conf[0].item())
            if confidence < args.confidence:
                continue
            raw_name = str(names.get(class_id, class_id)) if isinstance(names, dict) else str(names[class_id])
            if uses_text_prompts and 0 <= class_id < len(PROMPT_CLASS_KEYS):
                key = PROMPT_CLASS_KEYS[class_id]
            else:
                key = defect_key(raw_name)
            if key is None:
                continue
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
            lx1, ly1 = max(0, min(aw, tile_x + x1)), max(0, min(ah, tile_y + y1))
            lx2, ly2 = max(0, min(aw, tile_x + x2)), max(0, min(ah, tile_y + y2))
            if lx2 - lx1 < 4 or ly2 - ly1 < 4:
                continue
            ratios = box_ratios(masks, lx1, ly1, lx2, ly2)
            box_frac = ((lx2 - lx1) * (ly2 - ly1)) / max(1, ah * aw)
            if not verify_candidate(key, ratios, box_frac, args.strictness):
                continue
            fx1 = max(0, min(frame_width, origin_x + lx1))
            fy1 = max(0, min(frame_height, origin_y + ly1))
            fx2 = max(0, min(frame_width, origin_x + lx2))
            fy2 = max(0, min(frame_height, origin_y + ly2))
            if filter_equipment and not on_equipment(equipment_mask, key, fx1, fy1, fx2, fy2):
                continue
            candidates.append({
                "class": key, "class_id": class_id, "confidence": confidence,
                "x1": fx1, "y1": fy1, "x2": fx2, "y2": fy2,
                "method": "YOLO-World + CV-верификация",
            })

    frame_area = max(1, ah * aw)
    for key, x, y, w, hh, area in fallback_boxes:
        fx1 = max(0, min(frame_width, origin_x + x))
        fy1 = max(0, min(frame_height, origin_y + y))
        fx2 = max(0, min(frame_width, origin_x + x + w))
        fy2 = max(0, min(frame_height, origin_y + y + hh))
        if filter_equipment and not on_equipment(equipment_mask, key, fx1, fy1, fx2, fy2):
            continue
        base, scale = FALLBACK_CONF.get(key, (0.55, 10.0))
        candidates.append({
            "class": key, "class_id": -1,
            "confidence": min(0.95, base + scale * (area / frame_area)),
            "x1": fx1, "y1": fy1, "x2": fx2, "y2": fy2,
            "method": "classical CV",
        })

    candidates = filter_by_area_and_shape(candidates, frame_width, frame_height)
    candidates = suppress_duplicate_boxes(candidates, 0.30)
    candidates = suppress_cross_class(candidates, 0.55)

    if args.debug_equipment and equipment_mask is not None:
        args.output.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.output / "debug_equipment_mask.png"), equipment_mask)
    return candidates


def append_log(path: Path, records: list[dict[str, Any]]) -> None:
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "timestamp", "class", "label_ru", "class_id", "confidence",
            "model_class_guess", "observations", "severity", "recommendation",
            "method", "bbox_xyxy",
        ])
        if needs_header:
            writer.writeheader()
        writer.writerows(records)


def build_detections(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    detections = []
    for c in candidates:
        label, severity, recommendation = DEFECT_INFO[c["class"]]
        detections.append({
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "class": c["class"],
            "label_ru": label,
            "class_id": c["class_id"],
            "confidence": round(c["confidence"], 4),
            "model_class_guess": label,
            "observations": 1,
            "severity": severity,
            "recommendation": f"Кандидат модели. {recommendation}",
            "method": c.get("method", ""),
            "bbox_xyxy": f"{c['x1']},{c['y1']},{c['x2']},{c['y2']}",
        })
    return detections


def render_detections(frame: Any, detections: list[dict[str, Any]],
                      small_font: ImageFont.ImageFont) -> Any:
    for d in detections:
        x1, y1, x2, y2 = map(int, d["bbox_xyxy"].split(","))
        color = SEVERITY_COLORS.get(d["severity"], (0, 190, 255))
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3, cv2.LINE_AA)
    if detections:
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(image)
        for d in detections:
            x1, y1, _, _ = map(int, d["bbox_xyxy"].split(","))
            caption = f"{d['label_ru']} · {d['confidence']:.2f}"
            draw.text((x1, max(0, y1 - 25)), caption, font=small_font,
                      fill=(255, 255, 255), stroke_width=2, stroke_fill=(10, 10, 10))
        frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    return frame


def compute_global_status(detections: list[dict[str, Any]]) -> tuple[str, str]:
    if not detections:
        return "green", "Видимых проблем на оборудовании не обнаружено · это НЕ подтверждение исправности"
    max_sev = max(d["severity"] for d in detections)
    if max_sev >= 3:
        return "red", f"Обнаружены признаки дефекта · критичных: {sum(1 for d in detections if d['severity'] >= 3)}"
    return "yellow", f"Рекомендуется проверка · подозрительных: {len(detections)}"


def run_on_images(model: Any, paths: list[Path], args: argparse.Namespace,
                  uses_text_prompts: bool, names: Any,
                  small_font: ImageFont.ImageFont, font: ImageFont.ImageFont) -> int:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(q for q in p.rglob("*")
                                if q.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}))
        elif p.is_file():
            files.append(p)
    if not files:
        print("Не найдены изображения для проверки.", file=sys.stderr)
        return 2
    args.output.mkdir(parents=True, exist_ok=True)
    for path in files:
        frame = cv2.imread(str(path))
        if frame is None:
            print(f"Не удалось открыть: {path}", file=sys.stderr)
            continue
        cands = infer_candidates(model, frame, None, args, uses_text_prompts, names, None)
        detections = build_detections(cands)
        frame = render_detections(frame, detections, small_font)
        status_key, status_text = compute_global_status(detections)
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (frame.shape[1], 60), (9, 17, 33), -1)
        cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)
        ui = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw_ui_text(ui, f"СТАТУС: {status_text}", (14, 16), STATUS_COLORS[status_key], font)
        frame = cv2.cvtColor(np.asarray(ui), cv2.COLOR_RGB2BGR)
        out_path = args.output / f"checked_{path.stem}.jpg"
        cv2.imwrite(str(out_path), frame)
        print(f"\n{path.name}: найдено {len(detections)}")
        for d in detections:
            print(f"   - {d['label_ru']} ({d['class']}) conf={d['confidence']:.2f} [{d['method']}] bbox={d['bbox_xyxy']}")
        print(f"   сохранено: {out_path}")
    return 0


def main() -> int:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    if args.device is None and torch.backends.mps.is_available():
        args.device = "mps"
        print("Обнаружен Apple Silicon MPS: инференс на GPU.")

    try:
        model = YOLO(args.model)
        uses_text_prompts = callable(getattr(model, "set_classes", None))
        if uses_text_prompts:
            model.set_classes([text for _, text in PROMPTS])
            eq_state = "ВКЛЮЧЁН" if not args.no_equipment_filter else "отключён"
            st_state = "ВКЛЮЧЕНА" if not args.no_stability else "отключена"
            print(f"Фильтр оборудования: {eq_state}. Временная стабильность: {st_state}.")
            print("Для максимальной надёжности обучите свою модель: train_defect_model.py")
        else:
            print("Модель без текстовых запросов: классы из весов + CV-верификация.", file=sys.stderr)
    except Exception as exc:
        print(f"Не удалось загрузить модель {args.model!r}: {exc}", file=sys.stderr)
        return 2

    names = model.names
    font = load_font(21)
    small_font = load_font(16)

    if args.image:
        return run_on_images(model, args.image, args, uses_text_prompts, names, small_font, font)

    camera = cv2.VideoCapture(args.camera)
    if not camera.isOpened():
        print(f"Не удалось открыть камеру {args.camera}. macOS: Системные настройки → "
              "Конфиденциальность → Камера → разрешите Терминал/VSCode.", file=sys.stderr)
        return 3
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    log_path = args.output / "detections.csv"
    previous_time = time.perf_counter()
    fps = 0.0
    selected_roi: tuple[int, int, int, int] | None = None
    last_submit = 0.0
    last_analysis = 0.0
    roi_generation = 0
    future: Future[list[dict[str, Any]]] | None = None
    future_generation = -1
    candidates: list[dict[str, Any]] = []
    prev_raw: list[dict[str, Any]] = []
    prev_gray_full: np.ndarray | None = None
    last_error = ""
    sensitivity = f"строгость {args.strictness}"
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="defect-inference")
    print("\nКамера запущена. R — зона · C — весь кадр · 1/2/3 — детали · H/B — чувствительность · S — снимок · Q — выход")
    print("Дым = яркий мягкий плюм ярче фона; коррозия = плотные ядра ржавчины.\n")

    try:
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                print("Камера перестала отдавать кадры.", file=sys.stderr)
                break
            camera_frame = frame.copy()
            frame_height, frame_width = frame.shape[:2]
            now = time.perf_counter()

            if future is not None and future.done():
                try:
                    raw = future.result()
                    if future_generation == roi_generation:
                        candidates = raw if args.no_stability else match_stable(prev_raw, raw)
                        prev_raw = raw
                        last_analysis = now
                        last_error = ""
                except Exception as exc:
                    last_error = f"Ошибка анализа: {exc}"
                    print(last_error, file=sys.stderr)
                future = None

            if last_analysis and now - last_analysis > max(5.0, args.interval * 5):
                candidates = []
                prev_raw = []

            if future is None and now - last_submit >= args.interval:
                future_generation = roi_generation
                future = executor.submit(infer_candidates, model, frame.copy(),
                                         selected_roi, copy(args), uses_text_prompts, names,
                                         prev_gray_full)
                last_submit = now
            prev_gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            detections = build_detections(candidates)
            frame = render_detections(frame, detections, small_font)

            elapsed = max(time.perf_counter() - previous_time, 1e-6)
            previous_time = time.perf_counter()
            fps = 0.9 * fps + 0.1 * (1.0 / elapsed) if fps else 1.0 / elapsed

            if selected_roi:
                cv2.rectangle(frame, (selected_roi[0], selected_roi[1]),
                              (selected_roi[2], selected_roi[3]), (255, 220, 0), 2)

            status_key, status_text = compute_global_status(detections)
            if last_error:
                status_key, status_text = "red", f"Ошибка анализа: {last_error[:60]}"

            overlay = frame.copy()
            cv2.rectangle(overlay, (0, 0), (frame.shape[1], 130), (9, 17, 33), -1)
            cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)
            ui = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            draw_ui_text(ui, f"СТАТУС: {status_text}", (18, 12), STATUS_COLORS[status_key], font)
            roi_status = "выбран участок" if selected_roi else "весь кадр"
            age = f"анализ {now - last_analysis:.1f} с назад" if last_analysis else "анализ запускается"
            draw_ui_text(ui, f"Preview {fps:.1f} FPS · {sensitivity} · {args.tiles}×{args.tiles} · {age} · {roi_status}",
                         (18, 48), (225, 232, 245), small_font)
            draw_ui_text(ui, "R зона · C кадр · 1/2/3 детали · H чувств. · B строго · S снимок · Q выход",
                         (18, 78), (180, 190, 210), small_font)
            note = ("Показаны только дефекты на оборудовании, подтверждённые дважды"
                    if detections else
                    "На оборудовании признаков нет · для надёжной детекции обучите модель train_defect_model.py")
            draw_ui_text(ui, note, (18, 102), (160, 170, 190), small_font)
            frame = cv2.cvtColor(np.asarray(ui), cv2.COLOR_RGB2BGR)
            cv2.imshow("RepairHack · визуальный осмотр", frame)

            key_code = cv2.waitKey(1) & 0xFF
            if key_code in (ord("q"), ord("Q"), 27):
                break
            if key_code in (ord("r"), ord("R")):
                roi = cv2.selectROI("Выберите оборудование", camera_frame, showCrosshair=True, fromCenter=False)
                cv2.destroyWindow("Выберите оборудование")
                rx, ry, rw, rh = (int(v) for v in roi)
                if rw > 0 and rh > 0:
                    selected_roi = (rx, ry, min(frame_width, rx + rw), min(frame_height, ry + rh))
                    roi_generation += 1
                    candidates = []
                    prev_raw = []
                    last_analysis = 0.0
            if key_code in (ord("c"), ord("C")):
                selected_roi = None
                roi_generation += 1
                candidates = []
                prev_raw = []
                last_analysis = 0.0
            if key_code in (ord("1"), ord("2"), ord("3")):
                args.tiles = int(chr(key_code))
                last_submit = 0.0
            if key_code in (ord("h"), ord("H")):
                args.strictness = 0.6
                args.tiles = 3
                sensitivity = "чувствительный режим"
                roi_generation += 1
                candidates = []
                prev_raw = []
                last_analysis = 0.0
                last_submit = 0.0
                print("Чувствительный режим: верификация мягче, детализация 3×3.")
            if key_code in (ord("b"), ord("B")):
                args.strictness = 1.6
                args.tiles = 1
                sensitivity = "строгий режим"
                roi_generation += 1
                candidates = []
                prev_raw = []
                last_analysis = 0.0
                last_submit = 0.0
                print("Строгий режим: только выраженные признаки на оборудовании.")
            if key_code in (ord("s"), ord("S")):
                timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
                image_path = args.output / f"inspection_{timestamp}.jpg"
                if cv2.imwrite(str(image_path), frame):
                    append_log(log_path, detections)
                    print(f"Снимок: {image_path}; строк в журнале: {len(detections)}")
    except KeyboardInterrupt:
        print("\nОстановка по Ctrl+C.")
    finally:
        camera.release()
        cv2.destroyAllWindows()
        executor.shutdown(wait=False, cancel_futures=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
