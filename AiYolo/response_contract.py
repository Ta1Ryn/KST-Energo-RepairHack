"""Translate normalized YOLO center boxes to the existing C# top-left contract."""
import math

CODES = ("oilLeak", "displacement", "corrosion", "geometryDeformation")
NAMES = ("течь масла", "смещение узла", "коррозия", "деформация детали")


def make_response(rows, model_version):
    detections = []
    for class_id, confidence, xc, yc, width, height in rows:
        numbers = (class_id, confidence, xc, yc, width, height)
        if not all(math.isfinite(float(value)) for value in numbers):
            raise ValueError("Non-finite detection")
        class_id = int(class_id) if float(class_id).is_integer() else -1
        if class_id not in range(4) or not 0 <= confidence <= 1 or width <= 0 or height <= 0:
            raise ValueError("Invalid detection")
        left, top = max(0.0, xc - width / 2), max(0.0, yc - height / 2)
        right, bottom = min(1.0, xc + width / 2), min(1.0, yc + height / 2)
        if right <= left or bottom <= top:
            raise ValueError("Box is outside the image")
        detections.append({
            "label": CODES[class_id], "confidence": float(confidence),
            "box": {"x": left, "y": top, "width": right - left, "height": bottom - top},
        })
    found = [NAMES[i] for i, code in enumerate(CODES) if any(d["label"] == code for d in detections)]
    return {
        "modelVersion": model_version,
        "visualStatus": "possibleDefect" if detections else "checkRecommended",
        "summary": "Возможные дефекты: " + ", ".join(found) + "." if found else "Модель не нашла дефектов выше заданного порога уверенности.",
        "recommendation": "Проверить отмеченные области и применить инструкцию для данного узла." if found else "Подтвердить состояние осмотром и показаниями датчиков; при необходимости сделать более чёткий снимок.",
        "confidence": max((d["confidence"] for d in detections), default=None),
        "detections": detections,
    }
