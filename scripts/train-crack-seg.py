#!/usr/bin/env python3
"""Train an isolated crack segmentation pilot with real mask evaluation and progress."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]


def write(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));temp.replace(path)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,default=ROOT/'ComputerVision/datasets/crack-seg-ultralytics-v1/data.yaml')
    parser.add_argument('--weights',type=Path,default=ROOT/'ComputerVision/models/crack.pt')
    parser.add_argument('--epochs',type=int,default=20)
    parser.add_argument('--batch',type=int,default=4)
    parser.add_argument('--device',default='0')
    parser.add_argument('--patience',type=int,default=6)
    parser.add_argument('--learning-rate',type=float,default=.0003)
    args=parser.parse_args();data=args.data.resolve()
    summary=json.loads((data.parent/'summary.json').read_text())
    provenance=json.loads((data.parent/'provenance.json').read_text())
    hashes=set();groups=set()
    for split in ('train','val','test'):
        new_groups={r['group'] for r in provenance[split]}
        if groups & new_groups:raise ValueError('Source image family crosses dataset splits')
        groups.update(new_groups)
        for r in provenance[split]:
            image=data.parent/'images'/split/r['file'];label=data.parent/'labels'/split/Path(r['file']).with_suffix('.txt')
            if hashlib.sha256(image.read_bytes()).hexdigest()!=r['sha256'] or hashlib.sha256(label.read_bytes()).hexdigest()!=r['labelsSha256']:
                raise ValueError('Frozen image or segmentation label changed')
            if r['pixelSha256'] in hashes:raise ValueError('Duplicate pixels in frozen dataset')
            hashes.add(r['pixelSha256'])
    import torch
    from ultralytics import YOLO
    torch.set_num_threads(4)
    if args.device!='cpu' and not torch.cuda.is_available():raise RuntimeError('CUDA unavailable; run with GPU access')
    model=YOLO(str(args.weights.resolve()))
    if model.task!='segment' or list(model.names.values())!=['crack']:raise ValueError('A one-class crack segmentation checkpoint is required')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    run=ROOT/'ComputerVision/runs'/('crack-seg-'+stamp);run.mkdir(parents=True)
    pointer=ROOT/'.integration/crack-seg-training.json';status=run/'status.json';started=time.monotonic()
    info=dict(state='starting',task='segment',pilot=True,independenceConfirmed=False,epoch=0,completedEpochs=0,
        epochs=args.epochs,batch=0,batchesPerEpoch=0,pid=os.getpid(),data=str(data),output=str(run),startedAt=stamp,
        initialWeights=str(args.weights.resolve()),initialWeightsSha256=hashlib.sha256(args.weights.read_bytes()).hexdigest(),
        device=torch.cuda.get_device_name(int(args.device)) if args.device!='cpu' else 'CPU',
        datasetImages=summary['splitImages'],sourceArchiveSha256=summary['archiveSha256'],
        learningRate=args.learning_rate,patience=args.patience,limitations=summary['limitations'])
    last_write=0
    def publish():
        nonlocal last_write
        info['elapsedSeconds']=round(time.monotonic()-started,1)
        write(status,info);write(pointer,dict(statusFile=str(status),**info));last_write=time.monotonic()
    def epoch_start(trainer):
        # Frozen feature layers keep the pretrained batch statistics during this pilot.
        for layer in trainer.model.modules():
            if isinstance(layer,torch.nn.modules.batchnorm._BatchNorm) and not any(p.requires_grad for p in layer.parameters()):layer.eval()
        info.update(state='training',epoch=trainer.epoch+1,batch=0,batchesPerEpoch=len(trainer.train_loader));publish()
    def batch_end(trainer):
        info['batch']+=1
        if time.monotonic()-last_write>=5:publish()
    def epoch_end(trainer):
        metrics={k:float(v) for k,v in trainer.metrics.items() if isinstance(v,(int,float)) and math.isfinite(v)}
        info.update(state='training',epoch=trainer.epoch+1,completedEpochs=trainer.epoch+1,metrics=metrics);publish()
    publish();model.add_callback('on_train_epoch_start',epoch_start);model.add_callback('on_train_batch_end',batch_end);model.add_callback('on_fit_epoch_end',epoch_end)
    try:
        model.train(data=str(data),epochs=args.epochs,patience=args.patience,imgsz=640,batch=args.batch,nbs=8,
            device=args.device,workers=2,project=str(run.parent),name=run.name,exist_ok=True,
            optimizer='AdamW',lr0=args.learning_rate,warmup_bias_lr=0.0,freeze=10,amp=False,
            seed=42,deterministic=True,cache=False,plots=True,save=True,save_period=5,
            fliplr=.5,flipud=0.,degrees=5.,mosaic=.2,close_mosaic=5,mixup=0.,mask_ratio=4,
            hsv_h=.01,hsv_s=.2,hsv_v=.2)
        best=Path(model.trainer.best)
        if not best.is_file():raise RuntimeError('Training produced no best checkpoint')
        info.update(state='evaluating',weights=str(best));publish()
        candidate=YOLO(str(best))
        if candidate.task!='segment' or list(candidate.names.values())!=['crack']:raise ValueError('Checkpoint task or classes changed')
        # Test masks are evaluated after validation has selected the best checkpoint.
        result=candidate.val(data=str(data),split='test',imgsz=640,batch=args.batch,device=args.device,
            workers=2,plots=True,project=str(run),name='heldout-test')
        report=dict(task='segment',pilot=True,independenceConfirmed=False,weights=str(best),
            sha256=hashlib.sha256(best.read_bytes()).hexdigest(),classes=['crack'],testImages=summary['splitImages']['test'],
            boxMap50=float(result.box.map50),boxMap50_95=float(result.box.map),
            maskMap50=float(result.seg.map50),maskMap50_95=float(result.seg.map),
            limitations=summary['limitations'])
        write(run/'evaluation.json',report)
        with (run/'results.csv').open() as f:rows=list(csv.DictReader(f))
        info.update(state='complete',epoch=int(rows[-1]['epoch']),completedEpochs=int(rows[-1]['epoch']),
            weights=str(best),report=str(run/'evaluation.json'));publish()
        print(json.dumps(dict(run=str(run),epochs=info['epoch'],evaluation=report),ensure_ascii=False,indent=2),flush=True)
    except BaseException as error:
        info.update(state='failed',error=str(error));publish();raise


if __name__=='__main__':main()
