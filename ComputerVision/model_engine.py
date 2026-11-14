"""Pinned specialist checkpoints, explicit label mapping, and conservative UI responses."""
from pathlib import Path
import hashlib
import importlib
import json
import math
import threading
import time

import numpy as np
import torch
from ultralytics import YOLO
from ultralytics.utils import DEFAULT_CFG_DICT

ROOT = Path(__file__).resolve().parent
SELECTION = ROOT/'model-selection.local.json'
NAMES = {
    'corrosion':'Признаки коррозии', 'leak':'Возможные следы жидкости', 'crack':'Возможная трещина',
    'insulation_damage':'Повреждение изоляции', 'heat_damage':'Видимые следы нагрева',
    'contamination':'Грязь / отложения', 'mechanical_damage':'Механическое повреждение',
    'missing_part':'Возможное отсутствие детали', 'wear':'Износ / эрозия',
    'dark_spot':'Тёмное пятно', 'surface_fold':'Складка поверхности', 'porosity':'Пористость поверхности',
    'surface_scratch':'Царапина', 'surface_stain':'Пятно на поверхности',
}
LIQUID_MASK_CONFIDENCE = .55
LIQUID_CONFIRMATION = {'oil_trained': .45, 'case_pilot': .75}
LIQUID_OVERLAP = .25
SAFE_EXTRA = {
    'ultralytics.utils.IterableSimpleNamespace',
    'ultralytics.utils.loss.E2ELoss', 'ultralytics.utils.loss.BboxLoss',
    'ultralytics.utils.loss.v8SegmentationLoss', 'ultralytics.utils.loss.BCEDiceLoss',
    'ultralytics.utils.loss.MultiChannelDiceLoss', 'ultralytics.utils.tal.TaskAlignedAssigner',
}


def checkpoint_getattr(obj, name):
    # Older Ultralytics Segment checkpoints store this one function reference.
    from ultralytics.nn.modules.head import Detect
    if obj is Detect and name == 'forward':
        return Detect.forward
    raise ValueError('Unsupported checkpoint attribute')


def restricted_checkpoint(path):
    """Only installed, explicitly supported model classes may be deserialized."""
    allowed=[]
    for name in torch.serialization.get_unsafe_globals_in_checkpoint(path):
        if name == 'builtins.getattr':
            allowed.append((checkpoint_getattr, 'builtins.getattr'))
            continue
        if not name.startswith(('torch.nn.modules.','ultralytics.nn.modules.','ultralytics.nn.tasks.')) and name not in SAFE_EXTRA:
            raise ValueError('Unsupported checkpoint type: '+name)
        module,attribute=name.rsplit('.',1)
        obj=getattr(importlib.import_module(module),attribute)
        if name not in SAFE_EXTRA and (not isinstance(obj,type) or not issubclass(obj,torch.nn.Module)):
            raise ValueError('Unsupported model class: '+name)
        allowed.append(obj)
    with torch.serialization.safe_globals(allowed):
        return torch.load(path,map_location='cpu',weights_only=True)


def load_model(path):
    checkpoint=restricted_checkpoint(path)
    network=checkpoint.get('ema') or checkpoint.get('model')
    if not isinstance(network,torch.nn.Module):
        raise ValueError('Checkpoint contains no model')
    task='segment' if network.__class__.__name__=='SegmentationModel' else 'detect'
    args=checkpoint.get('train_args',{})
    if not isinstance(args,dict):
        args=vars(args)
    network=network.float().eval()
    network.args={**DEFAULT_CFG_DICT,**{k:v for k,v in args.items() if k in DEFAULT_CFG_DICT}}
    network.args.update(task=task,device='cpu',mode='predict')
    network.pt_path=str(path)
    if not hasattr(network,'stride'):
        network.stride=torch.tensor([32.])
    # The trusted YAML constructs the API wrapper; no second checkpoint load occurs.
    wrapper=YOLO('yolo11n.yaml',task='detect',verbose=False)
    wrapper.model=network
    wrapper.ckpt=checkpoint
    wrapper.ckpt_path=str(path)
    wrapper.task=task
    wrapper.overrides={'task':task,'model':str(path),'imgsz':640}
    wrapper.model_name=str(path)
    return wrapper


