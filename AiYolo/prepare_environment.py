from pathlib import Path
import torch
from ultralytics import YOLO

if not torch.cuda.is_available():
    raise SystemExit("CUDA is not available. Check the NVIDIA driver and CUDA PyTorch installation.")
print("GPU:", torch.cuda.get_device_name(0))
print("PyTorch:", torch.__version__, "CUDA runtime:", torch.version.cuda)
target = Path(__file__).resolve().parent / "weights" / "pretrained" / "yolov8n.pt"
target.parent.mkdir(parents=True, exist_ok=True)
model = YOLO(str(target))
if not target.is_file():
    raise SystemExit("Pretrained weights were not saved to the expected path.")
print("Pretrained YOLOv8n downloaded. These are general object weights, not a trained defect detector.")
