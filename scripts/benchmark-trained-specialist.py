#!/usr/bin/env python3
"""Compare supported labels on original case controls and report public controls separately."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import cv2
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ComputerVision'))
from case_data import records
from model_engine import ModelEngine, load_model, merge_detections

module_spec = importlib.util.spec_from_file_location('fixed_box_metrics', ROOT/'scripts/benchmark-specialist-models.py')
metric = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(metric)

BASELINES = {'crack': ['crack'], 'oil': ['oil'], 'corrosion': ['metal', 'case_pilot'],
             'damage': ['metal', 'case_pilot']}


def candidate_predict(model, path):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError('Cannot decode benchmark photo')
    if max(image.shape[:2]) > 1280:
        scale = 1280/max(image.shape[:2])
        image = cv2.resize(image, (round(image.shape[1]*scale), round(image.shape[0]*scale)), interpolation=cv2.INTER_AREA)
    prediction = model.predict(image, imgsz=640, conf=.25, iou=.5, max_det=150, device='cpu', verbose=False)[0]
    detections = []
    for coords, cid, confidence in zip(prediction.boxes.xyxyn.tolist(), prediction.boxes.cls.tolist(), prediction.boxes.conf.tolist()):
        x1, y1, x2, y2 = [max(0., min(1., value)) for value in coords]
        detections.append(dict(label=model.names[int(cid)], confidence=confidence,
                               box=dict(x=x1, y=y1, width=x2-x1, height=y2-y1)))
    return detections, image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--kind', choices=BASELINES, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    status = json.loads((run/'status.json').read_text())
    evaluation = json.loads((run/'evaluation.json').read_text())
    weights = run/'weights/best.pt'
    if status['state'] != 'complete' or hashlib.sha256(weights.read_bytes()).hexdigest() != evaluation['sha256']:
        raise ValueError('Training evaluation is incomplete or weights changed')
    classes = evaluation['classes']
    torch.set_num_threads(4)
    candidate = load_model(weights)
    engine = ModelEngine()
    baseline_rows, candidate_rows = [], []
    for sample in records(verify=True):
        if sample['split'] not in ('val', 'test'):
            continue
        predicted, image = candidate_predict(candidate, sample['path'])
        baseline = merge_detections([item for mid in BASELINES[args.kind]
                                    for item in engine.predict(mid, image, .25, False)['detections']
                                    if item['label'] in classes])
        row = {key: value for key, value in sample.items() if key != 'path'}
        row['reference'] = [item for item in row['reference'] if item['label'] in classes]
        baseline_rows.append(dict(row, detections=baseline))
        candidate_rows.append(dict(row, detections=predicted))
    out = ROOT/'ComputerVision/benchmarks'/run.name
    out.mkdir(parents=True, exist_ok=True)
    public_rows = []
    data = Path(status['data']).parent
    summary = json.loads((data/'summary.json').read_text())
    if args.kind != 'crack':
        provenance = json.loads((data/'provenance.json').read_text())
        for record in provenance['test']:
            if record.get('source') != 'roboflow':
                continue
            path = data/'images/test'/record['file']
            label = data/'labels/test'/Path(record['file']).with_suffix('.txt')
            if hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256'] or hashlib.sha256(label.read_bytes()).hexdigest() != record['labelsSha256']:
                raise ValueError('Frozen public test photo or annotation changed')
            reference = []
            for line in label.read_text().splitlines():
                cid, x, y, width, height = map(float, line.split())
                reference.append(dict(label=classes[int(cid)], box=dict(x=x-width/2, y=y-height/2, width=width, height=height)))
            detections, _ = candidate_predict(candidate, path)
            public_rows.append(dict(file=record['file'], group=record['group'], reference=reference, detections=detections))
    case_metrics = {name: {split: {code: metric.score([row for row in rows if row['split'] == split], code)
                                  for code in classes} for split in ('val', 'test')}
                    for name, rows in (('baseline', baseline_rows), ('candidate', candidate_rows))}
    report = dict(createdAt=datetime.now(timezone.utc).isoformat(), run=run.name, weights=str(weights),
                  sha256=evaluation['sha256'], threshold=.25, iou=.5, maxDetections=150,
                  metric='Fixed-threshold one-to-one box matching, not mAP',
                  baselineIds=BASELINES[args.kind], originalCaseValImages=5, originalCaseTestImages=12,
                  case=case_metrics, publicTestImages=len(public_rows),
                  publicTest={code: metric.score(public_rows, code) for code in classes} if public_rows else None,
                  trainingHeldoutEvaluation=evaluation, limitations=summary['limitations'])
    for name, value in (('baseline-case-predictions.json', baseline_rows), ('candidate-case-predictions.json', candidate_rows),
                        ('candidate-public-predictions.json', public_rows), ('report.json', report)):
        (out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(run=run.name, caseTest={name: value['test'] for name, value in case_metrics.items()},
                          publicTestImages=len(public_rows), publicTest=report['publicTest'], report=str(out/'report.json')), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
