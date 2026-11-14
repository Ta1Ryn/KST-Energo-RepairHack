#!/usr/bin/env python3
"""Freeze confirmed review annotations, retaining all previous evaluation photos."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import zipfile

from PIL import Image
import yaml

ROOT = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--base', type=Path, default=ROOT/'ComputerVision/datasets/pump-expanded-20261114-v1')
    parser.add_argument('--review', type=Path, default=ROOT/'ComputerVision/datasets/defects-review-200-v1')
    parser.add_argument('--output', type=Path, default=ROOT/'ComputerVision/datasets/pump-reviewed-200-20261114-v1')
    args = parser.parse_args()
    base, out, review = args.base.resolve(), args.output.resolve(), args.review.resolve()
    if out.exists():
        raise ValueError('Frozen dataset already exists; use a new output version.')
    summary = json.loads((base/'summary.json').read_text())
    provenance = json.loads((base/'provenance.json').read_text())
    local = json.loads((review/'annotations.json').read_text())
    catalog = json.loads((review/'catalog.json').read_text())
    sources = json.loads((ROOT/'ExternalData/defects-200-v1/selected-manifest.json').read_text())
    sources = {x['file'].removeprefix('selected/'): x for x in sources['images']}
    mapping = {c['annotationId']: c['trainingId'] for c in summary['classes']}
    seen = {r['pixelSha256'] for rows in provenance.values() for r in rows}
    names = {r['file'] for rows in provenance.values() for r in rows}
    additions, duplicates = [], []
    export_bytes = args.export.read_bytes()
    with zipfile.ZipFile(io.BytesIO(export_bytes)) as archive:
        if archive.testzip():
            raise ValueError('Corrupt annotation archive')
        manifest = json.loads(archive.read('manifest.json'))
        state = json.loads(archive.read('annotations.json'))
        if state != local or state['classes'] != catalog['classes']:
            raise ValueError('Saved annotations and export differ; take a fresh export.')
        accepted = {key for key, value in state['images'].items() if value['status'] in ('labeled', 'negative')}
        if accepted != {Path(item['file']).stem for item in manifest['images']}:
            raise ValueError('Export does not contain every confirmed photo')
        for item in manifest['images']:
            name = item['file']
            annotation = state['images'][Path(name).stem]
            if not item['reviewed'] or annotation['status'] not in ('labeled', 'negative'):
                raise ValueError('Unconfirmed photograph in export')
            if item['pixelSha256'] in seen:
                duplicates.append(dict(file=name, pixelSha256=item['pixelSha256']))
                continue
            if name in names:
                raise ValueError('Image filename collides with baseline: '+name)
            data = archive.read('images/'+name)
            if data != (review/'images'/name).read_bytes():
                raise ValueError('Photo differs from the annotation workspace')
            source = sources[item['sourcePath']]
            if source['selectedSha256'] != item['originalSha256']:
                raise ValueError('Selected source photograph changed')
            lines = []
            for b in annotation['boxes']:
                if b['classId'] not in mapping:
                    raise ValueError('New class needs explicit model mapping')
                x, y, w, h = (b[k] for k in ('x', 'y', 'w', 'h'))
                if not all(math.isfinite(v) for v in (x, y, w, h)) or x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > 1.000001 or y+h > 1.000001:
                    raise ValueError('Invalid confirmed box')
                lines.append(f'{mapping[b["classId"]]} {x+w/2:.8f} {y+h/2:.8f} {w:.8f} {h:.8f}')
            if (annotation['status'] == 'negative') != (not lines):
                raise ValueError('Annotation status does not match boxes')
            label = ('\n'.join(lines)+('\n' if lines else '')).encode()
            with Image.open(io.BytesIO(data)) as image:
                pixels = image.convert('RGB')
                decoded_hash = sha(str(pixels.size).encode()+pixels.tobytes())
            record = dict(file=name, source=review.name+'/'+item['sourcePath'],
                group='review200/'+(annotation['group'] or source['sourceGroup']),
                sourceGroup=source['sourceGroup'], sourcePage=source['sourcePage'], license=source['license'],
                sha256=sha(data), pixelSha256=item['pixelSha256'], decodedPixelSha256=decoded_hash,
                labelsSha256=sha(label), annotationRevision=annotation['revision'], annotationStatus=annotation['status'])
            additions.append((name, data, label, record))
            seen.add(item['pixelSha256'])
            names.add(name)
    if not additions:
        raise ValueError('No new confirmed photos')
    # Mutations begin only after every exported photo and annotation has been verified.
    shutil.copytree(base, out, ignore=shutil.ignore_patterns('*.cache', '*.zip'))
    for name, data, label, record in additions:
        (out/'images/train'/name).write_bytes(data)
        (out/'labels/train'/Path(name).with_suffix('.txt')).write_bytes(label)
        provenance['train'].append(record)
    snapshot_dir = review/'exports'
    snapshot_dir.mkdir(exist_ok=True)
    snapshot = snapshot_dir/(review.name+'-user-'+sha(export_bytes)[:12]+'.zip')
    if not snapshot.exists():
        snapshot.write_bytes(export_bytes)
    (out/'reviewed-user-export.zip').write_bytes(export_bytes)
    (out/'reviewed-annotations.json').write_text(json.dumps(local, ensure_ascii=False, indent=2))
    statuses = Counter(local['images'].get(x['id'], {}).get('status', 'unreviewed') for x in catalog['images'])
    counts = {subset: Counter() for subset in provenance}
    class_images = Counter()
    class_groups = {c['trainingId']: set() for c in summary['classes']}
    negatives = 0
    for subset, rows in provenance.items():
        for row in rows:
            lines = (out/'labels'/subset/Path(row['file']).with_suffix('.txt')).read_text().splitlines()
            ids = [int(line.split()[0]) for line in lines]
            counts[subset].update(ids)
            negatives += not ids
            class_images.update(set(ids))
            for cid in set(ids):
                class_groups[cid].add(row['group'])
    snapshot_info = dict(batch=review.name, source=str(snapshot), sha256=sha(export_bytes),
        images=len(additions), confirmedImages=len(accepted), excludedStatuses=dict(statuses), duplicates=duplicates)
    summary.update(createdAt=datetime.now(timezone.utc).isoformat(), baseDataset=base.name,
        images=sum(map(len, provenance.values())), sourceImages=sum(map(len, provenance.values())),
        boxes=sum(sum(c.values()) for c in counts.values()), negatives=negatives,
        groups=len({r['group'] for rows in provenance.values() for r in rows}),
        splitImages={key: len(rows) for key, rows in provenance.items()},
        archiveSha256=sha(export_bytes), sourceBatches=summary.get('sourceBatches', [])+[snapshot_info],
        reviewBatch=snapshot_info, limitations=[
            'Original 5 validation and 12 test images are byte-for-byte unchanged; new confirmed photos are training-only.',
            'Uncertain and unreviewed photos are excluded, without changing the saved user annotations.',
            'Source capture series are kept together; independent physical equipment units are not confirmed.',
            'New confirmed boxes cover corrosion, wear and mechanical damage; this batch has no new crack boxes.',
            'The original holdout is small and has been examined previously; this is a pilot, not industrial accuracy validation.'
        ])
    for c in summary['classes']:
        cid = c['trainingId']
        c.update(boxes=sum(counts[key][cid] for key in counts), images=class_images[cid],
            groups=len(class_groups[cid]), splitBoxes={key: counts[key][cid] for key in counts})
    config = yaml.safe_load((base/'data.yaml').read_text())
    config['path'] = str(out)
    (out/'data.yaml').write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    for name, value in (('summary.json', summary), ('provenance.json', provenance)):
        (out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    for subset in ('val', 'test'):
        for record in provenance[subset]:
            for folder in ('images', 'labels'):
                name = record['file'] if folder == 'images' else str(Path(record['file']).with_suffix('.txt'))
                if (out/folder/subset/name).read_bytes() != (base/folder/subset/name).read_bytes():
                    raise ValueError('Evaluation photo or label changed')
    print(json.dumps({k: summary[k] for k in ('images', 'boxes', 'negatives', 'splitImages', 'reviewBatch')}, ensure_ascii=False), flush=True)
    print(out/'data.yaml', flush=True)


if __name__ == '__main__':
    main()
