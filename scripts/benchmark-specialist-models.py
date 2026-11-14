#!/usr/bin/env python3
"""Fixed-threshold box evaluation; never edits annotations or selects deployment models."""
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ComputerVision'))
import cv2
import numpy as np
import torch
import ultralytics
from case_data import records
from model_engine import ModelEngine, NAMES, overlap

OUT=ROOT/'ComputerVision/benchmarks/specialists-v1'
THRESHOLD=.25


def score(rows, label):
    tp=fp=fn=0
    for row in rows:
        targets=[d for d in row['reference'] if d['label']==label]
        matched=set()
        for prediction in sorted((d for d in row['detections'] if d['label']==label),key=lambda d:d['confidence'],reverse=True):
            choices=[(overlap(prediction['box'],target['box']),i) for i,target in enumerate(targets) if i not in matched]
            best=max(choices,default=(0,-1))
            if best[0]>=.5:
                tp+=1;matched.add(best[1])
            else:
                fp+=1
        fn+=len(targets)-len(matched)
    return dict(tp=tp,fp=fp,fn=fn,referenceBoxes=tp+fn,predictedBoxes=tp+fp,
                precision=tp/(tp+fp) if tp+fp else None,recall=tp/(tp+fn) if tp+fn else None)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    samples=records(verify=True)
    engine=ModelEngine()
    known={d['label'] for row in samples for d in row['reference']}
    report=dict(createdAt=datetime.now(timezone.utc).isoformat(),threshold=THRESHOLD,iou=.5,
                metric='Greedy one-to-one box matching at fixed threshold; not mAP',
                images=54,testImages=12,device='cpu',threads=torch.get_num_threads(),
                python=platform.python_version(),torch=torch.__version__,ultralytics=ultralytics.__version__,
                limitations=['Маленькая выборка; физическая независимость групп не подтверждена.',
                'Нет снимков исправного оборудования: частоту ложных тревог на нём измерить нельзя.',
                'Метки пользователя сохранены без исправлений; возможны неоднозначные и неполные метки.',
                'Снимки кейса могли входить в обучение публичных моделей; это неизвестно.',
                'Все 54 фото включают train нашей модели; отдельно показаны 12 test.',
                'После просмотра сравнения этот test нельзя считать новым закрытым тестом для следующего выбора модели.',
                'Разные модели поддерживают разные классы. Сводные проценты между ними напрямую несопоставимы.'],models=[])
    for mid,entry in engine.entries.items():
        engine.predict(mid,np.zeros((128,128,3),dtype=np.uint8),THRESHOLD,False)
        rows=[]
        for sample in samples:
            picture=cv2.imread(str(sample['path']))
            if max(picture.shape[:2])>1280:
                scale=1280/max(picture.shape[:2]);picture=cv2.resize(picture,(round(picture.shape[1]*scale),round(picture.shape[0]*scale)),interpolation=cv2.INTER_AREA)
            result=engine.predict(mid,picture,THRESHOLD,False)
            rows.append({**{k:v for k,v in sample.items() if k!='path'},**{k:result[k] for k in ('detections','elapsedMs')}})
        supported=sorted(set(entry['mapping'].values()) & known)
        metrics={subset:{label:score([r for r in rows if subset=='all' or r['split']==subset],label) for label in supported} for subset in ('all','test')}
        times=[r['elapsedMs'] for r in rows]
        model=dict(id=mid,name=entry['name'],sha256=entry['sha256'],metrics=metrics,
                   unmeasuredLabels=sorted(set(entry['mapping'].values())-known),
                   medianMs=round(statistics.median(times),1),p95Ms=round(float(np.percentile(times,95)),1),
                   imagesWithDetections=sum(bool(r['detections']) for r in rows),predictions=sum(len(r['detections']) for r in rows))
        report['models'].append(model)
        (OUT/(mid+'-predictions.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        print(json.dumps(model,ensure_ascii=False),flush=True)
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    lines=['СРАВНЕНИЕ ПЯТИ МОДЕЛЕЙ НА ФОТОГРАФИЯХ КЕЙСА','',
           'Порог 0.25; совпадение рамок IoU ≥ 0.5. Это precision/recall при одном пороге, не mAP.',
           'Precision — доля предсказанных рамок, совпавших с метками. Recall — доля меток, найденных моделью.',
           'Ниже только 12 test-фото; полный разбор 54 фото и результаты по каждому снимку лежат в ComputerVision/benchmarks/specialists-v1.','']
    for model in report['models']:
        lines.extend([model['name'],f"CPU: медиана {model['medianMs']} мс; p95 {model['p95Ms']} мс (без загрузки весов)."])
        for label,metric in model['metrics']['test'].items():
            precision='—' if metric['precision'] is None else f"{100*metric['precision']:.1f}%"
            recall='—' if metric['recall'] is None else f"{100*metric['recall']:.1f}%"
            lines.append(f"  {NAMES[label]}: найдено {metric['tp']}/{metric['referenceBoxes']}, лишних рамок {metric['fp']}, precision {precision}, recall {recall}.")
        if model['unmeasuredLabels']:lines.append('  Нет сопоставимых меток: '+', '.join(NAMES[k] for k in model['unmeasuredLabels']))
        lines.append('')
    lines+=['Ограничения:']+['• '+line for line in report['limitations']]
    (ROOT/'СРАВНЕНИЕ_МОДЕЛЕЙ.txt').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':main()
