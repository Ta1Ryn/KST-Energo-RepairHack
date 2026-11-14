#!/usr/bin/env python3
"""Train an isolated pilot checkpoint; never replace the working CV model automatically."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def run_queue(path):
    """Run prepared training jobs serially on the GPU, with durable status and logs."""
    plan = json.loads(path.read_text())
    jobs = plan['jobs']
    if not jobs or len(jobs) > 12:
        raise ValueError('A training queue must contain 1 to 12 jobs')
    commands = []
    for job in jobs:
        if not re.fullmatch(r'[a-z0-9-]{1,60}', job['id']):
            raise ValueError('Invalid queue job ID')
        data, weights = Path(job['data']).resolve(), Path(job['weights']).resolve()
        if not data.is_file() or not weights.is_file():
            raise ValueError('Queue inputs must exist before training starts')
        if not 1 <= job['epochs'] <= 200 or not 1 <= job['batch'] <= 32 or not 1 <= job['patience'] <= 100 or not 0 < job['learningRate'] <= .01:
            raise ValueError('Invalid queue training settings')
        command = [sys.executable, str(Path(__file__).resolve()), '--data', str(data), '--weights', str(weights),
                   '--epochs', str(job['epochs']), '--batch', str(job['batch']), '--device', str(job.get('device', '0')),
                   '--patience', str(job['patience']), '--learning-rate', str(job['learningRate']),
                   '--run-prefix', job['id'], '--status-name', job['id']+'-training.json', '--keep-frozen-bn']
        commands.append(command)
    if len({job['id'] for job in jobs}) != len(jobs):
        raise ValueError('Duplicate queue job IDs')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    report_path = ROOT/'.integration/roboflow-training-queue.json'
    logs = ROOT/'ComputerVision/runs'/('roboflow-queue-'+stamp)
    logs.mkdir(parents=True)
    state = dict(state='starting', pid=os.getpid(), startedAt=stamp, plan=str(path), logs=str(logs),
                 completedJobs=0, jobs=[dict(id=job['id'], state='queued', epochs=job['epochs'], data=job['data']) for job in jobs])
    write_json(report_path, state)
    try:
        for index, (job, command) in enumerate(zip(jobs, commands)):
            entry = state['jobs'][index]
            log = logs/(job['id']+'.log')
            pointer = ROOT/'.integration'/(job['id']+'-training.json')
            entry.update(state='starting', log=str(log))
            state.update(state='training', currentJob=job['id'])
            write_json(report_path, state)
            print('Starting '+job['id']+'; progress: '+str(pointer), flush=True)
            with log.open('w') as stream:
                process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
                entry['pid'] = process.pid
                while process.poll() is None:
                    if pointer.is_file():
                        progress = json.loads(pointer.read_text())
                        if progress.get('pid') == process.pid:
                            entry.update({key: progress[key] for key in ('state', 'epoch', 'completedEpochs', 'batch', 'batchesPerEpoch', 'elapsedSeconds', 'output', 'metrics') if key in progress})
                    write_json(report_path, state)
                    time.sleep(5)
            entry.update(state='complete' if process.returncode == 0 else 'failed', exitCode=process.returncode)
            if pointer.is_file():
                progress = json.loads(pointer.read_text())
                if progress.get('pid') == process.pid:
                    entry.update({key: progress[key] for key in ('epoch', 'completedEpochs', 'elapsedSeconds', 'output', 'weights', 'report', 'error') if key in progress})
            state['completedJobs'] += process.returncode == 0
            write_json(report_path, state)
            print(json.dumps(entry, ensure_ascii=False), flush=True)
        state.update(state='complete' if state['completedJobs'] == len(jobs) else 'complete_with_errors', currentJob=None)
        write_json(report_path, state)
    except BaseException as error:
        state.update(state='failed', error=str(error))
        write_json(report_path, state)
        raise
    print('Queue status: '+str(report_path), flush=True)
    if state['completedJobs'] != len(jobs):
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path)
    parser.add_argument('--queue-file', type=Path, help='Run a reviewed JSON training plan serially')
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--batch', type=int, default=2)
    parser.add_argument('--device', default='0')
    parser.add_argument('--weights',type=Path,default=ROOT/'ComputerVision/yolo11n.pt')
    parser.add_argument('--learning-rate',type=float,default=.001)
    parser.add_argument('--patience',type=int,default=15)
    parser.add_argument('--keep-frozen-bn',action='store_true',help='Keep batch statistics of frozen feature layers from the initial checkpoint')
    parser.add_argument('--run-prefix',default='case-pilot')
    parser.add_argument('--status-name',default='case-training.json')
    args = parser.parse_args()
    if args.queue_file:
        if args.data:
            parser.error('Pass either --data or --queue-file')
        run_queue(args.queue_file.resolve())
        return
    if not args.data:
        parser.error('--data is required for an individual training job')
    if not re.fullmatch(r'[a-z0-9-]{1,60}',args.run_prefix) or not re.fullmatch(r'[a-z0-9-]{1,60}\.json',args.status_name):
        raise ValueError('Invalid run or status name')
    pointer=ROOT/'.integration'/args.status_name
    data = args.data.resolve()
    summary = json.loads((data.parent/'summary.json').read_text())
    provenance = json.loads((data.parent/'provenance.json').read_text())
    used_groups, used_hashes = set(), set()
    for subset in ('train','val','test'):
        groups = {r['group'] for r in provenance[subset]}
        if groups & used_groups:
            raise ValueError('Groups cross dataset splits')
        used_groups.update(groups)
        for record in provenance[subset]:
            path = data.parent/'images'/subset/record['file']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256'] or record['pixelSha256'] in used_hashes:
                raise ValueError('Modified or duplicated image')
            label=data.parent/'labels'/subset/Path(record['file']).with_suffix('.txt')
            if hashlib.sha256(label.read_bytes()).hexdigest()!=record['labelsSha256']:
                raise ValueError('Annotations changed after dataset preparation')
            used_hashes.add(record['pixelSha256'])
    import torch
    from ultralytics import YOLO
    torch.set_num_threads(4)
    if args.device!='cpu' and not torch.cuda.is_available():
        raise RuntimeError('CUDA unavailable. Run with GPU access or explicitly pass --device cpu.')
    device_name = torch.cuda.get_device_name(int(args.device)) if args.device!='cpu' else 'CPU'
    print('Training device:',device_name,flush=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    run_dir=ROOT/'ComputerVision/runs'/(args.run_prefix+'-'+stamp)
    run_dir.mkdir(parents=True)
    metadata=dict(state='starting',task='detect',pilot=True,independenceConfirmed=False,device=device_name,pid=os.getpid(),
                  epochs=args.epochs,epoch=0,completedEpochs=0,batch=0,batchesPerEpoch=0,data=str(data),output=str(run_dir),startedAt=stamp,
                  sourceArchiveSha256=summary['archiveSha256'],limitations=summary['limitations'])
    status=run_dir/'status.json'
    write_json(status,metadata)
    write_json(pointer,dict(statusFile=str(status),**metadata))
    model=YOLO(str(args.weights.resolve()))
    metadata.update(initialWeights=str(args.weights.resolve()),initialWeightsSha256=hashlib.sha256(args.weights.read_bytes()).hexdigest(),
                    learningRate=args.learning_rate,patience=args.patience,keepFrozenBatchNorm=args.keep_frozen_bn,batchSize=args.batch,
                    datasetImages=summary['splitImages'])
    started=time.monotonic()
    last_write=0
    def publish():
        nonlocal last_write
        metadata['elapsedSeconds']=round(time.monotonic()-started,1)
        write_json(status,metadata)
        write_json(pointer,dict(statusFile=str(status),**metadata))
        last_write=time.monotonic()
    def epoch_start(trainer):
        metadata.update(state='training',epoch=trainer.epoch+1,batch=0,batchesPerEpoch=len(trainer.train_loader))
        publish()
    def batch_end(trainer):
        metadata['batch']+=1
        if time.monotonic()-last_write>=5:
            publish()
    def after_epoch(trainer):
        metrics={key:float(value) for key,value in trainer.metrics.items() if isinstance(value,(int,float)) and math.isfinite(value)}
        metadata.update(state='training',epoch=trainer.epoch+1,completedEpochs=trainer.epoch+1,metrics=metrics)
        publish()
    model.add_callback('on_train_epoch_start',epoch_start)
    model.add_callback('on_train_batch_end',batch_end)
    model.add_callback('on_fit_epoch_end',after_epoch)
    if args.keep_frozen_bn:
        def preserve_frozen_statistics(trainer):
            for layer in trainer.model.modules():
                if isinstance(layer,torch.nn.modules.batchnorm._BatchNorm) and not any(p.requires_grad for p in layer.parameters()):
                    layer.eval()
        model.add_callback('on_train_epoch_start',preserve_frozen_statistics)
    try:
        model.train(data=str(data),epochs=args.epochs,patience=args.patience,imgsz=640,batch=args.batch,nbs=8,
                    device=args.device,workers=2,project=str(run_dir.parent),name=run_dir.name,exist_ok=True,
                    pretrained=True,optimizer='AdamW',lr0=args.learning_rate,warmup_bias_lr=0.0,freeze=10,amp=False,seed=42,deterministic=True,
                    cache=False,plots=True,save=True,save_period=10,
                    fliplr=0.0,flipud=0.0,degrees=5.0,mosaic=.2,close_mosaic=10,mixup=0.0,
                    hsv_h=.01,hsv_s=.25,hsv_v=.25)
        best=Path(model.trainer.best)
        if not best.exists():
            raise RuntimeError('No best.pt checkpoint')
        trained=YOLO(str(best))
        expected=[entry['code'] for entry in summary['classes']]
        if [trained.names[i] for i in range(len(trained.names))]!=expected:
            raise RuntimeError('Checkpoint class order mismatch')
        metadata.update(state='evaluating',weights=str(best))
        publish()
        # Test is accessed only after training/model selection on validation has ended.
        result=trained.val(data=str(data),split='test',imgsz=640,batch=args.batch,device=args.device,
                           workers=2,plots=True,project=str(run_dir),name='heldout-test')
        per_class=[]
        for i,cid in enumerate(result.box.ap_class_index):
            cid=int(cid)
            per_class.append(dict(code=expected[cid],precision=float(result.box.p[i]),recall=float(result.box.r[i]),
                                  map50=float(result.box.ap50[i]),map50_95=float(result.box.ap[i]),
                                  testBoxes=summary['classes'][cid]['splitBoxes']['test']))
        report=dict(pilot=True,independenceConfirmed=False,weights=str(best),sha256=hashlib.sha256(best.read_bytes()).hexdigest(),
                    classes=expected,testImages=summary['splitImages']['test'],map50=float(result.box.map50),
                    map50_95=float(result.box.map),perClass=per_class,
                    unmeasuredClasses=[entry['code'] for entry in summary['classes'] if not entry['splitBoxes']['test']],
                    limitations=summary['limitations'])
        write_json(run_dir/'evaluation.json',report)
        with (run_dir/'results.csv').open() as stream:
            completed_epochs=list(csv.DictReader(stream))
        metadata.update(state='complete',epoch=int(completed_epochs[-1]['epoch']),completedEpochs=int(completed_epochs[-1]['epoch']),weights=str(best),report=str(run_dir/'evaluation.json'),
                        elapsedSeconds=round(time.monotonic()-started,1))
        publish()
        print(json.dumps(metadata,ensure_ascii=False,indent=2),flush=True)
    except BaseException as error:
        metadata.update(state='failed',error=str(error),elapsedSeconds=round(time.monotonic()-started,1))
        publish()
        raise


if __name__=='__main__':
    main()
