#!/usr/bin/env python3
"""Collect real, attributed defect photographs for an explicitly reviewed batch.

Source categories are review hints, never confirmed labels. Downloads are cached,
photographs are not generated or augmented, and original bytes are preserved.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import struct
import urllib.parse
import urllib.request
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'ExternalData/defects-200-v1'
INDEX = Path('/tmp/kst-new-photo-sources')
AGENT = 'KST-Energo defect photo review/1.0 (educational local prototype)'
ZIP_URL = 'https://data.vicos.si/datasets/KSDD/KolektorSDD2.zip'


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def get(url, limit=20_000_000, headers=None):
    request = urllib.request.Request(url, headers={'User-Agent': AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        if headers and 'Range' in headers:
            if response.status != 206:
                raise ValueError('Source ignored the requested byte range')
            expected = headers['Range'].split('=')[1].split('-')[0]
            if not (response.headers.get('Content-Range') or '').startswith('bytes ' + expected + '-'):
                raise ValueError('Source returned a different byte range')
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Download exceeds size limit')
    return raw


def zip_entry(info, url=ZIP_URL):
    start = info['offset']
    end = start + info['compressed'] + len(info['name'].encode()) + 4096
    raw = get(url, limit=info['compressed'] + 8192,
              headers={'Range': f'bytes={start}-{end}'})
    if raw[:4] != b'PK\x03\x04':
        raise ValueError('Invalid ZIP entry header')
    name_size, extra_size = struct.unpack_from('<2H', raw, 26)
    payload_start = 30 + name_size + extra_size
    payload = raw[payload_start:payload_start + info['compressed']]
    if len(payload) != info['compressed']:
        raise ValueError('Truncated ZIP entry')
    unpacked = zlib.decompress(payload, -15) if info['method'] == 8 else payload
    if len(unpacked) != info['size'] or zlib.crc32(unpacked) != info['crc32']:
        raise ValueError('ZIP integrity check failed')
    return unpacked


def image_stats(raw):
    with Image.open(io.BytesIO(raw)) as original:
        if original.width * original.height > 25_000_000:
            raise ValueError('Image dimensions exceed limit')
        pixels = ImageOps.exif_transpose(original).convert('RGB')
        gray = np.asarray(pixels.convert('L').resize((9, 8), Image.Resampling.LANCZOS))
        bits = gray[:, 1:] > gray[:, :-1]
        dhash = int(''.join('1' if value else '0' for value in bits.flat), 2)
        return dict(width=pixels.width, height=pixels.height,
                    pixelSha256=hashlib.sha256(str(pixels.size).encode() + pixels.tobytes()).hexdigest(),
                    dhash=f'{dhash:016x}')


def download(item):
    key = hashlib.sha256((item['source'] + '/' + item['sourcePath']).encode()).hexdigest()[:18]
    ext = Path(item['sourcePath']).suffix.lower()
    destination = OUT / 'candidates' / (key + ext)
    record_path = OUT / 'records' / (key + '.json')
    if record_path.exists() and destination.exists():
        return json.loads(record_path.read_text())
    try:
        raw = zip_entry(item['zipEntry']) if 'zipEntry' in item else get(item['downloadUrl'])
        stats = image_stats(raw)
        if min(stats['width'], stats['height']) < 220 or max(stats['width'], stats['height']) < 380:
            return dict(sourcePath=item['sourcePath'], rejected='Image too small for clear manual review')
        destination.write_bytes(raw)
        record = {k: v for k, v in item.items() if k != 'zipEntry'}
        record.update(id=key, file=destination.relative_to(OUT).as_posix(), **stats,
                      sha256=hashlib.sha256(raw).hexdigest(), status='unreviewed')
        atomic_json(record_path, record)
        return record
    except Exception as error:
        return dict(sourcePath=item['sourcePath'], error=str(error))


def candidates():
    items = []
    tree = json.loads((INDEX / 'magnetic-tile-tree.json').read_text())['tree']
    revision = (INDEX / 'magnetic-tile-revision.txt').read_text().strip()
    for category, limit, hint in [('MT_Crack', 57, 'crack'), ('MT_Break', 45, 'mechanical_damage'),
                                  ('MT_Fray', 32, 'wear'), ('MT_Free', 30, 'normal-candidate')]:
        paths = sorted(x['path'] for x in tree if x['path'].startswith(category + '/') and x['path'].endswith('.jpg'))
        if category == 'MT_Free':
            paths = paths[::max(1, len(paths) // limit)][:limit]
        for path in paths[:limit]:
            base = 'https://raw.githubusercontent.com/abin24/Magnetic-tile-defect-datasets./' + revision + '/'
            items.append(dict(source='magnetic-tile', sourcePath=path, sourceRevision=revision,
                              sourcePage='https://github.com/abin24/Magnetic-tile-defect-datasets.',
                              downloadUrl=base + urllib.parse.quote(path), sourceMaskUrl=base + urllib.parse.quote(str(Path(path).with_suffix('.png'))),
                              license='Upstream repository terms; see downloaded source README',
                              author='Yibin Huang et al.', hint=hint, sourceGroup='magnetic-tile-' + category))

    metadata = json.loads((INDEX / 'khoaliamle-Corrosion_Rust.json').read_text())
    paths = sorted(x['rfilename'] for x in metadata['siblings']
                   if '/rust/' in x['rfilename'] and Path(x['rfilename']).suffix.lower() in {'.jpg', '.png'})
    grouped = {}
    for path in paths:
        stem = Path(path).stem
        # Repository contains augmented variants of the same original image.
        # Keep one per original filename prefix, then still review visual similarity.
        group = re.sub(r'_[a-z0-9]{6,}\.[a-z0-9]+$', '', stem, flags=re.I)
        group = re.sub(r'_[0-9]+$', '', group) if re.match(r'^(DSC|RIMG|IMG|WA|FL|WP)', group) else group
        grouped.setdefault(group, path)
    # Prefer camera originals over small extracted texture patches.
    ordered = sorted(grouped.items(), key=lambda row: (not bool(re.match(r'^(DSC|RIMG|IMG|WA|FL|WP)', row[0])), row[0]))
    for group, path in ordered[:150]:
        items.append(dict(source='corrosion-rust', sourcePath=path, sourceRevision=metadata['sha'],
                          sourcePage='https://huggingface.co/datasets/khoaliamle/Corrosion_Rust',
                          downloadUrl='https://huggingface.co/datasets/khoaliamle/Corrosion_Rust/resolve/' + metadata['sha'] + '/' + urllib.parse.quote(path),
                          license='MIT declared by dataset uploader; underlying photograph provenance not independently established',
                          author='khoaliamle (dataset uploader)', hint='corrosion', sourceGroup='corrosion-rust-' + group))

    index = json.loads((INDEX / 'ksdd2-index.json').read_text())
    by_name = {entry['name']: entry for entry in index}
    masks = sorted((entry for entry in index if entry['name'].startswith('train/') and entry['name'].endswith('_GT.png') and entry['size'] > 350),
                   key=lambda entry: entry['compressed'], reverse=True)[:120]
    def inspect_mask(entry):
        try:
            cached = OUT / 'source-masks' / ('ksdd2-' + Path(entry['name']).name)
            raw = cached.read_bytes() if cached.exists() else zip_entry(entry)
            if not cached.exists():
                cached.write_bytes(raw)
            mask = np.asarray(Image.open(io.BytesIO(raw)).convert('L')) > 0
            ys, xs = np.where(mask)
            if not len(xs):
                return None
            width, height = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
            # Long, clearly visible surface anomalies get reviewed first.
            if max(width, height) < 65 or len(xs) < 200:
                return None
            image_name = entry['name'].replace('_GT.png', '.png')
            return dict(source='kolektorsdd2', sourcePath=image_name, sourceRevision='original-zip-2026-11-14',
                        sourcePage='https://www.vicos.si/resources/kolektorsdd2/', downloadUrl=ZIP_URL,
                        license='CC BY-NC-SA 4.0', licenseUrl='https://creativecommons.org/licenses/by-nc-sa/4.0/',
                        author='ViCoS / Kolektor', hint='surface-anomaly-review', sourceGroup='kolektorsdd2-training-surface-series',
                        zipEntry=by_name[image_name], sourceMask=cached.relative_to(OUT).as_posix(),
                        sourceMaskBounds=dict(x=int(xs.min()), y=int(ys.min()), w=width, h=height),
                        maskPixels=int(len(xs)), elongation=max(width, height) / max(1, min(width, height)))
        except Exception as error:
            print('Mask download:', entry['name'], str(error), flush=True)
            return None
    with ThreadPoolExecutor(max_workers=5) as pool:
        anomalies = [item for item in pool.map(inspect_mask, masks) if item is not None]
    anomalies.sort(key=lambda item: (-item['elongation'], -item['maskPixels']))
    items.extend(anomalies[:75])
    return items


def contact_sheets(images, prefix='contact', size=30):
    font = ImageFont.load_default(size=13)
    for start in range(0, len(images), size):
        sheet = Image.new('RGB', (1500, 1320), 'white')
        draw = ImageDraw.Draw(sheet)
        for offset, item in enumerate(images[start:start + size]):
            x, y = (offset % 5) * 300, (offset // 5) * 220
            with Image.open(OUT / item['file']) as original:
                thumb = ImageOps.contain(ImageOps.exif_transpose(original).convert('RGB'), (292, 181))
                sheet.paste(thumb, (x + (292 - thumb.width) // 2, y + (181 - thumb.height) // 2))
            draw.text((x + 4, y + 184), f"{start + offset + 1:03d} {item['id']} {item.get('hint', '')}"[:40], font=font, fill='black')
            draw.text((x + 4, y + 202), Path(item['sourcePath']).name[:40], font=font, fill='black')
        sheet.save(OUT / f'{prefix}-{start // size + 1:02d}.jpg', quality=93)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--contacts-only', action='store_true')
    args = parser.parse_args()
    for sub in ['candidates', 'records', 'source-masks', 'source-docs']:
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    if args.contacts_only:
        images = json.loads((OUT / 'candidate-manifest.json').read_text())['images']
    else:
        for path in INDEX.glob('*README.md'):
            shutil.copy2(path, OUT / 'source-docs' / path.name)
        revision = (INDEX / 'magnetic-tile-revision.txt').read_text().strip()
        try:
            readme = get('https://raw.githubusercontent.com/abin24/Magnetic-tile-defect-datasets./' + revision + '/README.md')
            (OUT / 'source-docs/magnetic-tile-README.md').write_bytes(readme)
        except Exception as error:
            print('Source README:', str(error), flush=True)
        listing = candidates()
        atomic_json(OUT / 'download-list.json', listing)
        print('Candidate download list:', len(listing), flush=True)
        images, issues, seen = [], [], set()
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(download, item) for item in listing]
            for future in as_completed(futures):
                item = future.result()
                if 'file' not in item:
                    issues.append(item)
                    continue
                if item['pixelSha256'] in seen:
                    issues.append(dict(sourcePath=item['sourcePath'], rejected='Exact pixel duplicate'))
                    continue
                seen.add(item['pixelSha256'])
                images.append(item)
                if len(images) % 25 == 0:
                    print('Downloaded and checked:', len(images), flush=True)
        images.sort(key=lambda item: (item['source'], item.get('hint', ''), item['sourcePath']))
        for number, item in enumerate(images, 1):
            item['reviewIndex'] = number
        atomic_json(OUT / 'candidate-manifest.json', dict(createdAt=datetime.now(timezone.utc).isoformat(),
                    purpose='Manual selection and annotation of 200 new real photographs', labelsVerified=False,
                    generatedImages=0, augmentedVariantsIntentionallyAdded=0, images=images, issues=issues))
    contact_sheets(images)
    from collections import Counter
    print(json.dumps(dict(directory=str(OUT), candidates=len(images), hints=Counter(item.get('hint') for item in images)), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
