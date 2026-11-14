#!/usr/bin/env python3
"""Reproduce the casting classifier's published split using local safetensors."""
import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import zipfile

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ComputerVision'))
from casting_engine import CastingClassifier


def metrics(results,labels):
    counts=Counter((r['target'],r['predicted']) for r in results)
    per_class={}
    for label in labels:
        tp=counts[(label,label)];fp=sum(v for (t,p),v in counts.items() if p==label and t!=label);fn=sum(v for (t,p),v in counts.items() if t==label and p!=label)
        per_class[label]={'precision':tp/max(1,tp+fp),'recall':tp/max(1,tp+fn),'f1':2*tp/max(1,2*tp+fp+fn)}
    correct=sum(r['target']==r['predicted'] for r in results)
    return dict(images=len(results),correct=correct,accuracy=correct/max(1,len(results)),perClass=per_class,
        macroF1=sum(x['f1'] for x in per_class.values())/len(labels),confusion={t:{p:counts[(t,p)] for p in labels} for t in labels})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--device',default='cpu');parser.add_argument('--image',type=Path)
    parser.add_argument('--reuse-predictions',action='store_true',help='Recompute duplicate audit from the previously saved inference, without running the model again.')
    args=parser.parse_args()
    if args.image and args.reuse_predictions:parser.error('--image and --reuse-predictions cannot be combined')
    directory=ROOT/'ComputerVision/experimental/casting-vit'
    manifest=json.loads((directory/'source-manifest.json').read_text())
    labels=['defective','ok'];model=None
    if not args.reuse_predictions:model=CastingClassifier(args.device)
    if args.image:
        with Image.open(args.image) as image:result=model.predict([image.convert('RGB')])[0]
        print(json.dumps({'scope':'Top-view submersible pump impeller casting only','scores':result},ensure_ascii=False));return
    source=ROOT/'ExternalData/pump-impeller-casting/original-kaggle.zip'
    source_manifest=json.loads((source.parent/'source-manifest.json').read_text())
    source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
    if source_sha!=source_manifest['sha256']:raise ValueError('Modified dataset archive')
    out=ROOT/'ComputerVision/benchmarks/casting-vit';out.mkdir(parents=True,exist_ok=True)
    results=[];started=time.perf_counter();previous=None
    if args.reuse_predictions:
        previous=json.loads((out/'report.json').read_text())
        if previous['revision']!=manifest['revision'] or previous['model']!=manifest['repo']:raise ValueError('Cached predictions use a different model')
        results=json.loads((out/'predictions.json').read_text())
        if previous.get('predictionsSha256') and hashlib.sha256((out/'predictions.json').read_bytes()).hexdigest()!=previous['predictionsSha256']:raise ValueError('Modified cached predictions')
    with zipfile.ZipFile(source) as archive:
        names=sorted(n for n in archive.namelist() if '/test/' in n and n.lower().endswith(('.jpg','.jpeg','.png')))
        if len(names)!=715:raise ValueError('Unexpected author test count: '+str(len(names)))
        if args.reuse_predictions and sorted(r['file'] for r in results)!=names:raise ValueError('Incomplete cached test predictions')
        train_hashes={hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist() if '/train/' in n and n.lower().endswith(('.jpg','.jpeg','.png'))}
        duplicate_test=[]
        for offset in range(0,len(names),8):
            paths=names[offset:offset+8];images=[]
            for name in paths:
                data=archive.read(name)
                if hashlib.sha256(data).hexdigest() in train_hashes:duplicate_test.append(name)
                with Image.open(io.BytesIO(data)) as im:images.append(im.convert('RGB'))
            if args.reuse_predictions:continue
            predictions=model.predict(images)
            for name,scores in zip(paths,predictions):
                target='defective' if '/def_front/' in name else 'ok';predicted=max(scores,key=scores.get)
                results.append(dict(file=name,target=target,predicted=predicted,scores=scores))
            if offset%128==0:print('Evaluated',min(offset+8,len(names)),'/',len(names),flush=True)
    for item in results:
        expected='defective' if '/def_front/' in item['file'] else 'ok'
        if item['target']!=expected or item['predicted']!=max(item['scores'],key=item['scores'].get) or set(item['scores'])!=set(labels):raise ValueError('Invalid cached classification')
    duplicate_set=set(duplicate_test)
    clean=metrics([r for r in results if r['file'] not in duplicate_set],labels)
    clean['note']='Near duplicates and same-casting augmentation overlap are not excluded.'
    report=dict(model=manifest['repo'],revision=manifest['revision'],**metrics(results,labels),
        device=previous['device'] if previous else args.device,elapsedSeconds=previous['elapsedSeconds'] if previous else round(time.perf_counter()-started,2),
        sourceArchiveSha256=source_sha,predictionsReused=args.reuse_predictions,
        exactFileTrainTestDuplicates=len(duplicate_test),exactDuplicateFiles=duplicate_test,withoutExactTrainDuplicates=clean,authorReportedAccuracy=.996,
        limitations=['This reproduces the publisher split of already augmented 300x300 top-view impeller photos.',
            'Independence of original castings across splits is unverified; exact-byte checks cannot exclude near-duplicates.',
            'The model classifies a whole image and provides no localized fault boxes.',
            'Not a classifier of assembled pump operating condition or arbitrary tablet camera views.',
            'Source dataset has CC BY-NC-ND 4.0 restrictions; model card MIT does not replace dataset terms.'])
    (out/'predictions.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    report['predictionsSha256']=hashlib.sha256((out/'predictions.json').read_bytes()).hexdigest()
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='exactDuplicateFiles'},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
