#!/usr/bin/env python3
"""Freeze Roboflow exports and confirmed user boxes for separate defect detectors."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import zipfile

import numpy as np
from PIL import Image
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ('train', 'val', 'test')
SPECS = {
    'oil': dict(project='oil-leak-detection-axcq4', workspace='chiranjeevi-p2jmx',
                mapping={'oil_leak': 'leak'}, classes=['leak'], regroup=True),
    'corrosion': dict(project='corrosion-jmvmm', workspace='yilmaz',
                      mapping={'corrosion': 'corrosion'}, classes=['corrosion'], regroup=True),
    'damage': dict(project='wind-turbine-object-detection', workspace='drone-s4cii',
                   mapping={'crack': 'crack', 'damage': 'mechanical_damage', 'erosion': 'wear'},
                   classes=['crack', 'mechanical_damage', 'wear'], regroup=False),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def family(name):
    name = PurePosixPath(name).stem.split('.rf.')[0].lower()
    return re.sub(r'(?:_jpg|_jpeg|_png|_webp)+$', '', name)


def image_identity(data):
    with Image.open(io.BytesIO(data)) as original:
        image = original.convert('RGB')
        pixel_sha = sha(str(image.size).encode() + image.tobytes())
        gray = np.asarray(image.resize((9, 8)).convert('L'), dtype=np.int16)
        bits = (gray[:, 1:] > gray[:, :-1]).flatten()
        dhash = sum(int(value) << i for i, value in enumerate(bits))
        thumbnail = np.asarray(image.resize((32, 32)), dtype=np.int16)
    return pixel_sha, dhash, thumbnail


def parse_labels(raw, source_classes, mapping, output_classes):
    boxes = []
    polygons = 0
    for line in raw.decode('utf-8').splitlines():
        if not line.strip():
            continue
        values = [float(value) for value in line.split()]
        class_id = int(values[0])
        if values[0] != class_id or not 0 <= class_id < len(source_classes):
            raise ValueError('Invalid source annotation class')
        coords = values[1:]
        if not all(math.isfinite(value) and -1e-6 <= value <= 1 + 1e-6 for value in coords):
            raise ValueError('Invalid normalized annotation coordinates')
        if len(coords) == 4:
            x, y, width, height = coords
            if width <= 0 or height <= 0 or x-width/2 < -1e-6 or y-height/2 < -1e-6 or x+width/2 > 1+1e-6 or y+height/2 > 1+1e-6:
                raise ValueError('Invalid source bounding box')
        elif len(coords) >= 6 and len(coords) % 2 == 0:
            xs, ys = coords[::2], coords[1::2]
            left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
            width, height = right-left, bottom-top
            x, y = (left+right)/2, (top+bottom)/2
            if width <= 0 or height <= 0:
                raise ValueError('Degenerate source polygon')
            polygons += 1
        else:
            raise ValueError('Source annotations mix unsupported tasks')
        code = mapping[source_classes[class_id]]
        boxes.append((output_classes.index(code), x, y, width, height))
    return encode_boxes(boxes), polygons


def encode_boxes(boxes):
    return ''.join(str(cid) + ' ' + ' '.join(f'{value:.8f}' for value in values) + '\n'
                   for cid, *values in boxes).encode()


class Union:
    def __init__(self, keys):
        self.parent = {key: key for key in keys}

    def find(self, key):
        parent = self.parent[key]
        if parent != key:
            self.parent[key] = self.find(parent)
        return self.parent[key]

    def join(self, left, right):
        left, right = self.find(left), self.find(right)
        if left != right:
            self.parent[max(left, right)] = min(left, right)


def load_user(base):
    summary = json.loads((base/'summary.json').read_text())
    provenance = json.loads((base/'provenance.json').read_text())
    rows = []
    for split in SPLITS:
        for record in provenance[split]:
            image_path = base/'images'/split/record['file']
            label_path = base/'labels'/split/Path(record['file']).with_suffix('.txt')
            image, label = image_path.read_bytes(), label_path.read_bytes()
            if sha(image) != record['sha256'] or sha(label) != record['labelsSha256']:
                raise ValueError('Frozen user image or annotation changed')
            pixel_sha, dhash, thumbnail = image_identity(image)
            rows.append(dict(image=image, label=label, record=record, split=split,
                             pixelSha256=pixel_sha, dhash=dhash, thumbnail=thumbnail))
    return summary, rows


def import_archive(path, spec, snapshot_dir):
    archive_bytes = path.read_bytes()
    digest = sha(archive_bytes)
    rows, removed = [], []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        members = set(archive.namelist())
        if len(members) != len(archive.namelist()) or archive.testzip():
            raise ValueError('Corrupt or ambiguous Roboflow ZIP')
        for name in members:
            entry = PurePosixPath(name)
            if entry.is_absolute() or '..' in entry.parts:
                raise ValueError('Unsafe archive path')
        config = yaml.safe_load(archive.read('data.yaml'))
        source = config['roboflow']
        if source['project'] != spec['project'] or source['workspace'] != spec['workspace']:
            raise ValueError('Archive belongs to a different requested project')
        names = config['names']
        source_classes = names if isinstance(names, list) else [names[i] for i in range(len(names))]
        if set(source_classes) != set(spec['mapping']) or config['nc'] != len(source_classes):
            raise ValueError('Source classes need an explicit mapping')
        for name in sorted(members):
            entry = PurePosixPath(name)
            if len(entry.parts) != 3 or entry.parts[1] != 'images' or entry.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
                continue
            split = {'train': 'train', 'valid': 'val', 'val': 'val', 'test': 'test'}.get(entry.parts[0])
            if not split:
                raise ValueError('Unknown source split')
            label_name = str(entry.parent.parent/'labels'/entry.with_suffix('.txt').name)
            if label_name not in members:
                raise ValueError('Missing source label: ' + label_name)
            image, original_label = archive.read(name), archive.read(label_name)
            try:
                label, polygons = parse_labels(original_label, source_classes, spec['mapping'], spec['classes'])
                pixel_sha, dhash, thumbnail = image_identity(image)
            except (ValueError, OSError) as error:
                removed.append(dict(sourceImage=name, reason=str(error)))
                continue
            record = dict(file=spec['project']+'-'+digest[:8]+'-'+entry.name,
                          group=spec['project']+'/'+family(name), source='roboflow', sourceImage=name,
                          sourceLabels=label_name, sourceSplit=split, sourceArchiveSha256=digest,
                          sourceLabelsSha256=sha(original_label), sourcePolygonCount=polygons,
                          sourceProject=source['project'], sourceVersion=source['version'],
                          sourceUrl=source['url'], license=source['license'],
                          sha256=sha(image), labelsSha256=sha(label), pixelSha256=pixel_sha)
            rows.append(dict(image=image, label=label, record=record, split=split,
                             dhash=dhash, thumbnail=thumbnail))
        if not rows:
            raise ValueError('Archive contains no usable labeled photos')
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_dir/(spec['project']+'-v'+str(source['version'])+'-'+digest[:12]+'.zip')
    if snapshot.exists() and sha(snapshot.read_bytes()) != digest:
        raise ValueError('Existing source snapshot changed')
    if not snapshot.exists():
        snapshot.write_bytes(archive_bytes)
    source_info = dict(**source, archive=str(snapshot), archiveSha256=digest,
                       originalImages=len(rows)+len(removed), originalSplitImages=dict(Counter(row['split'] for row in rows)),
                       sourceClasses=source_classes, mapping=spec['mapping'], removed=removed)
    return rows, source_info


def isolate_public(rows, user_rows, regroup):
    # A Roboflow family covers re-exports and augmentations of a source filename.
    union = Union({row['record']['group'] for row in rows})
    by_pixel = defaultdict(list)
    for row in rows:
        by_pixel[row['record']['pixelSha256']].append(row)
    excluded, duplicates = set(), []
    for matches in by_pixel.values():
        for row in matches[1:]:
            union.join(matches[0]['record']['group'], row['record']['group'])
        if len(matches) > 1:
            conflict = len({row['label'] for row in matches}) > 1
            discarded = matches if conflict else matches[1:]
            for row in discarded:
                excluded.add(row['record']['file'])
                duplicates.append(dict(sourceImage=row['record']['sourceImage'],
                    reason='identical_pixels_conflicting_boxes' if conflict else 'identical_pixels'))
    for row in rows:
        row['record']['sourceFamily'] = row['record']['group']
        row['record']['group'] = union.find(row['record']['group'])
    groups = defaultdict(list)
    for row in rows:
        if row['record']['file'] not in excluded:
            groups[row['record']['group']].append(row)
    links, conflicts = defaultdict(list), []
    for group, members in groups.items():
        for row in members:
            for own in user_rows:
                exact = row['record']['pixelSha256'] == own['pixelSha256']
                near = not exact and (row['dhash'] ^ own['dhash']).bit_count() <= 2 and float(np.abs(row['thumbnail']-own['thumbnail']).mean()) < 7
                if exact or near:
                    links[group].append(dict(split=own['split'], userFile=own['record']['file'],
                        publicFile=row['record']['file'], kind='exact' if exact else 'same_frame_reexport'))
                    if exact:
                        excluded.add(row['record']['file'])
        linked_splits = {link['split'] for link in links[group]}
        if len(linked_splits) > 1:
            excluded.update(row['record']['file'] for row in members)
            conflicts.append(dict(group=group, reason='source_family_links_multiple_existing_user_splits'))
    keys = sorted(groups)
    random.Random(20261114).shuffle(keys)
    # All variants of a source remain together when rebuilding a leaking/missing split.
    n_val, n_test = max(1, round(len(keys)*.1)), max(1, round(len(keys)*.1))
    generated = {key: 'val' if i < n_val else 'test' if i < n_val+n_test else 'train'
                 for i, key in enumerate(keys)}
    moved = 0
    for group, members in groups.items():
        source_splits = {row['split'] for row in members}
        linked_splits = {link['split'] for link in links[group]}
        if len(linked_splits) == 1:
            assigned = next(iter(linked_splits))
        elif regroup or len(source_splits) > 1:
            assigned = generated[group]
        else:
            assigned = next(iter(source_splits))
        for row in members:
            if row['split'] != assigned:
                moved += 1
            row['split'] = assigned
    retained = [row for row in rows if row['record']['file'] not in excluded]
    audit = dict(duplicateExclusions=duplicates, sourceFamilyConflicts=conflicts,
                 userReexportLinks={group: entries for group, entries in links.items() if entries},
                 reassignedImages=moved,
                 splitPolicy='seeded_source_family_80_10_10' if regroup else 'retain_native_splits_except_linked_families')
    return retained, audit


def freeze_dataset(kind, path, base, base_summary, user_rows, output_root, snapshot_dir):
    spec = SPECS[kind]
    out = output_root/('roboflow-'+kind+'-user-20261114-v1')
    if out.exists():
        raise ValueError('Frozen output already exists: ' + str(out))
    public, source_info = import_archive(path, spec, snapshot_dir)
    public, audit = isolate_public(public, user_rows, spec['regroup'])
    rows = list(public)
    user_codes = [entry['code'] for entry in base_summary['classes']]
    user_box_counts = Counter()
    for own in user_rows:
        boxes = []
        for line in own['label'].decode().splitlines():
            if not line.strip():
                continue
            source_id, *coords = [float(value) for value in line.split()]
            code = user_codes[int(source_id)]
            if code in spec['classes']:
                boxes.append((spec['classes'].index(code), *coords))
                user_box_counts[code] += 1
        label = encode_boxes(boxes)
        record = dict(own['record'], source='confirmed_user', sourceDataset=base.name,
                      sourceLabelsSha256=sha(own['label']), labelsSha256=sha(label),
                      pixelSha256=own['pixelSha256'])
        rows.append(dict(image=own['image'], label=label, record=record, split=own['split']))
    provenance = {split: [] for split in SPLITS}
    used_pixels, used_files, group_split = set(), set(), {}
    for row in rows:
        record, split = row['record'], row['split']
        if record['file'] in used_files or record['pixelSha256'] in used_pixels:
            raise ValueError('Duplicate image survived import isolation')
        if record['group'] in group_split and group_split[record['group']] != split:
            raise ValueError('Source family crosses frozen splits')
        used_files.add(record['file']); used_pixels.add(record['pixelSha256']); group_split[record['group']] = split
        provenance[split].append(record)
    if any(not provenance[split] for split in SPLITS):
        raise ValueError('Empty training or evaluation split')
    counts = {split: Counter() for split in SPLITS}
    source_counts = {split: Counter() for split in SPLITS}
    negatives = 0
    for row in rows:
        ids = [int(line.split()[0]) for line in row['label'].decode().splitlines()]
        counts[row['split']].update(ids)
        source_counts[row['split']][row['record']['source']] += 1
        negatives += not ids
    limitations = [
        'Specialists use only their supported classes. Other defect labels remain in the original nine-class user dataset and model.',
        'All 207 confirmed user training photos are retained; the original 5 validation and 12 test photos keep their split and image bytes.',
        'Uncertain and unreviewed annotations are preserved separately and are not converted to negatives.',
        'Public source filename families, identical decoded pixels and detected user re-exports do not cross training/evaluation splits.',
        'Source filename groups do not establish independent physical equipment. Reports are pilot evaluations.',
        'Source polygons are projected to their enclosing boxes for detection; original masks are preserved in the source ZIP. User boxes never become invented masks.',
        'Old user holdouts are small and previously inspected; pump/metal performance must be read separately from public-domain performance.',
    ]
    if kind == 'damage':
        limitations.append('Blade damage maps to mechanical_damage and erosion to wear. This is not a dedicated deformation, shaft alignment or pump fault classifier.')
    manifest_sha = sha(json.dumps(dict(source=source_info['archiveSha256'], user=base_summary['archiveSha256'],
                                      audit=audit, mapping=spec['mapping']), sort_keys=True).encode())
    summary = dict(createdAt=datetime.now(timezone.utc).isoformat(), task='detect', pilot=True,
                   independenceConfirmed=False, archiveSha256=manifest_sha, images=len(rows),
                   boxes=sum(sum(value.values()) for value in counts.values()), negatives=negatives,
                   splitImages={split: len(provenance[split]) for split in SPLITS},
                   splitSourceImages={split: dict(source_counts[split]) for split in SPLITS},
                   classes=[dict(trainingId=cid, code=code,
                                 splitBoxes={split: counts[split][cid] for split in SPLITS})
                            for cid, code in enumerate(spec['classes'])],
                   userDataset=str(base), userArchiveSha256=base_summary['archiveSha256'],
                   userProjectedBoxes=dict(user_box_counts), source=source_info, audit=audit,
                   limitations=limitations)
    # All source validation finishes before publishing a versioned training directory.
    staging = out.with_name(out.name+'.preparing')
    if staging.exists():
        raise ValueError('Incomplete preparation directory already exists: '+str(staging))
    for split in SPLITS:
        (staging/'images'/split).mkdir(parents=True)
        (staging/'labels'/split).mkdir(parents=True)
    for row in rows:
        split, filename = row['split'], row['record']['file']
        (staging/'images'/split/filename).write_bytes(row['image'])
        (staging/'labels'/split/Path(filename).with_suffix('.txt')).write_bytes(row['label'])
    config = dict(path=str(out), train='images/train', val='images/val', test='images/test',
                  nc=len(spec['classes']), names=spec['classes'])
    (staging/'data.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
    write_json(staging/'summary.json', summary)
    write_json(staging/'provenance.json', provenance)
    (staging/'original-user-summary.json').write_bytes((base/'summary.json').read_bytes())
    staging.rename(out)
    return dict(kind=kind, data=str(out/'data.yaml'), images=len(rows), splitImages=summary['splitImages'],
                userProjectedBoxes=dict(user_box_counts), publicImages=len(public),
                exclusions=len(source_info['removed'])+len(audit['duplicateExclusions']),
                regroupedImages=audit['reassignedImages'], source=source_info['url'])


def main():
    parser = argparse.ArgumentParser()
    for kind in SPECS:
        parser.add_argument('--'+kind, type=Path, required=True)
    parser.add_argument('--user-data', type=Path, default=ROOT/'ComputerVision/datasets/pump-reviewed-200-20261114-v1')
    parser.add_argument('--output-root', type=Path, default=ROOT/'ComputerVision/datasets')
    parser.add_argument('--snapshot-dir', type=Path, default=ROOT/'ExternalData/roboflow-imports-20261114')
    args = parser.parse_args()
    base = args.user_data.resolve()
    base_summary, user_rows = load_user(base)
    outputs = []
    for kind in SPECS:
        result = freeze_dataset(kind, getattr(args, kind).resolve(), base, base_summary, user_rows,
                                args.output_root.resolve(), args.snapshot_dir.resolve())
        outputs.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    write_json(ROOT/'.integration/roboflow-datasets.json', dict(datasets=outputs, userData=str(base)))


if __name__ == '__main__':
    main()
