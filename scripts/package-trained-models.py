#!/usr/bin/env python3
"""Package the verified model catalog, completed training reports and actual user feedback."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CV = ROOT / 'ComputerVision'
OUTPUT = ROOT.parents[1] / 'КСТ_модели_и_сравнение_2026-11-14.zip'
RUNS = (
    'roboflow-oil-20261114-170338',
    'roboflow-damage-20261114-170823',
    'roboflow-corrosion-20261114-171510',
    'corrosion-user-adapt-20261114-175313',
    'crack-seg-20261114-164154',
)


def sha(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def main():
    catalog_path = CV / 'models/catalog.json'
    catalog = json.loads(catalog_path.read_text())
    files = {catalog_path: 'ComputerVision/models/catalog.json'}
    checks = []
    for entry in catalog:
        path = CV / entry['file']
        actual = sha(path)
        if actual != entry['sha256']:
            raise ValueError('Checkpoint hash mismatch: ' + entry['id'])
        files[path] = 'ComputerVision/' + entry['file']
        checks.append(dict(id=entry['id'], sha256=actual, bytes=path.stat().st_size))

    completed = []
    for run in RUNS:
        folder = CV / 'runs' / run
        status = json.loads((folder / 'status.json').read_text())
        if status['state'] != 'complete':
            raise ValueError('Training is not complete: ' + run)
        completed.append(dict(run=run, epoch=status.get('epoch'), epochs=status.get('epochs'), state='complete'))
        for name in ('status.json', 'results.csv', 'results.png', 'args.yaml'):
            path = folder / name
            if path.exists():
                files[path] = 'ComputerVision/runs/' + run + '/' + name
        for path in sorted((CV / 'benchmarks' / run).glob('*.json')):
            files[path] = str(path.relative_to(ROOT))

    comparison = json.loads((ROOT / '.integration/training-comparison.json').read_text())
    offline = Path(comparison['offline'])
    if sha(offline) != comparison['offlineSha256']:
        raise ValueError('The standalone comparison changed')
    files[offline] = offline.name
    for name in ('training-comparison.json', 'training-comparison-verification.json', 'user-comparison-feedback-20261114.json'):
        path = ROOT / '.integration' / name
        files[path] = 'reports/' + name
    frozen = CV / 'datasets/case-training-ac35f818-v2'
    for name in ('summary.json', 'provenance.json'):
        files[frozen / name] = 'annotations/frozen-case/' + name
    for path in sorted((frozen / 'labels').rglob('*.txt')):
        files[path] = 'annotations/frozen-case/' + str(path.relative_to(frozen))

    selection = json.loads((CV / 'model-selection.local.json').read_text())
    selection = {key: selection[key] for key in ('models', 'threshold')}
    readme = '''КСТ Энерго · модели и сравнение · 14.11.2026

В архиве все 11 проверенных моделей, отчёты пяти новых этапов обучения,
сравнение на 12 исходных контрольных фотографиях и 27 оценок пользователя.
Веса каждого файла сверены с SHA-256 каталога. Новые модели сохранены отдельно.

Активный набор: oil, metal, case_pilot; порог уверенности 0.25.
Все новые модели доступны для сравнения, но автоматически не подменяют активные.

Обучение: масло — 25 эпох; повреждения — 25; коррозия — 30;
адаптация коррозии на наших фотографиях — ещё 20; Crack-Seg — ранняя остановка на 7 из 20.
Разметка пользователя сохранена. Полигоны публичной коррозии преобразованы
в прямоугольники для детектора; это обучение детекции, а не сегментации коррозии.

Контрольные фотографии насосов, confidence 0.25, IoU 0.5:
• Масло: совпадения с разметкой 0/2 → 0/2; несовпавшие рамки 7 → 1.
• Коррозия, первый этап: 4/13 → 1/13; несовпавшие рамки 1 → 8.
• Коррозия, адаптация: 4/13 → 4/13; несовпавшие рамки 1 → 5.
• Повреждения: трещины 0/5 → 0/5; механические повреждения 0/1 → 0/1.
• Износ: 0/2 → 0/2; несовпавшие рамки 6 → 0.
• Crack-Seg: 3/5 → 2/5; несовпавшие рамки 1 → 2.
Общее улучшение на насосах не подтверждено. Несовпавшая рамка означает
несовпадение с сохранённой разметкой при указанном IoU.

Оценки пользователя: коррозия — новые 5, старые 3; масло — новые 3, одинаково 4;
повреждения — новые 1, одинаково 2, старые 4; Crack-Seg — одинаково 4, старые 1.
Пустые оценки не считались отрицательными примерами; голоса не меняли разметку.

Откройте КСТ_сравнение_моделей_2026-11-14.html в браузере: изображения встроены.
Сохранённые пользовательские оценки находятся в reports/user-comparison-feedback-20261114.json.
Полный ранее созданный архив исходной разметки и фотографий остаётся рядом:
КСТ_разметка_и_обученные_веса_2026-11-14.zip.
'''
    manifest = dict(createdAt=datetime.now(timezone.utc).isoformat(), models=checks,
                    training=completed, activeSelection=selection, photos=12, topics=4, userReviews=27)
    with zipfile.ZipFile(OUTPUT, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr('Прочитай_меня.txt', readme)
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('ComputerVision/model-selection.local.json', json.dumps(selection, ensure_ascii=False, indent=2))
        for path, name in sorted(files.items(), key=lambda item: item[1]):
            archive.write(path, name)
    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip() is not None:
            raise ValueError('Archive integrity check failed')
        for entry in catalog:
            content = archive.read('ComputerVision/' + entry['file'])
            if hashlib.sha256(content).hexdigest() != entry['sha256']:
                raise ValueError('Packaged checkpoint hash mismatch: ' + entry['id'])
        count = len(archive.namelist())
    report = dict(archive=str(OUTPUT), sha256=sha(OUTPUT), bytes=OUTPUT.stat().st_size,
                  files=count, modelCount=len(checks), userReviews=27, integrity='PASS')
    (ROOT / '.integration/trained-model-delivery.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
