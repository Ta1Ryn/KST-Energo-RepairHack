#!/usr/bin/env python3
"""Publish a reproducible comparison from saved predictions and unchanged case photos."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CV = ROOT / 'ComputerVision'
sys.path.insert(0, str(CV))
from case_data import records

RUNS = {
    'oil': 'roboflow-oil-20261114-170338',
    'damage': 'roboflow-damage-20261114-170823',
    'corrosion': 'roboflow-corrosion-20261114-171510',
    'corrosion_adapt': 'corrosion-user-adapt-20261114-175313',
    'crack': 'crack-seg-20261114-164154',
}
LABELS = {
    'corrosion': 'Коррозия', 'leak': 'Следы масла', 'crack': 'Трещина',
    'mechanical_damage': 'Повреждение', 'wear': 'Износ',
}


def read_json(path):
    return json.loads(path.read_text())


def iou(a, b):
    width = max(0, min(a['x'] + a['width'], b['x'] + b['width']) - max(a['x'], b['x']))
    height = max(0, min(a['y'] + a['height'], b['y'] + b['height']) - max(a['y'], b['y']))
    intersection = width * height
    return intersection / max(1e-12, a['width'] * a['height'] + b['width'] * b['height'] - intersection)


def photo_score(row, label):
    targets = [d for d in row['reference'] if d['label'] == label]
    matched = set()
    tp = fp = 0
    for detection in sorted((d for d in row['detections'] if d['label'] == label),
                            key=lambda d: d['confidence'], reverse=True):
        best = max(((iou(detection['box'], target['box']), i)
                    for i, target in enumerate(targets) if i not in matched), default=(0, -1))
        if best[0] >= .5:
            tp += 1
            matched.add(best[1])
        else:
            fp += 1
    return dict(tp=tp, fp=fp, fn=len(targets) - len(matched), referenceBoxes=len(targets))


def main():
    samples = [row for row in records(verify=True) if row['split'] == 'test']
    if len(samples) != 12:
        raise ValueError('Expected the unchanged twelve original case test photos')
    sample_ids = {row['id'] for row in samples}
    sources = {}
    for key, run in RUNS.items():
        directory = CV / 'benchmarks' / run
        report = read_json(directory / 'report.json')
        status = read_json(CV / 'runs' / run / 'status.json')
        weights = CV / 'runs' / run / 'weights' / 'best.pt'
        if status['state'] != 'complete' or hashlib.sha256(weights.read_bytes()).hexdigest() != report['sha256']:
            raise ValueError('Completed checkpoint does not match its benchmark: ' + run)
        if report['threshold'] != .25 or report['iou'] != .5:
            raise ValueError('Comparison settings differ: ' + run)
        sources[key] = dict(report=report, status=status)
        for name in ('baseline', 'candidate'):
            rows = {row['id']: row for row in read_json(directory / (name + '-case-predictions.json'))
                    if row['split'] == 'test'}
            if set(rows) != sample_ids:
                raise ValueError('Missing or additional comparison photos: ' + run)
            supported = report['case'][name]['test']
            for sample in samples:
                expected = [d for d in sample['reference'] if d['label'] in supported]
                if rows[sample['id']]['reference'] != expected:
                    raise ValueError('The saved reference differs from the frozen annotation')
            scores = {pid: {label: photo_score(row, label) for label in supported}
                      for pid, row in rows.items()}
            for label, totals in supported.items():
                for field in ('tp', 'fp', 'fn', 'referenceBoxes'):
                    if sum(score[label][field] for score in scores.values()) != totals[field]:
                        raise ValueError('Per-photo counts do not reproduce the saved benchmark')
            sources[key][name] = dict(
                detections={pid: row['detections'] for pid, row in rows.items()}, scores=scores,
                totals=supported, run=run, sha256=report['sha256'] if name == 'candidate' else None,
            )

    if sources['corrosion']['baseline']['detections'] != sources['corrosion_adapt']['baseline']['detections']:
        raise ValueError('Corrosion baselines differ between the two training stages')
    photos = []
    for sample in samples:
        content = sample['path'].read_bytes()
        with Image.open(sample['path']) as picture:
            mime = Image.MIME[picture.format]
            width, height = picture.size
        photos.append({key: sample[key] for key in ('id', 'name', 'source', 'category', 'imageUrl', 'reference')} | dict(
            width=width, height=height, sha256=hashlib.sha256(content).hexdigest(),
            embedded='data:' + mime + ';base64,' + base64.b64encode(content).decode(),
        ))

    definitions = [
        ('corrosion', 'Коррозия', 'corrosion_adapt', 'metal + case_pilot', '30 + 20 эпох · адаптация на твоих фото',
         'По 12 фото: найдено 4 из 13 меток и раньше, и сейчас. Несовпавших рамок стало 5 вместо 1.', 'corrosion'),
        ('oil', 'Масло', 'oil', 'oil', '25 эпох · масло и твоя разметка',
         'Несовпавших рамок стало 1 вместо 7. Обе размеченные течи пока пропускают обе модели.', None),
        ('damage', 'Повреждения и износ', 'damage', 'metal + case_pilot', '25 эпох · лопасти и твоя разметка',
         'Лишних рамок износа меньше. Размеченные трещины, повреждение и износ на этих 12 фото пока пропущены.', None),
        ('crack', 'Трещины · Crack-Seg', 'crack', 'crack', '7 эпох · остановка по качеству',
         'Прежняя модель совпала с 3 из 5 меток, новая — с 2 из 5. На этих фото прежняя точнее.', None),
    ]
    topics = []
    for key, title, source_key, baseline_name, candidate_name, note, public_key in definitions:
        source = sources[source_key]
        external = sources[public_key] if public_key else source
        topic = dict(id=key, title=title, classes=list(source['candidate']['totals']), note=note,
                     before=source['baseline'] | dict(title='Прежняя модель', subtitle=baseline_name),
                     after=source['candidate'] | dict(title='Новая модель', subtitle=candidate_name),
                     publicTest=external['report']['publicTest'],
                     publicTestImages=external['report']['publicTestImages'])
        if public_key:
            topic['stage'] = external['candidate'] | dict(title='Первый этап', subtitle='30 эпох · публичная коррозия')
            topic['publicCaption'] = 'Первый этап коррозии, до адаптации на твоих фото'
        topics.append(topic)

    data = dict(version='kst-training-comparison-20261114-v1', createdAt=datetime.now(timezone.utc).isoformat(),
                threshold=.25, iou=.5, labels=LABELS, photos=photos, topics=topics,
                completed=[dict(id=key, run=RUNS[key], epochs=value['status']['completedEpochs'],
                                planned=value['status']['epochs']) for key, value in sources.items()])
    serialized = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c')
    template = (CV / 'training-comparison-template.html').read_text()
    if template.count('__COMPARISON_DATA__') != 1:
        raise ValueError('Expected one data placeholder')
    page = template.replace('__COMPARISON_DATA__', serialized)
    panel = CV / 'training-panel.html'
    archive = ROOT / '.integration' / 'training-panel-before-comparison.html'
    if not archive.exists():
        archive.write_bytes(panel.read_bytes())
    panel.write_text(page)
    offline = Path('/home/nok1d/Downloads/КСТ_сравнение_моделей_2026-11-14.html')
    offline.write_text(page)
    summary = dict(createdAt=data['createdAt'], photos=len(photos), topics=len(topics),
                   verifiedWeights=[dict(run=RUNS[key], sha256=value['report']['sha256'])
                                    for key, value in sources.items()],
                   online='http://127.0.0.1:8001/training', offline=str(offline),
                   offlineSha256=hashlib.sha256(offline.read_bytes()).hexdigest())
    (ROOT / '.integration' / 'training-comparison.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
