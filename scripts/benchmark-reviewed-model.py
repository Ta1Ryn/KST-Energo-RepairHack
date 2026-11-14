#!/usr/bin/env python3
"""Compare same-class, fixed-threshold predictions on frozen evaluation photos."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import cv2
import torch
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ComputerVision'))
spec = importlib.util.spec_from_file_location('box_benchmark', ROOT/'scripts/benchmark-specialist-models.py')
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, default=ROOT/'ComputerVision/models/case-pilot.pt')
    args = parser.parse_args()
    run = args.run.resolve()
    status = json.loads((run/'status.json').read_text())
    if status['state'] != 'complete':
        raise ValueError('Training must be complete before comparison')
    evaluation = json.loads((run/'evaluation.json').read_text())
    checkpoint = run/'weights/best.pt'
    if sha(checkpoint) != evaluation['sha256']:
        raise ValueError('Checkpoint changed after evaluation')
    data = Path(status['data']).parent
    summary = json.loads((data/'summary.json').read_text())
    labels = [c['code'] for c in summary['classes']]
    provenance = json.loads((data/'provenance.json').read_text())
    samples = []
    for split in ('val', 'test'):
        for record in provenance[split]:
            path = data/'images'/split/record['file']
            annotation = data/'labels'/split/Path(record['file']).with_suffix('.txt')
            if sha(path) != record['sha256'] or sha(annotation) != record['labelsSha256']:
                raise ValueError('Frozen evaluation data changed')
            reference = []
            for line in annotation.read_text().splitlines():
                cls, x, y, w, h = map(float, line.split())
                reference.append(dict(label=labels[int(cls)], box=dict(x=x-w/2, y=y-h/2, width=w, height=h)))
            samples.append(dict(path=path, split=split, file=record['file'], group=record['group'], reference=reference))
    out = ROOT/'ComputerVision/benchmarks'/run.name
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    report = dict(createdAt=datetime.now(timezone.utc).isoformat(), run=run.name, data=str(data),
        threshold=.25, iou=.5, metric='Same classes; greedy one-to-one matching at a fixed threshold, not mAP',
        limitations=summary['limitations'], models=[])
    for name, weights in (('baseline', args.baseline.resolve()), ('reviewed_candidate', checkpoint)):
        model = YOLO(str(weights))
        if [model.names[i] for i in range(len(model.names))] != labels:
            raise ValueError('Model class order differs')
        rows = []
        for sample in samples:
            picture = cv2.imread(str(sample['path']))
            if max(picture.shape[:2]) > 1280:
                scale = 1280/max(picture.shape[:2])
                picture = cv2.resize(picture, (round(picture.shape[1]*scale), round(picture.shape[0]*scale)), interpolation=cv2.INTER_AREA)
            prediction = model.predict(picture, imgsz=640, conf=.25, iou=.5, device='cpu', max_det=150, verbose=False)[0]
            detections = []
            for box, cid, confidence in zip(prediction.boxes.xyxyn.tolist(), prediction.boxes.cls.tolist(), prediction.boxes.conf.tolist()):
                x1, y1, x2, y2 = [max(0., min(1., value)) for value in box]
                detections.append(dict(label=labels[int(cid)], confidence=confidence,
                    box=dict(x=x1, y=y1, width=x2-x1, height=y2-y1)))
            rows.append({**{k: v for k, v in sample.items() if k != 'path'}, 'detections': detections})
        metrics = {split: {label: benchmark.score([r for r in rows if r['split'] == split], label)
            for label in labels} for split in ('val', 'test')}
        report['models'].append(dict(id=name, weights=str(weights), sha256=sha(weights), metrics=metrics))
        (out/(name+'-predictions.json')).write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        print(json.dumps(dict(model=name, metrics=metrics), ensure_ascii=False), flush=True)
    (out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(out/'report.json', flush=True)


if __name__ == '__main__':
    main()
