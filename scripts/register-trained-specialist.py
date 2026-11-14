#!/usr/bin/env python3
"""Register a verified local training result without changing the selected models."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ComputerVision'))
from model_engine import load_model

SPECS = {
    'crack': dict(id='crack_trained', name='Трещины · Crack-Seg · дообучение', classes=['crack'],
                  domain='Crack-Seg: дороги и стены. Перенос на насосы и металл проверяется отдельно.'),
    'oil': dict(id='oil_trained', name='Следы масла · Roboflow + наша разметка', classes=['leak'],
                domain='Дообучена на Oil Leak Detection и подтверждённых фото пользователя. Состав жидкости и источник течи по фото не подтверждаются.'),
    'corrosion': dict(id='corrosion_trained', name='Коррозия · Roboflow + наша разметка', classes=['corrosion'],
                      domain='Дообучена на corrosion v5 и подтверждённой разметке пользователя. Исходные маски использованы как рамки; степень потери металла не измеряется.'),
    'corrosion_adapt': dict(id='corrosion_adapt_trained', name='Коррозия · адаптация на наших фото', classes=['corrosion'],
                           domain='Второй этап после corrosion v5: дообучение только на подтверждённых фото пользователя. По прежним 12 контрольным фото найдено 4/13 меток, несовпавших рамок 5. Проверяется отдельно от первого этапа.'),
    'damage': dict(id='damage_trained', name='Повреждения · лопасти + наша разметка',
                   classes=['crack', 'mechanical_damage', 'wear'],
                   domain='Трещины, повреждения и эрозия лопастей плюс подтверждённые фото пользователя. Отдельной проверки деформации или соосности валов нет.'),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--kind', choices=SPECS, required=True)
    args = parser.parse_args()
    run = args.run.resolve()
    status = json.loads((run/'status.json').read_text())
    report = json.loads((run/'evaluation.json').read_text())
    if status['state'] != 'complete':
        raise ValueError('Training and held-out evaluation must finish before registration')
    weights = run/'weights/best.pt'
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    spec = SPECS[args.kind]
    if digest != report['sha256'] or report['classes'] != spec['classes']:
        raise ValueError('Evaluation does not match this checkpoint or supported classes')
    model = load_model(weights)
    if list(model.names.values()) != spec['classes'] or model.task != report.get('task', 'detect'):
        raise ValueError('Checkpoint task or class order mismatch')
    prediction = model.predict(np.zeros((128,128,3), dtype=np.uint8), imgsz=640,
                               device='cpu', conf=.25, verbose=False)[0]
    if prediction.boxes is None:
        raise ValueError('Checkpoint cannot produce detection results')
    model_dir = ROOT/'ComputerVision/models'
    catalog_path = model_dir/'catalog.json'
    catalog = json.loads(catalog_path.read_text())
    selection_path = ROOT/'ComputerVision/model-selection.local.json'
    prior_selection = selection_path.read_bytes() if selection_path.exists() else None
    backup = model_dir/'archive/training-before-20261114'
    backup.mkdir(parents=True, exist_ok=True)
    if not (backup/'catalog.json').exists():
        shutil.copy2(catalog_path, backup/'catalog.json')
    filename = args.kind+'-trained-'+digest[:12]+'.pt'
    destination = model_dir/filename
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        raise ValueError('Existing versioned weights changed')
    if not destination.exists():
        shutil.copy2(weights, destination)
    data = Path(status['data']).parent
    summary = json.loads((data/'summary.json').read_text())
    entry = dict(id=spec['id'], name=spec['name'], file='models/'+filename, sha256=digest,
                 experimental=True, task=model.task, sourceRun=run.name,
                 trainingData=str(data), trainingReport=str(run/'evaluation.json'),
                 splitImages=summary['splitImages'], domain=spec['domain'],
                 mapping={code: code for code in spec['classes']}, ignored=[])
    if 'source' in summary:
        entry['sourceUrl'] = summary['source']['url']
    catalog = [old for old in catalog if old['id'] != entry['id']]+[entry]
    temporary = catalog_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2)+'\n')
    temporary.replace(catalog_path)
    if prior_selection != (selection_path.read_bytes() if selection_path.exists() else None):
        raise ValueError('Selected model configuration unexpectedly changed')
    print(json.dumps(dict(id=entry['id'], weights=str(destination), sha256=digest,
                          task=model.task, classes=spec['classes'], catalogModels=len(catalog)), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
