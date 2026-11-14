import hashlib
import io
import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from response_contract import CODES, make_response

ROOT = Path(__file__).resolve().parent
Image.MAX_IMAGE_PIXELS = 25_000_000
lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    from ultralytics import YOLO
    pointer = ROOT / "model.local.json"
    configured = os.environ.get("FORK_YOLO_WEIGHTS")
    if not configured and pointer.is_file():
        configured = json.loads(pointer.read_text(encoding="utf-8"))["weights"]
    if not configured or not Path(configured).is_file():
        raise RuntimeError("No trained defect weights. Train a model before starting the service.")
    weights = Path(configured).resolve()
    detector = YOLO(str(weights))
    if detector.task != "detect" or tuple(detector.names[i] for i in range(len(detector.names))) != CODES:
        raise RuntimeError("Expected the 4 trained defect classes; stock COCO weights cannot serve as a defect model.")
    app.state.detector = detector
    app.state.version = "yolov8-" + hashlib.sha256(weights.read_bytes()).hexdigest()[:12]
    threshold = float(os.environ.get("FORK_YOLO_CONFIDENCE", "0.35"))
    if not 0 < threshold < 1:
        raise RuntimeError("Confidence must be between 0 and 1.")
    app.state.threshold = threshold
    yield


app = FastAPI(title="Fork local YOLOv8", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "modelVersion": app.state.version, "classes": list(CODES)}


@app.post("/analyze")
def analyze(
    file: UploadFile = File(...),
    equipmentId: UUID = Form(...),
    componentId: UUID = Form(...),
    installationId: UUID = Form(...),
):
    content = file.file.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "Image exceeds 10 MiB")
    try:
        with Image.open(io.BytesIO(content)) as original:
            if original.format not in ("JPEG", "PNG"):
                raise HTTPException(415, "Use JPEG or PNG")
            if min(original.size) < 64 or original.width * original.height > Image.MAX_IMAGE_PIXELS:
                raise HTTPException(422, "Image resolution must be at least 64x64 and at most 25 megapixels")
            if original.getexif().get(274, 1) != 1:
                raise HTTPException(422, "Normalize EXIF orientation before uploading, so boxes match the original photo coordinates")
            image = original.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise HTTPException(415, "Invalid image") from error
    if not lock.acquire(blocking=False):
        raise HTTPException(503, "Model is busy; retry after the current request")
    try:
        result = app.state.detector.predict(
            source=image, conf=app.state.threshold, imgsz=640,
            device=os.environ.get("FORK_YOLO_DEVICE", "0"), max_det=200, verbose=False,
        )[0]
        boxes = result.boxes
        rows = [] if boxes is None else [
            (class_id, confidence, *box)
            for class_id, confidence, box in zip(boxes.cls.cpu().tolist(), boxes.conf.cpu().tolist(), boxes.xywhn.cpu().tolist())
        ]
        return make_response(rows, app.state.version)
    finally:
        lock.release()
