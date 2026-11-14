#!/usr/bin/env python3
"""Freeze the official Crack-Seg masks, excluding duplicate views across splits."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import zipfile

from PIL import Image
import yaml

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'ExternalData/crack-seg-v1'
OUT=ROOT/'ComputerVision/datasets/crack-seg-ultralytics-v1'


def sha(data):return hashlib.sha256(data).hexdigest()


def main():
    if OUT.exists():raise ValueError('Frozen output already exists; use another version.')
    source=SOURCE/'crack-seg.zip'
    downloaded=source if source.exists() else SOURCE/'crack-seg.zip.part'
    asset=json.loads((SOURCE/'release-asset.json').read_text())
    data=downloaded.read_bytes();digest=sha(data)
    if len(data)!=asset['size'] or 'sha256:'+digest!=asset['digest']:
        raise ValueError('Official release size or SHA256 does not match')
    config=yaml.safe_load((SOURCE/'original-data.yaml').read_text())
    if config['names']!={0:'crack'}:raise ValueError('Unexpected source classes')
    records={key:[] for key in ('train','val','test')}
    additions=[];skipped=[];seen={};family_splits={};raw_counts={};segments=Counter();normals=Counter()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip():raise ValueError('Corrupt archive entry')
        names=set(archive.namelist())
        for split in ('test','val','train'):
            photos=sorted(n for n in names if n.startswith('images/'+split+'/') and Path(n).suffix.lower() in ('.jpg','.jpeg','.png'))
            raw_counts[split]=len(photos)
            for name in photos:
                image_data=archive.read(name)
                label_name='labels/'+split+'/'+Path(name).with_suffix('.txt').name
                if label_name not in names:raise ValueError('Missing segmentation annotation: '+name)
                label=archive.read(label_name)
                count=0;reason=None
                try:
                    for line in label.decode().splitlines():
                        values=list(map(float,line.split()))
                        if len(values)<7 or len(values)%2!=1 or values[0]!=0 or any(not math.isfinite(x) or not 0<=x<=1 for x in values[1:]):
                            raise ValueError('Invalid segmentation polygon')
                        points=list(zip(values[1::2],values[2::2]))
                        area=abs(sum(x*y2-x2*y for (x,y),(x2,y2) in zip(points,points[1:]+points[:1])))/2
                        if area<=1e-10:raise ValueError('Degenerate segmentation polygon')
                        count+=1
                    with Image.open(io.BytesIO(image_data)) as image:
                        pixels=image.convert('RGB');width,height=pixels.size
                        pixel_hash=sha(str(pixels.size).encode()+pixels.tobytes())
                except (ValueError,OSError,UnicodeError) as error:
                    reason=str(error)
                if reason:
                    skipped.append(dict(file=name,reason=reason));continue
                family=Path(name).stem.split('.rf.',1)[0]
                if pixel_hash in seen:
                    skipped.append(dict(file=name,reason='duplicate decoded pixels',duplicateOf=seen[pixel_hash]));continue
                if family in family_splits and family_splits[family]!=split:
                    skipped.append(dict(file=name,reason='source filename family crosses splits',retainedSplit=family_splits[family]));continue
                seen[pixel_hash]=name;family_splits[family]=split
                record=dict(file=Path(name).name,source=name,group='crack-seg-source/'+family,
                    sha256=sha(image_data),pixelSha256=pixel_hash,labelsSha256=sha(label),width=width,height=height,segments=count)
                records[split].append(record);additions.append((name,image_data,label_name,label))
                segments[split]+=count;normals[split]+=count==0
    if not all(records.values()):raise ValueError('An evaluation split is empty')
    OUT.mkdir(parents=True)
    for name,picture,label_name,label in additions:
        for relative,content in ((name,picture),(label_name,label)):
            path=OUT/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    config=dict(path=str(OUT),train='images/train',val='images/val',test='images/test',names={0:'crack'})
    (OUT/'data.yaml').write_text(yaml.safe_dump(config,sort_keys=False))
    summary=dict(createdAt=datetime.now(timezone.utc).isoformat(),task='segment',classes=['crack'],
        archiveSha256=digest,sourceAsset=asset,sourceRevision='b0b618a2564471ea0d27e8ddbd4d559fa6fe7ba2',
        sourcePage='https://docs.ultralytics.com/ru/datasets/segment/crack-seg/',
        rawSplitImages=raw_counts,splitImages={k:len(v) for k,v in records.items()},
        splitSegments=dict(segments),negativeImages=dict(normals),images=sum(map(len,records.values())),
        excluded=skipped,independenceConfirmed=False,limitations=[
            'Crack-Seg covers roads and walls, not a validated pump or metal dataset.',
            'Original polygon bytes are retained. Invalid masks and duplicate views are excluded, not relabeled.',
            'Decoded pixels and source filename families do not cross training/evaluation splits.',
            'Filename families do not establish independent physical scenes or structures.',
            'The initial public crack checkpoint may have seen some public source images; membership is unknown.',
            'Existing user annotations, working models and the old case test photos are unchanged.'
        ])
    for name,value in (('summary.json',summary),('provenance.json',records)):
        (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    (SOURCE/'ATTRIBUTION.txt').write_text('Crack-Seg dataset distributed by Ultralytics.\n'
        'Documentation: https://docs.ultralytics.com/ru/datasets/segment/crack-seg/\n'
        'Dataset citation from documentation: University, crack Dataset, Roboflow, December 2022.\n'
        'Source: https://universe.roboflow.com/university-bswxt/crack-bphdr\n'
        'Official release asset and SHA256: release-asset.json. Original configuration: original-data.yaml.\n')
    if downloaded!=source:downloaded.replace(source)
    print(json.dumps({key:summary[key] for key in ('images','rawSplitImages','splitImages','splitSegments','negativeImages')},ensure_ascii=False))
    print('Excluded:',len(skipped));print(OUT/'data.yaml')


if __name__=='__main__':main()