def overlap(a,b):
    x=max(a['x'],b['x']);y=max(a['y'],b['y'])
    right=min(a['x']+a['width'],b['x']+b['width'])
    bottom=min(a['y']+a['height'],b['y']+b['height'])
    intersection=max(0,right-x)*max(0,bottom-y)
    return intersection/max(1e-12,a['width']*a['height']+b['width']*b['height']-intersection)


def merge_detections(detections, prefer_masks=False):
    kept=[]
    for item in sorted(detections,key=lambda d:d['confidence'],reverse=True):
        old=next((d for d in kept if d['label']==item['label'] and overlap(d['box'],item['box'])>.5),None)
        if old:
            sources=sorted(set(old.get('modelIds',[old['modelId']])+item.get('modelIds',[item['modelId']])))
            if prefer_masks and item.get('polygon') and not old.get('polygon'):
                kept[kept.index(old)]=dict(item,modelIds=sources)
            else:
                old['modelIds']=sources
        else:
            kept.append(dict(item,modelIds=sorted(set(item.get('modelIds',[item['modelId']])))))
    return kept[:150]


class ModelEngine:
    def __init__(self):
        self.entries={}
        self.models={}
        self.lock=threading.RLock()
        self.catalog_stamp=None
        self.refresh_catalog()
        torch.set_num_threads(min(4,max(1,torch.get_num_threads())))

    def refresh_catalog(self):
        path=ROOT/'models/catalog.json'
        with self.lock:
            stamp=path.stat().st_mtime_ns
            if stamp==self.catalog_stamp:
                return
            items=json.loads(path.read_text())
            entries={item['id']:item for item in items}
            if len(entries)!=len(items):
                raise ValueError('Duplicate model catalog IDs')
            for mid in list(self.models):
                if mid not in entries or entries[mid]['sha256']!=self.entries[mid]['sha256']:
                    del self.models[mid]
            self.entries=entries
            self.catalog_stamp=stamp

    def get(self,model_id):
        with self.lock:
            self.refresh_catalog()
            if model_id not in self.entries:
                raise ValueError('Unknown model: '+model_id)
            if model_id not in self.models:
                entry=self.entries[model_id]
                path=(ROOT/entry['file']).resolve()
                if not path.is_relative_to((ROOT/'models').resolve()):
                    raise ValueError('Invalid model path')
                if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:
                    raise ValueError('Model checksum mismatch: '+model_id)
                model=load_model(path)
                classes=set(model.names.values())
                unmapped=classes-set(entry['mapping'])-set(entry['ignored'])
                if unmapped:
                    raise ValueError('Unmapped model labels: '+', '.join(sorted(unmapped)))
                self.models[model_id]=model
            return self.models[model_id]

    def predict(self,model_id,image,confidence=.25,include_masks=True):
        if not math.isfinite(confidence) or not .05<=confidence<=.9:
            raise ValueError('Порог должен быть от 0.05 до 0.90.')
        with self.lock:
            started=time.perf_counter()
            model=self.get(model_id)
            entry=self.entries[model_id]
            prediction=model.predict(image,imgsz=512 if model_id=='metal' else 640,conf=confidence,iou=.5,
                                     device='cpu',max_det=150,verbose=False)[0]
            detections=[]
            boxes=prediction.boxes
            if boxes is not None:
                for index,(coordinates,cid,score) in enumerate(zip(boxes.xyxyn.cpu().tolist(),boxes.cls.cpu().tolist(),boxes.conf.cpu().tolist())):
                    original=model.names[int(cid)]
                    if original in entry['ignored']:
                        continue
                    label=entry['mapping'][original]
                    x1,y1,x2,y2=[max(0.,min(1.,float(v))) for v in coordinates]
                    if not all(math.isfinite(v) for v in [x1,y1,x2,y2,score]) or x2<=x1 or y2<=y1:
                        continue
                    detection=dict(label=label,name=NAMES[label],sourceLabel=original,confidence=float(score),
                                   modelId=model_id,modelName=entry['name'],
                                   box=dict(x=x1,y=y1,width=x2-x1,height=y2-y1))
                    if include_masks and prediction.masks is not None:
                        polygon=prediction.masks.xyn[index]
                        step=max(1,math.ceil(len(polygon)/96))
                        points=[[round(float(np.clip(x,0,1)),6),round(float(np.clip(y,0,1)),6)] for x,y in polygon[::step]]
                        if len(points)>=3 and all(math.isfinite(v) for point in points for v in point):
                            detection['polygon']=points
                    detections.append(detection)
            return dict(id=model_id,name=entry['name'],status='ok',detections=merge_detections(detections),
                        elapsedMs=round((time.perf_counter()-started)*1000,1),experimental=True,
                        domain=entry['domain'],threshold=confidence,sha256=entry['sha256'])

    def selection(self):
        self.refresh_catalog()
        if not SELECTION.exists():
            return {'models':['legacy'],'threshold':.25}
        selected=json.loads(SELECTION.read_text())
        ids=selected.get('models',[])
        if not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)) or any(i not in self.entries and i!='legacy' for i in ids):
            raise ValueError('Invalid active model configuration')
        if 'legacy' in ids and len(ids)>1:
            raise ValueError('Legacy model must run separately')
        threshold=float(selected.get('threshold',.25))
        if not math.isfinite(threshold) or not .05<=threshold<=.9:
            raise ValueError('Invalid active confidence threshold')
        return {'models':ids,'threshold':threshold}

    def select(self,ids,threshold):
        self.refresh_catalog()
        if not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)) or any(i not in self.entries and i!='legacy' for i in ids):
            raise ValueError('Выберите известные модели.')
        if 'legacy' in ids and len(ids)>1:
            raise ValueError('Прежний CV выбирается отдельно.')
        if not isinstance(threshold,(int,float)) or not math.isfinite(threshold) or not .05<=threshold<=.9:
            raise ValueError('Порог должен быть от 0.05 до 0.90.')
        with self.lock:
            # Validate a real inference before activating a model for Android.
            for model_id in ids:
                if model_id!='legacy':
                    self.predict(model_id,np.zeros((128,128,3),dtype=np.uint8),threshold,False)
            config=dict(models=ids,threshold=threshold)
            temporary=SELECTION.with_suffix('.tmp')
            temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2))
            temporary.replace(SELECTION)
        return config

    def version(self,selection=None):
        selection=selection or self.selection()
        identity={**selection,'weights':{i:self.entries[i]['sha256'] for i in selection['models'] if i!='legacy'},
                  'pipeline':'segmentation-liquid-consensus-v1','liquidMaskThreshold':LIQUID_MASK_CONFIDENCE,
                  'liquidConfirmation':LIQUID_CONFIRMATION,'liquidOverlap':LIQUID_OVERLAP}
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:12]
        return 'repairhack-cv-v2-'+digest

    def analyze(self,image):
        selection=self.selection()
        if 'legacy' in selection['models']:
            raise ValueError('Legacy selection is handled by the legacy pipeline')
        with self.lock:
            results=[self.predict(i,image,selection['threshold'],True) for i in selection['models']]
        raw=[d for result in results for d in result['detections']]
        # Жидкость показываем только по настоящей маске и независимому подтверждению.
        # Никакие контуры не строятся из прямоугольной разметки детекторов.
        confirmation=[d for d in raw if d['label']=='leak' and d['modelId'] in LIQUID_CONFIRMATION
                      and d['confidence']>=LIQUID_CONFIRMATION[d['modelId']]]
        liquid=[]
        for mask in raw:
            if mask['label']!='leak' or not mask.get('polygon') or mask['confidence']<LIQUID_MASK_CONFIDENCE:
                continue
            supporters=[d for d in confirmation if d['modelId']!=mask['modelId'] and overlap(mask['box'],d['box'])>=LIQUID_OVERLAP]
            if supporters:
                liquid.append(dict(mask,modelIds=sorted({mask['modelId'],*(d['modelId'] for d in supporters)})))
        detections=merge_detections([d for d in raw if d['label']!='leak']+liquid,prefer_masks=True)
        labels=list(dict.fromkeys(NAMES[d['label']] for d in detections))
        summary='Возможные признаки: '+', '.join(labels)+'.' if labels else 'Модели не выделили уверенных признаков при выбранном пороге.'
        coverage=', '.join(self.entries[i]['name'] for i in selection['models'])
        summary+=' Проверка: '+coverage+'. Экспериментальный результат.'
        return dict(modelVersion=self.version(selection),visualStatus='possibleDefect' if detections else 'checkRecommended',
                    summary=summary,recommendation='Подтвердить отмеченные области осмотром и показаниями датчиков. Отсутствие отметок не подтверждает исправность. Состав жидкости по фото не подтверждается.',
                    confidence=max((d['confidence'] for d in detections),default=None),detections=detections,
                    modelResults=[{k:v for k,v in r.items() if k!='detections'} for r in results])
