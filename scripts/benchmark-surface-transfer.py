#!/usr/bin/env python3
"""Compare a completed transfer run without changing the active camera models."""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ComputerVision'))
from model_engine import ModelEngine,load_model,merge_detections
spec=importlib.util.spec_from_file_location('specialist_benchmark',ROOT/'scripts/benchmark-specialist-models.py')
benchmark=importlib.util.module_from_spec(spec);spec.loader.exec_module(benchmark)
LABELS=['corrosion','crack']


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--data',type=Path,default=ROOT/'ComputerVision/datasets/surface-transfer-rf100-v1')
    args=parser.parse_args();run=args.run.resolve();data=args.data.resolve()
    checkpoint=run/'weights/best.pt'
    evaluation=json.loads((run/'evaluation.json').read_text())
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest()!=evaluation['sha256']:raise ValueError('Candidate checksum mismatch')
    provenance=json.loads((data/'provenance.json').read_text())
    samples=[]
    for split in ('val','test'):
        for item in provenance[split]:
            path=data/'images'/split/item['file'];label=data/'labels'/split/Path(item['file']).with_suffix('.txt')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256'] or hashlib.sha256(label.read_bytes()).hexdigest()!=item['labelsSha256']:raise ValueError('Modified frozen data')
            reference=[]
            for line in label.read_text().splitlines():
                cls,x,y,w,h=map(float,line.split())
                reference.append(dict(label=LABELS[int(cls)],box=dict(x=x-w/2,y=y-h/2,width=w,height=h)))
            samples.append(dict(path=path,file=item['file'],source=item['source'],origin=item['origin'],split=split,reference=reference))
    out=ROOT/'ComputerVision/benchmarks'/run.name;out.mkdir(parents=True,exist_ok=True)
    engine=ModelEngine();candidate=load_model(checkpoint)
    if set(candidate.names.values())!=set(LABELS):raise ValueError('Unexpected candidate labels')
    entries={'surface_candidate':dict(name='RF100 + наша разметка · YOLO11n',sha256=evaluation['sha256'],mapping={n:n for n in LABELS})}
    entries.update({key:engine.entries[key] for key in ('case_pilot','corrosion','crack','metal')})
    report=dict(createdAt=datetime.now(timezone.utc).isoformat(),run=run.name,data=data.name,threshold=.25,iou=.5,
        metric='One-to-one box matching at fixed confidence, not mAP',device='cpu',models=[],
        limitations=['Case test has only 12 repeatedly evaluated photos; it is not a fresh final test.',
          'RF100 test and user test are reported separately; source scenes and labels differ.',
          'Filename family grouping does not establish physical independence.',
          'Models support different classes; compare only the same class and subset.',
          'Only corrosion and crack are evaluated; other defect classes are not represented in these metrics.',
          'Public models may have seen these public source images; their training membership is unknown.'])
    for mid,entry in entries.items():
        rows=[]
        for item in samples:
            picture=cv2.imread(str(item['path']))
            if max(picture.shape[:2])>1280:
                scale=1280/max(picture.shape[:2]);picture=cv2.resize(picture,(round(picture.shape[1]*scale),round(picture.shape[0]*scale)),interpolation=cv2.INTER_AREA)
            if mid=='surface_candidate':
                start=time.perf_counter()
                result=candidate.predict(picture,imgsz=640,conf=.25,iou=.5,device='cpu',max_det=150,verbose=False)[0]
                detections=[]
                for xyxy,cls,confidence in zip(result.boxes.xyxyn.tolist(),result.boxes.cls.tolist(),result.boxes.conf.tolist()):
                    x1,y1,x2,y2=[max(0.,min(1.,float(x))) for x in xyxy]
                    detections.append(dict(label=candidate.names[int(cls)],confidence=confidence,modelId=mid,box=dict(x=x1,y=y1,width=x2-x1,height=y2-y1)))
                result=dict(detections=merge_detections(detections),elapsedMs=round((time.perf_counter()-start)*1000,1))
            else:result=engine.predict(mid,picture,.25,False)
            rows.append({**{k:v for k,v in item.items() if k!='path'},'detections':result['detections'],'elapsedMs':result['elapsedMs']})
        subsets={f'{origin}-{split}':[r for r in rows if r['origin']==origin and r['split']==split] for origin in ('user-reviewed','rf100') for split in ('val','test')}
        supported=set(entry['mapping'].values())&set(LABELS)
        metrics={name:dict(images=len(group),classes={label:benchmark.score(group,label) for label in sorted(supported)}) for name,group in subsets.items()}
        item=dict(id=mid,name=entry['name'],sha256=entry['sha256'],metrics=metrics)
        report['models'].append(item)
        (out/(mid+'-predictions.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        print(json.dumps({'model':mid,'caseTest':metrics['user-reviewed-test'],'externalTest':metrics['rf100-test']},ensure_ascii=False),flush=True)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print('Report:',out/'report.json',flush=True)


if __name__=='__main__':main()
