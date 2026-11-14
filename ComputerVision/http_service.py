"""HTTP CV adapter and local comparison of pinned specialist models."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import logging
import math
import os
import secrets
from pathlib import Path
import threading
from uuid import UUID

import cv2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
import numpy as np
from PIL import Image, UnidentifiedImageError
import torch
from ultralytics import YOLO

from cv_camera import PROMPTS, DEFECT_INFO, infer_candidates
from model_engine import ModelEngine, NAMES
from case_data import records

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
Image.MAX_IMAGE_PIXELS = 25_000_000
LOG = logging.getLogger(__name__)


def make_response(candidates, width, height, version):
    detections = []
    for candidate in candidates[:200]:
        left = max(0, min(width, candidate['x1']))
        top = max(0, min(height, candidate['y1']))
        right = max(left, min(width, candidate['x2']))
        bottom = max(top, min(height, candidate['y2']))
        confidence = float(candidate['confidence'])
        if right <= left or bottom <= top or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('Invalid CV detection')
        # A generic liquid leak does not identify oil. Preserve the CV class;
        # manual inspection records the confirmed business defect separately.
        detections.append(dict(label=candidate['class'], confidence=confidence,
            box=dict(x=left/width, y=top/height, width=(right-left)/width, height=(bottom-top)/height)))
    classes = list(dict.fromkeys(d['label'] for d in detections))
    names = [DEFECT_INFO.get(key, (key, 0, ''))[0] for key in classes]
    return dict(modelVersion=version, visualStatus='possibleDefect' if detections else 'checkRecommended',
        summary=('Найдены признаки: ' + ', '.join(names) + '.') if names else 'На снимке не найдены подтверждённые алгоритмом признаки дефектов.',
        recommendation='Проверить отмеченные области и подтвердить результат осмотром и показаниями датчиков.' if detections else 'Подтвердить состояние осмотром; при необходимости сделать снимок ближе и при лучшем освещении.',
        confidence=max((d['confidence'] for d in detections), default=None), detections=detections)


@asynccontextmanager
async def lifespan(app):
    device = os.environ.get('FORK_CV_DEVICE', 'cpu')
    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    cv2.setNumThreads(2)
    weights = Path(os.environ.get('FORK_CV_WEIGHTS', str(ROOT / 'yolov8s-world.pt'))).resolve()
    if not weights.is_file():
        raise RuntimeError('CV weights are missing: ' + str(weights))
    model = YOLO(str(weights))
    if not callable(getattr(model, 'set_classes', None)):
        raise RuntimeError('The integrated CV service requires YOLO-World weights with text prompts.')
    model.set_classes([text for _, text in PROMPTS])
    app.state.model = model
    app.state.version = 'repairhack-cv-v1-' + hashlib.sha256(weights.read_bytes()).hexdigest()[:12]
    app.state.args = argparse.Namespace(device=device, tiles=1, confidence=0.01, image_size=640,
        strictness=1.0, no_equipment_filter=False, debug_equipment=False, raise_inference_errors=True)
    app.state.device = device
    app.state.engine = ModelEngine()
    app.state.review_token = secrets.token_urlsafe(32)
    app.state.gallery = {row['id']:row for row in records()}
    # Readiness means an actual model inference succeeded, including required assets.
    model.predict(np.full((128,128,3),128,dtype=np.uint8), imgsz=640, device=device, verbose=False)
    for model_id in app.state.engine.selection()['models']:
        if model_id != 'legacy':
            app.state.engine.predict(model_id,np.zeros((128,128,3),dtype=np.uint8),include_masks=False)
    yield


app = FastAPI(title='KST-Energo ComputerVision', lifespan=lifespan)


@app.get('/health')
def health():
    selection=app.state.engine.selection()
    legacy=selection['models']==['legacy']
    return dict(status='ok', modelVersion=app.state.version if legacy else app.state.engine.version(selection),
                device=app.state.device if legacy else 'cpu',activeModels=selection['models'],
                threshold=selection['threshold'],experimental=True,
                geometry='segmentation-when-available',liquidPolicy='mask-with-independent-confirmation',
                classes=list(DEFECT_INFO) if legacy else sorted({label for mid in selection['models'] for label in app.state.engine.entries[mid]['mapping'].values()}))


def decode_photo(content):
    if len(content) > 10*1024*1024:
        raise HTTPException(413, 'Фотография превышает 10 МиБ.')
    try:
        with Image.open(io.BytesIO(content)) as original:
            if original.format not in ('JPEG','PNG'):
                raise HTTPException(415, 'Используйте JPEG или PNG.')
            if min(original.size)<64 or original.width*original.height>Image.MAX_IMAGE_PIXELS:
                raise HTTPException(422, 'Размер фото: от 64×64 до 25 мегапикселей.')
            if original.getexif().get(274,1)!=1:
                raise HTTPException(422, 'Перед загрузкой примените EXIF-поворот к пикселям фотографии.')
            image = cv2.cvtColor(np.asarray(original.convert('RGB')),cv2.COLOR_RGB2BGR)
            # Keep CPU memory and verification work bounded. Normalized boxes
            # still refer to the full original image, using the same aspect ratio.
            if max(image.shape[:2]) > 1280:
                scale = 1280 / max(image.shape[:2])
                image = cv2.resize(image,(round(image.shape[1]*scale),round(image.shape[0]*scale)),interpolation=cv2.INTER_AREA)
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as error:
        raise HTTPException(415, 'Не удалось прочитать фотографию.') from error
    return image


@app.post('/analyze')
def analyze(file: UploadFile=File(...), equipmentId: UUID=Form(...),
            componentId: UUID=Form(...), installationId: UUID=Form(...)):
    image=decode_photo(file.file.read(10*1024*1024+1))
    if not LOCK.acquire(blocking=False):
        raise HTTPException(503, 'Сервис анализирует другое фото. Повторите запрос.')
    try:
        if app.state.engine.selection()['models'] != ['legacy']:
            return app.state.engine.analyze(image)
        candidates = infer_candidates(app.state.model,image,None,app.state.args,True,app.state.model.names,None)
        return make_response(candidates,image.shape[1],image.shape[0],app.state.version)
    except Exception as error:
        LOG.exception('CV inference failed')
        raise HTTPException(500, 'Ошибка обработки фото модулем CV.') from error
    finally:
        LOCK.release()


def local_review(request, write=False):
    if request.url.hostname not in ('127.0.0.1','localhost','::1') or not request.client or request.client.host not in ('127.0.0.1','::1','testclient'):
        raise HTTPException(403,'Окно сравнения доступно только на компьютере с сервером.')
    origin=request.headers.get('origin')
    if origin and origin!=str(request.base_url).rstrip('/'):
        raise HTTPException(403,'Недопустимый источник запроса.')
    if write and not secrets.compare_digest(request.headers.get('x-review-token',''),app.state.review_token):
        raise HTTPException(403,'Обновите страницу сравнения.')


@app.get('/review',response_class=HTMLResponse)
def review(request:Request):
    local_review(request)
    return HTMLResponse((ROOT/'comparison.html').read_text(),headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
        'Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; frame-ancestors 'none'"})


@app.get('/models')
def models(request:Request):
    local_review(request)
    app.state.engine.refresh_catalog()
    report=ROOT/'benchmarks/specialists-v1/report.json'
    return dict(models=list(app.state.engine.entries.values()),selection=app.state.engine.selection(),
                token=app.state.review_token,names=NAMES,benchmark=json.loads(report.read_text()) if report.exists() else None,
                images=[{k:v for k,v in row.items() if k!='path'} for row in app.state.gallery.values()])


@app.get('/training',response_class=HTMLResponse)
def training_page(request:Request):
    local_review(request)
    return HTMLResponse((ROOT/'training-panel.html').read_text(),headers={'Cache-Control':'no-store',
        'X-Content-Type-Options':'nosniff',
        'Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"})


@app.get('/training-status')
def training_status(request:Request):
    local_review(request)
    app.state.engine.refresh_catalog()
    integration=ROOT.parent/'.integration'
    labels={'roboflow-oil':('Следы масла','oil_trained'),
            'roboflow-damage':('Трещины, повреждения и эрозия','damage_trained'),
            'roboflow-corrosion':('Коррозия','corrosion_trained')}
    path=integration/'roboflow-training-queue.json'
    if not path.exists():
        return dict(state='idle',jobs=[],completedJobs=0,totalJobs=0,updatedAt=None,confirmedUserPhotos=130)
    try:
        queue=json.loads(path.read_text())
        jobs=[]
        for item in queue['jobs']:
            label,model_id=labels.get(item['id'],(item['id'],None))
            jobs.append(dict(id=item['id'],name=label,modelId=model_id,
                ready=model_id in app.state.engine.entries,
                **{key:item[key] for key in ('state','epoch','completedEpochs','epochs','batch','batchesPerEpoch','elapsedSeconds') if key in item}))
        crack=integration/'crack-seg-training.json'
        if crack.exists():
            item=json.loads(crack.read_text())
            jobs.append(dict(id='crack-seg',name='Трещины · Crack-Seg',modelId='crack_trained',
                ready='crack_trained' in app.state.engine.entries,
                **{key:item[key] for key in ('state','epoch','completedEpochs','epochs','batch','batchesPerEpoch','elapsedSeconds') if key in item}))
        return dict(state=queue['state'],currentJob=queue.get('currentJob'),jobs=jobs,
            completedJobs=queue['completedJobs'],totalJobs=len(queue['jobs']),confirmedUserPhotos=130,
            updatedAt=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat())
    except (ValueError,KeyError,TypeError):
        raise HTTPException(503,'Статус обучения временно недоступен.')


@app.get('/review/image/{image_id}')
def case_photo(image_id:str,request:Request):
    local_review(request)
    row=app.state.gallery.get(image_id)
    if row is None:raise HTTPException(404,'Фото не найдено.')
    return FileResponse(row['path'],media_type='image/jpeg')


class Selection(BaseModel):
    models:list[str]=Field(min_length=1,max_length=16)
    threshold:float=Field(default=.25,ge=.05,le=.9,allow_inf_nan=False)


@app.post('/selection')
def selection(config:Selection,request:Request):
    local_review(request,True)
    if not LOCK.acquire(blocking=False):raise HTTPException(503,'Дождитесь окончания анализа.')
    try:
        selected=app.state.engine.select(config.models,config.threshold)
        return dict(selection=selected,health=health())
    except ValueError as error:
        raise HTTPException(422,str(error)) from error
    except Exception as error:
        LOG.exception('Model selection failed')
        raise HTTPException(500,'Модель не прошла пробный запуск; прежний выбор сохранён.') from error
    finally:
        LOCK.release()


@app.post('/compare')
def compare(request:Request,file:UploadFile=File(...),models:str=Form(...),threshold:float=Form(.25)):
    local_review(request,True)
    ids=models.split(',')
    if not ids or len(ids)>16 or len(ids)!=len(set(ids)) or any(mid not in app.state.engine.entries for mid in ids):
        raise HTTPException(422,'Выберите от одной до шестнадцати моделей.')
    if not math.isfinite(threshold) or not .05<=threshold<=.9:
        raise HTTPException(422,'Порог должен быть от 0.05 до 0.90.')
    image=decode_photo(file.file.read(10*1024*1024+1))
    if not LOCK.acquire(blocking=False):raise HTTPException(503,'Сервис занят. Повторите после окончания анализа.')
    try:
        results=[]
        for mid in ids:
            try:
                results.append(app.state.engine.predict(mid,image,threshold))
            except Exception:
                LOG.exception('Comparison failed for %s',mid)
                results.append(dict(id=mid,name=app.state.engine.entries[mid]['name'],status='error',
                                    error='Модель не обработала снимок. Подробности в журнале CV.'))
        return dict(results=results,threshold=threshold)
    finally:
        LOCK.release()
