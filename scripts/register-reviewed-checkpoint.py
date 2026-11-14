#!/usr/bin/env python3
"""Make an evaluated pilot selectable for review; retain the working model selection."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True)
    args=parser.parse_args();run=args.run.resolve()
    status=json.loads((run/'status.json').read_text())
    evaluation=json.loads((run/'evaluation.json').read_text())
    if status['state']!='complete':raise ValueError('Run is incomplete')
    checkpoint=run/'weights/best.pt'
    digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if digest!=evaluation['sha256']:raise ValueError('Checkpoint checksum differs')
    comparison=ROOT/'ComputerVision/benchmarks'/run.name/'report.json'
    if not comparison.exists():raise ValueError('Baseline comparison is required')
    model_dir=ROOT/'ComputerVision/models';catalog_path=model_dir/'catalog.json'
    catalog=json.loads(catalog_path.read_text())
    backup=model_dir/'backups'/run.name;backup.mkdir(parents=True,exist_ok=True)
    if not (backup/'catalog.json').exists():shutil.copy2(catalog_path,backup/'catalog.json')
    target=model_dir/'reviewed200-pilot.pt'
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
        shutil.copy2(target,backup/'previous-reviewed200-pilot.pt')
    temp=target.with_suffix('.tmp');shutil.copy2(checkpoint,temp);temp.replace(target)
    entry=dict(id='reviewed200',name='Новая разметка · 130 фото · пилот',file='models/reviewed200-pilot.pt',
        sha256=digest,experimental=True,sourceRun=run.name,
        domain='Трубы и металлические детали. Дообучение на 224 фото; уверенное улучшение на старой контрольной выборке не подтверждено. Выбирайте для отдельной проверки.',
        mapping={code:code for code in evaluation['classes']},ignored=[])
    catalog=[old for old in catalog if old['id']!='reviewed200']+[entry]
    temp=catalog_path.with_suffix('.tmp');temp.write_text(json.dumps(catalog,ensure_ascii=False,indent=2));temp.replace(catalog_path)
    print('Reviewed pilot registered:',target)


if __name__=='__main__':main()
