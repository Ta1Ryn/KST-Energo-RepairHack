#!/usr/bin/env python3
"""Validate an annotation ZIP and create an immutable, provisionally grouped pilot split."""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
import math
from pathlib import Path
import random
import shutil
import zipfile

from PIL import Image
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--groups', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    digest = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    policy = json.loads(args.groups.read_text())
    if policy['archiveSha256'] != digest:
        raise ValueError('Grouping proposal belongs to another annotation archive.')
    if args.output.exists():
        raise ValueError('Output exists. Use a new directory; existing data will not be overwritten.')
    records = []
    with zipfile.ZipFile(args.archive) as archive:
        if archive.testzip() is not None:
            raise ValueError('Corrupt ZIP')
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError('Duplicate ZIP paths')
        manifest = json.loads(archive.read('manifest.json'))
        classes = json.loads(archive.read('classes.json'))
        if manifest['classes'] != [item['code'] for item in classes]:
            raise ValueError('Class definitions differ')
        group_map = {}
        forced_train = set()
        for group in policy['groups']:
            for source in group['sources']:
                if source in group_map:
                    raise ValueError('Source assigned to several groups: ' + source)
                group_map[source] = group['id']
            if group.get('trainOnly'):
                forced_train.add(group['id'])
        seen_names = set()
        for item in manifest['images']:
            name = item['file']
            if Path(name).name != name or name in seen_names or not name.endswith('.jpg'):
                raise ValueError('Invalid or duplicate image filename')
            seen_names.add(name)
            if item.get('reviewed') is not True:
                raise ValueError('Unreviewed image in export')
            content = archive.read('images/' + name)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != 'JPEG' or image.getexif().get(274, 1) != 1:
                    raise ValueError('Expected an orientation-normalized JPEG')
                image.verify()
            rows = []
            for line in archive.read('labels/' + Path(name).with_suffix('.txt').name).decode().splitlines():
                if not line.strip():
                    continue
                row = [float(value) for value in line.split()]
                if len(row) != 5 or not all(math.isfinite(value) for value in row):
                    raise ValueError('Invalid label')
                cid, x, y, width, height = row
                if not cid.is_integer() or not 0 <= cid < len(classes) or not (width > 0 and height > 0 and x-width/2 >= -1e-6 and y-height/2 >= -1e-6 and x+width/2 <= 1.000001 and y+height/2 <= 1.000001):
                    raise ValueError('Invalid class or bounding box')
                rows.append([int(cid), x, y, width, height])
            if bool(item['negative']) != (not rows):
                raise ValueError('Negative marker differs from labels')
            group = group_map.get(item['sourcePath']) or item.get('group')
            if not group:
                raise ValueError('Every image needs an explicit grouping proposal')
            records.append(dict(file=name, source=item['sourcePath'], group=group, rows=rows,
                                bytes=content, sha256=hashlib.sha256(content).hexdigest(), pixelSha256=item['pixelSha256']))
    if set(group_map) - {r['source'] for r in records}:
        raise ValueError('Grouping proposal contains unknown sources')
    # Exact re-exports must never cross splits; differing annotations need review.
    seen = {}
    unique = []
    duplicate_count = 0
    for record in records:
        fingerprint = record['pixelSha256']
        if fingerprint in seen:
            if seen[fingerprint]['rows'] != record['rows']:
                raise ValueError('Duplicate photo with conflicting annotations: ' + record['source'])
            duplicate_count += 1
            continue
        seen[fingerprint] = record
        unique.append(record)
    records = unique
    counts = Counter(row[0] for record in records for row in record['rows'])
    active = sorted(counts)
    mapping = {old: new for new, old in enumerate(active)}
    groups = defaultdict(list)
    for record in records:
        groups[record['group']].append(record)
    coverage = {cid: {r['group'] for r in records if any(row[0] == cid for row in r['rows'])} for cid in active}
    # A class occurring in a single scene cannot have a meaningful held-out test.
    for cid, present in coverage.items():
        if len(present) == 1:
            forced_train.update(present)
    candidates = sorted(set(groups) - forced_train)
    best = None
    for attempt in range(30000):
        rng = random.Random(42 + attempt)
        order = list(candidates)
        rng.shuffle(order)
        val_n = max(1, round(len(order)*.18))
        test_n = max(1, round(len(order)*.18))
        membership = dict.fromkeys(order[:val_n], 'val') | dict.fromkeys(order[val_n:val_n+test_n], 'test')
        split = {key: [] for key in ('train', 'val', 'test')}
        for group, rows in groups.items():
            split[membership.get(group, 'train')].extend(rows)
        supports = {key: Counter(row[0] for r in values for row in r['rows']) for key, values in split.items()}
        if any(not values for values in split.values()) or any(not supports['train'][cid] for cid in active):
            continue
        score = sum(abs(len(split[key])/len(records)-ratio) for key, ratio in [('train', .7), ('val', .15), ('test', .15)])
        score += sum(5 for key in ('val', 'test') for cid in active if len(coverage[cid]) >= 3 and not supports[key][cid])
        score += sum(8*max(0, .5-supports['train'][cid]/counts[cid]) for cid in active)
        if best is None or score < best[0]:
            best = (score, split, supports, attempt)
    if best is None:
        raise ValueError('Cannot split groups while retaining all classes in training. Collect more data.')
    _, split, supports, attempt = best
    output = args.output.resolve()
    output.mkdir(parents=True)
    provenance = {}
    for subset, items in split.items():
        (output/'images'/subset).mkdir(parents=True)
        (output/'labels'/subset).mkdir(parents=True)
        provenance[subset] = []
        for record in items:
            (output/'images'/subset/record['file']).write_bytes(record['bytes'])
            lines = [f"{mapping[row[0]]} " + ' '.join(f'{v:.8f}' for v in row[1:]) for row in record['rows']]
            (output/'labels'/subset/Path(record['file']).with_suffix('.txt')).write_text('\n'.join(lines)+ ('\n' if lines else ''))
            entry={k:v for k,v in record.items() if k not in {'bytes','rows'}}
            entry['labelsSha256']=hashlib.sha256((output/'labels'/subset/Path(record['file']).with_suffix('.txt')).read_bytes()).hexdigest()
            provenance[subset].append(entry)
    summary = dict(pilot=True, independenceConfirmed=False, archiveSha256=digest, duplicateImagesRemoved=duplicate_count,
                   sourceImages=len(manifest['images']), images=len(records), boxes=sum(counts.values()),
                   groups=len(groups), splitSeed=42+attempt, negatives=sum(not r['rows'] for r in records),
                   classes=[dict(trainingId=mapping[cid], annotationId=cid, **classes[cid], boxes=counts[cid],
                                 images=sum(any(row[0]==cid for row in r['rows']) for r in records),
                                 groups=len(coverage[cid]), splitBoxes={key:supports[key][cid] for key in split}) for cid in active],
                   absentClasses=[item['code'] for i,item in enumerate(classes) if i not in active],
                   splitImages={key:len(values) for key,values in split.items()},
                   limitations=['Only 54 supplied case images; some are synthetic/stock or have overlays.',
                                'Grouping is provisional: physical equipment identities were not supplied.',
                                'No normal negative images were annotated; false-alarm rate on healthy equipment cannot be estimated.',
                                'Original user labels preserved, including flagged ambiguous cases; expert review remains necessary.',
                                'Sparse or missing test classes have no reliable performance estimate.'])
    config=dict(path=str(output), train='images/train', val='images/val', test='images/test', names={mapping[cid]:classes[cid]['code'] for cid in active})
    (output/'data.yaml').write_text(yaml.safe_dump(config,sort_keys=False,allow_unicode=True))
    for name,value in [('summary.json',summary),('provenance.json',provenance),('grouping-proposal.json',policy)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    shutil.copy2(args.archive,output/'original-annotations.zip')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print('Dataset:',output/'data.yaml')


if __name__ == '__main__':
    main()
