#!/usr/bin/env python3
"""Download pinned public specialist weights and verify their recorded SHA256."""
from pathlib import Path
import hashlib
import json
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    dict(id='corrosion', name='Коррозия · YOLOv8', repo='Decizez/yolov-corrosion-detection',
         revision='d470ae2a2fedd7b1eed7c90e812363b09ef063b5', remote='Lite_YOLO8_v1.pt',
         sha256='77035a7c890c91c7576b3afbd5f6750d6b59387c30268eac48fbc11c7b07730d', cached='corrosion-lite.pt',
         domain='Коррозия металлических поверхностей. Уровни коррозии не заменяют измерение толщины.',
         mapping={key:'corrosion' for key in ['Rust','copper corrosion','corroded-part','corrosion','iron rust','mild-corrosion','moderate-corrosion','rust','severe-corrosion']},
         ignored=['car']),
    dict(id='oil', name='Следы жидкости · YOLO26n', repo='kareem1232/spill_leak_detection_nano',
         revision='26eeaf9464458c81e106d019caa4ad1db43b41ba', remote='weights/best.pt',
         sha256='889f199d967d3958195922e68774e960138e69877757a349e4efe590d1596274', cached='spill-leak-nano.pt',
         domain='Авторский класс oil. Состав жидкости и наличие течи требуют проверки; описание обучающих данных не опубликовано.',
         mapping={'oil':'leak'}, ignored=[]),
    dict(id='metal', name='Поверхность металла · YOLO11s', repo='rltjr/metal-surface-defect-yolo11s-seg',
         revision='5b1505fb0ea54c61a58dc898a5c1833a8d1a8935', remote='metal-surface-defect-yolo11s-seg.pt',
         sha256='32ac4da08f4ccc20ee8c7c52bf5afe845d7aa31642dd05014bc563f0f1a53675',
         domain='Обучена на отдельных обработанных металлических деталях. Пятно или царапина не означают неисправность всего агрегата.',
         mapping={'Dark Spot':'dark_spot','Fold':'surface_fold','Oxidation':'corrosion','Porosity':'porosity','Scratch':'surface_scratch','Stain':'surface_stain'}, ignored=[]),
    dict(id='crack', name='Трещины · YOLOv8', repo='OpenSistemas/YOLOv8-crack-seg',
         revision='910c33b45c040fd8b1cb93eae3fb08ec2ca8b956', remote='yolov8n/weights/best.pt',
         sha256='cce8b30758621622a03119677709d172f2ec1a2f0452279bda37732fcffb00af',
         domain='Обучена преимущественно на дорогах и стенах. Результат на металле экспериментальный.',
         mapping={'crack':'crack','Crack':'crack','cracks':'crack'}, ignored=[]),
]


def main():
    directory=ROOT/'ComputerVision/models'
    directory.mkdir(exist_ok=True)
    catalog=[]
    for source in SOURCES:
        destination=directory/(source['id']+'.pt')
        url=f"https://huggingface.co/{source['repo']}/resolve/{source['revision']}/{source['remote']}"
        if not destination.exists():
            temporary=destination.with_suffix('.download')
            cached=ROOT/'.integration/model-research/weights'/source.get('cached','missing')
            if cached.is_file():
                shutil.copyfile(cached,temporary)
            else:
                with urllib.request.urlopen(url,timeout=60) as response,temporary.open('wb') as stream:
                    shutil.copyfileobj(response,stream)
            if hashlib.sha256(temporary.read_bytes()).hexdigest()!=source['sha256']:
                raise ValueError('Downloaded checksum mismatch: '+source['id'])
            temporary.replace(destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest()!=source['sha256']:
            raise ValueError('Existing checksum mismatch: '+source['id'])
        record={k:v for k,v in source.items() if k!='cached'}
        record.update(file=destination.relative_to(ROOT/'ComputerVision').as_posix(),url=url,experimental=True)
        catalog.append(record)
        print(source['id'],destination.stat().st_size,'bytes, SHA256 OK',flush=True)
    pointer=json.loads((ROOT/'.integration/case-training.json').read_text())
    checkpoint=Path(pointer['weights'])
    # The archive may be extracted under another user/path.
    if not checkpoint.is_file():
        checkpoint=ROOT/'ComputerVision/runs'/Path(pointer['output']).name/'weights/best.pt'
    evaluation=json.loads((checkpoint.parent.parent/'evaluation.json').read_text())
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest()!=evaluation['sha256']:
        raise ValueError('Pilot checkpoint checksum mismatch')
    pilot=directory/'case-pilot.pt'
    shutil.copy2(checkpoint,pilot)
    data=Path(pointer['data']).parent
    if not data.is_dir():data=ROOT/'ComputerVision/datasets'/data.name
    summary=json.loads((data/'summary.json').read_text())
    split=summary['splitImages']
    domain=(f"Обучена на наборе из {summary['images']} фото: {split['train']} train, {split['val']} val, {split['test']} test. "
            f"mAP50 {evaluation['map50']:.4f}; качество экспериментальное, требуется проверка инженером.")
    catalog.append(dict(id='case_pilot',name=f"Наша разметка {summary['images']} фото · YOLO11n",file='models/case-pilot.pt',
                        sha256=hashlib.sha256(pilot.read_bytes()).hexdigest(),experimental=True,
                        domain=domain,
                        mapping={name:name for name in evaluation['classes']},ignored=[],
                        sourceRun=checkpoint.parent.parent.name))
    (directory/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
