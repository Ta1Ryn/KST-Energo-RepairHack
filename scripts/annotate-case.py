#!/usr/bin/env python3
"""Local case-photo labelling with explicit review, atomic saves and YOLO export."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
import os
import re
from pathlib import Path
import secrets
import threading
from urllib.parse import urlparse
import zipfile

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / 'ComputerVision/annotation'
SOURCE = ROOT / 'CaseFiles'
CLASSES = json.loads((UI / 'classes.json').read_text())
LOCK = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


class Dataset:
    def __init__(self, directory, source=SOURCE, title='Разметка фотографий кейса'):
        if not source.is_dir():
            raise ValueError('Photo source directory not found: ' + str(source))
        self.title = title
        self.export_prefix = re.sub(r'[^A-Za-z0-9_-]', '-', directory.name)[:80] or 'annotations'
        self.directory = directory
        (directory / 'images').mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.images = []
        seen = {}
        for path in sorted(source.rglob('*')):
            if not path.is_file() or path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}:
                continue
            relative = path.relative_to(source).as_posix()
            raw_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            image_id = hashlib.sha256((relative + raw_hash).encode()).hexdigest()[:24]
            destination = directory / 'images' / (image_id + '.jpg')
            with Image.open(path) as source_image:
                pixels = ImageOps.exif_transpose(source_image).convert('RGB')
                width, height = pixels.size
                if min(width, height) < 64 or width * height > 25_000_000:
                    raise ValueError('Unsupported image dimensions: ' + relative)
                pixel_hash = hashlib.sha256(str(pixels.size).encode() + pixels.tobytes()).hexdigest()
                if not destination.exists():
                    pixels.save(destination, 'JPEG', quality=95)
            item = dict(id=image_id, name=path.name, sourcePath=relative, category=path.parent.name,
                        width=width, height=height, originalSha256=raw_hash, pixelSha256=pixel_hash,
                        duplicateOf=seen.get(pixel_hash), imageUrl='/image/' + image_id)
            seen.setdefault(pixel_hash, image_id)
            self.images.append(item)
        self.by_id = {item['id']: item for item in self.images}
        self.path = directory / 'annotations.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else dict(version=1, classes=CLASSES, images={})
        if self.state['classes'] != CLASSES:
            raise ValueError('Saved class definitions differ. Keep the existing dataset and use another data directory.')
        atomic_json(directory / 'catalog.json', dict(version=1, source=source.name, classes=CLASSES, images=self.images))

    def annotation(self, image_id):
        return copy.deepcopy(self.state['images'].get(image_id, dict(boxes=[], status='unreviewed', group='', note='', revision=0)))

    def save(self, image_id, value):
        if image_id not in self.by_id:
            raise KeyError(image_id)
        boxes = value.get('boxes')
        status = value.get('status')
        if status not in {'unreviewed', 'labeled', 'negative', 'uncertain'} or not isinstance(boxes, list) or len(boxes) > 200:
            raise ValueError('Некорректный статус или список рамок.')
        for box in boxes:
            if not isinstance(box, dict) or type(box.get('classId')) is not int or not 0 <= box['classId'] < len(CLASSES):
                raise ValueError('Неизвестный класс.')
            coordinates = [box.get(key) for key in ('x', 'y', 'w', 'h')]
            if any(type(number) not in (int, float) or not math.isfinite(number) for number in coordinates):
                raise ValueError('Некорректные координаты.')
            x, y, width, height = coordinates
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
                raise ValueError('Рамка выходит за границы фотографии.')
        if status == 'negative' and boxes or status == 'labeled' and not boxes:
            raise ValueError('Подтверждённая разметка не соответствует рамкам.')
        group, note = value.get('group', ''), value.get('note', '')
        if not isinstance(group, str) or not isinstance(note, str) or len(group) > 120 or len(note) > 2000:
            raise ValueError('Слишком длинная группа или заметка.')
        with LOCK:
            old = self.annotation(image_id)
            if type(value.get('revision')) is not int or value['revision'] != old['revision']:
                return None
            entry = dict(boxes=boxes, status=status, group=group.strip(), note=note.strip(),
                         revision=old['revision'] + 1, updatedAt=now())
            new_state = copy.deepcopy(self.state)
            new_state['images'][image_id] = entry
            atomic_json(self.path, new_state)
            self.state = new_state
            return copy.deepcopy(entry)

    def export(self):
        with LOCK:
            state = copy.deepcopy(self.state)
        accepted = [(item, state['images'].get(item['id'])) for item in self.images]
        accepted = [(item, annotation) for item, annotation in accepted
                    if annotation and annotation['status'] in {'labeled', 'negative'}]
        if not accepted:
            raise ValueError('Сначала подтвердите хотя бы одну фотографию. Черновик можно скачать в любой момент.')
        output = io.BytesIO()
        manifest = dict(version=1, classes=[c['code'] for c in CLASSES], images=[], excludedUnreviewed=True)
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for item, annotation in accepted:
                name = item['id'] + '.jpg'
                archive.write(self.directory / 'images' / name, 'images/' + name)
                rows = [f"{b['classId']} {b['x'] + b['w']/2:.8f} {b['y'] + b['h']/2:.8f} {b['w']:.8f} {b['h']:.8f}" for b in annotation['boxes']]
                archive.writestr('labels/' + item['id'] + '.txt', '\n'.join(rows) + ('\n' if rows else ''))
                manifest['images'].append(dict(file=name, sourcePath=item['sourcePath'], group=annotation['group'],
                                              reviewed=True, negative=annotation['status']=='negative',
                                              pixelSha256=item['pixelSha256'], duplicateOf=item['duplicateOf'],
                                              originalSha256=item['originalSha256']))
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr('classes.json', json.dumps(CLASSES, ensure_ascii=False, indent=2))
            archive.writestr('annotations.json', json.dumps(state, ensure_ascii=False, indent=2))
            archive.writestr('README.txt', 'YOLO annotations; images have normalized EXIF orientation.\n'
                            'Only labeled/negative images are included. Uncertain/unreviewed images are excluded.\n'
                            'This is an UNSPLIT annotation batch, not a validated training dataset.\n'
                            'Review capture groups before creating train/val/test; keep entire equipment units/series together.\n'
                            'Remove duplicate images using pixelSha256 and review near-duplicates.\n'
                            'Empty group means UNKNOWN, not an independent photo.\n'
                            'Do not merge class IDs with the old AiYolo four-class dataset.\n')
        return output.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, status, data, mime='application/json', filename=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if filename:
            self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
        self.end_headers()
        self.wfile.write(data)

    def json(self, status, value):
        self.send(status, json.dumps(value, ensure_ascii=False).encode())

    def valid_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.valid_host():
            self.json(403, {'error': 'Local access only'})
            return
        route = urlparse(self.path).path
        dataset = self.server.dataset
        if route in ('/', '/annotate'):
            self.send(200, (UI / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        elif route == '/health':
            self.json(200, dict(service='kst-case-annotation', photos=len(dataset.images)))
        elif route == '/api/catalog':
            with LOCK:
                data = dict(images=dataset.images, classes=CLASSES, token=dataset.token, title=dataset.title, exportPrefix=dataset.export_prefix,
                            annotations=copy.deepcopy(dataset.state['images']), savePath=str(dataset.path))
            self.json(200, data)
        elif route.startswith('/image/') and route[7:] in dataset.by_id:
            self.send(200, (dataset.directory / 'images' / (route[7:] + '.jpg')).read_bytes(), 'image/jpeg')
        elif route == '/api/draft':
            with LOCK:
                data = json.dumps(dataset.state, ensure_ascii=False, indent=2).encode()
            self.send(200, data, filename=dataset.export_prefix+'-draft.json')
        elif route == '/api/export':
            try:
                self.send(200, dataset.export(), 'application/zip', dataset.export_prefix+'-annotations-yolo.zip')
            except ValueError as error:
                self.json(409, dict(error=str(error)))
        else:
            self.json(404, dict(error='Not found'))

    def do_PUT(self):
        dataset = self.server.dataset
        if not self.valid_host() or self.headers.get('X-Annotation-Token') != dataset.token:
            self.json(403, dict(error='Обновите страницу перед сохранением.'))
            return
        if self.headers.get('Origin') not in (None, f'http://{self.headers.get("Host")}'):
            self.json(403, dict(error='Origin mismatch'))
            return
        route = urlparse(self.path).path
        if not route.startswith('/api/annotations/') or route.removeprefix('/api/annotations/') not in dataset.by_id:
            self.json(404, dict(error='Unknown image'))
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 100_000:
                raise ValueError('Invalid request size')
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError('Expected an object')
            result = dataset.save(route.removeprefix('/api/annotations/'), value)
            if result is None:
                self.json(409, dict(error='Снимок изменён в другой вкладке. Скачайте черновик и обновите страницу.'))
            else:
                self.json(200, result)
        except (ValueError, TypeError) as error:
            self.json(400, dict(error=str(error)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=5100)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'ComputerVision/datasets/case-v1')
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--title', default='Разметка фотографий кейса')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.dataset = Dataset(args.data_dir.resolve(),args.source.resolve(),args.title)
    print(f'Ready: http://127.0.0.1:{args.port}/ — {len(server.dataset.images)} photos', flush=True)
    print('Annotations: ' + str(server.dataset.path), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
