#!/usr/bin/env python3
"""Freeze 200 source photographs for review, with hashes and attribution."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'ExternalData/defects-200-v1'
SELECTED = SOURCE / 'selected'


def read(name):
    return json.loads((SOURCE / name).read_text())['images']


def visible(item):
    with Image.open(SOURCE / item['file']) as image:
        gray = np.asarray(image.convert('L').resize((160, 160)))
    return float(gray.mean()) >= 52 and float((gray < 8).mean()) < 0.28


def defect_area(item):
    areas = []
    for annotation in item.get('sourceAnnotations', []):
        if annotation['category_id'] not in (1, 2, 3):
            continue
        box = annotation.get('bbox', [0, 0, 0, 0])
        areas.append(annotation.get('area', box[2] * box[3]))
    return max(areas, default=0) / (item['width'] * item['height'])


def main():
    if (SOURCE / 'selected-manifest.json').exists():
        raise ValueError('This review batch is frozen; keep it and use a new version for changes.')
    cracks = [x for x in read('crack-part-candidates.json') if min(x['width'], x['height']) >= 220]
    pipes = sorted((x for x in read('gas-original-candidates.json') if visible(x)),
                   key=lambda x: (-defect_area(x), x['sourcePath']))
    parts = read('mpdd-candidates.json')
    # Interleave different metal-part conditions so a single view does not fill the batch.
    buckets = {}
    for item in parts:
        buckets.setdefault((item.get('hint'), item.get('sourceCondition')), []).append(item)
    metal = []
    for index in range(max(map(len, buckets.values()))):
        for bucket in buckets.values():
            if index < len(bucket):
                metal.append(bucket[index])
    chosen, seen, near_duplicates = [], set(), []
    def add(item, category, near_check=False):
        if item['pixelSha256'] in seen:
            return False
        if near_check and any(other['source'] == item['source'] and
                              (int(other['dhash'], 16) ^ int(item['dhash'], 16)).bit_count() <= 3
                              for other, _ in chosen):
            near_duplicates.append(item['id'])
            return False
        seen.add(item['pixelSha256'])
        chosen.append((item, category))
        return True
    for item in cracks:
        add(item, '01 · Трещины на деталях')
    pipe_count = 0
    for item in pipes:
        if pipe_count >= 150:
            break
        pipe_count += add(item, '02 · Трубы и покрытие', near_check=True)
    for item in metal:
        if len(chosen) >= 200:
            break
        category = ('03 · Коррозия на металле' if item['hint'] == 'corrosion' else
                    '05 · Детали без отмеченных дефектов' if item['hint'] == 'normal-candidate' else
                    '04 · Повреждения и износ деталей')
        add(item, category)
    if len(chosen) != 200:
        raise ValueError(f'Only {len(chosen)} suitable distinct photographs; do not fill with duplicates.')
    selected = []
    for number, (item, category) in enumerate(chosen, 1):
        destination = SELECTED / category / f"{number:03d}-{item['id']}.jpg"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(SOURCE / item['file']) as original:
            pixels = ImageOps.exif_transpose(original).convert('RGB')
            pixels.save(destination, 'JPEG', quality=95)
            selected_hash = hashlib.sha256(str(pixels.size).encode() + pixels.tobytes()).hexdigest()
        row = dict(item, reviewIndex=number, category=category,
                   originalFile=item['file'], file=destination.relative_to(SOURCE).as_posix(),
                   selectedPixelSha256BeforeJpeg=selected_hash,
                   selectedSha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                   manualLabelsConfirmed=False)
        selected.append(row)
    manifest = dict(createdAt=datetime.now(timezone.utc).isoformat(), images=selected,
                    count=200, sourceCounts=Counter(x['source'] for x in selected),
                    categoryCounts=Counter(x['category'] for x in selected),
                    labelsVerified=False, excludedNearDuplicateIds=near_duplicates,
                    limitations=[
                        'This is a review batch, not 200 confirmed defective or independent equipment units.',
                        'Source conditions and masks are review hints; all manual annotations start unreviewed.',
                        'Magnetic-tile crack photographs show ferrite parts, not pump housings.',
                        'Pipe photographs cover corrosion, coating degradation and deformations; do not call pipe joints cracks.',
                        'Keep source capture series together; do not create a validation/test split from neighbouring views.',
                        'Existing user-labelled photographs and active model weights are unchanged.'
                    ])
    (SOURCE / 'selected-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (SOURCE / 'ATTRIBUTION.txt').write_text('\n\n'.join('\n'.join([
        x['file'], x['sourcePath'], x['author'], x['sourcePage'], x['license']]) for x in selected) + '\n')
    (SOURCE / 'README.txt').write_text(
        '200 фотографий для ручной проверки и разметки.\n'
        'Открыть Разметка-200-фото.sh из корня проекта.\n'
        'Выберите видимый дефект и обведите только повреждённую область.\n'
        'Для чистой детали подтвердите отсутствие видимых дефектов.\n'
        'Если трещина, стык, грязь или повреждение покрытия неразличимы — «Не уверен».\n'
        'Названия категорий и авторская разметка не заменяют проверку фотографии.\n'
        'Это фотографии отдельных деталей и труб; число фото не означает число независимых насосов.\n'
        'Экспорт содержит только подтверждённые рамки и снимки без видимых дефектов.\n'
        'Для оценки после обучения нужны новые фотографии ваших насосов.\n'
        'Ссылки и условия источников: ATTRIBUTION.txt и selected-manifest.json.\n')
    print(json.dumps({k: manifest[k] for k in ('count', 'sourceCounts', 'categoryCounts')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
