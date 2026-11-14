#!/usr/bin/env python3
"""Prepare an experimental corrosion/crack transfer set without mixing scene families."""
from collections import Counter,defaultdict
import hashlib
import io
import json
from pathlib import Path
import re

from PIL import Image
import pyarrow.parquet as pq
import yaml

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'ExternalData/rf100-corrosion-parquet'
CASE=ROOT/'ComputerVision/datasets/pump-expanded-20261114-v1'
OUT=ROOT/'ComputerVision/datasets/surface-transfer-rf100-v1'
CLASSES=['corrosion','crack']


def pixel_hash(data):
    with Image.open(io.BytesIO(data)) as im:
        image=im.convert('RGB')
        return hashlib.sha256(str(image.size).encode()+image.tobytes()).hexdigest()


def main():
    if OUT.exists():raise ValueError('Output already exists; frozen data is not overwritten.')
    cases=json.loads((CASE/'provenance.json').read_text());definition=json.loads((CASE/'summary.json').read_text())
    old_ids={c['trainingId']:c['code'] for c in definition['classes']};records=[]
    for subset,items in cases.items():
        for item in items:
            data=(CASE/'images'/subset/item['file']).read_bytes();rows=[]
            for line in (CASE/'labels'/subset/Path(item['file']).with_suffix('.txt')).read_text().splitlines():
                values=line.split();code=old_ids[int(values[0])]
                if code in CLASSES:rows.append([CLASSES.index(code),*map(float,values[1:])])
            records.append(dict(file='case-'+item['file'],source='case/'+item['source'],group='case/'+item['group'],
                subset=subset,origin='user-reviewed',bytes=data,rows=rows,pixelSha256=pixel_hash(data)))
    original_family_splits=defaultdict(set);source_labels=Counter()
    for parquet in sorted((SOURCE/'data').glob('*.parquet')):
        original_split=parquet.name.split('-')[0]
        for item in pq.read_table(parquet).to_pylist():
            name=item['image']['path'];raw=item['image']['bytes'];w=item['width'];h=item['height']
            with Image.open(io.BytesIO(raw)) as image:
                if image.size!=(w,h) or image.format!='JPEG' or image.getexif().get(274,1)!=1:raise ValueError('Unexpected image geometry')
            prefix=re.split(r'[0-9_\- .]',name)[0].lower()
            family='common-lpt-vlc-and-unknown' if prefix in ('lpt','vlc','img') else prefix
            original_family_splits[family].add(original_split)
            # Similar bridge views and all unknown video sequences stay together in training.
            subset='train' if family=='common-lpt-vlc-and-unknown' else 'val' if family in ('kkr','mansaung','shweli') else 'test'
            rows=[]
            for category,box in zip(item['objects']['category'],item['objects']['bbox']):
                source_labels[category]+=1
                if category==1:continue  # Slippage is outside this two-class specialist.
                if category not in (2,3):raise ValueError('Unmapped source annotation')
                x,y,bw,bh=box
                if x<0 or y<0 or bw<=0 or bh<=0 or x+bw>w+.001 or y+bh>h+.001:raise ValueError('Invalid box')
                rows.append([category-2,(x+bw/2)/w,(y+bh/2)/h,bw/w,bh/h])
            records.append(dict(file='rf-'+hashlib.sha256((name+hashlib.sha256(raw).hexdigest()).encode()).hexdigest()[:24]+'.jpg',
                source=name,group='rf100/'+family,subset=subset,origin='rf100',originalSplit=original_split,
                bytes=raw,rows=rows,pixelSha256=pixel_hash(raw)))
    fingerprints=defaultdict(list)
    for record in records:fingerprints[record['pixelSha256']].append(record)
    kept=[];excluded=[]
    for items in fingerprints.values():
        if len(items)==1:kept.extend(items);continue
        own=[x for x in items if x['origin']=='user-reviewed']
        if len(own)>1:raise ValueError('Own-data duplicate needs review')
        if own:
            kept.extend(own);excluded.extend(dict(source=x['source'],reason='external repeat of user image') for x in items if x not in own)
        elif len({json.dumps(sorted(x['rows'])) for x in items})>1:
            excluded.extend(dict(source=x['source'],reason='identical image with conflicting source labels') for x in items)
        else:
            kept.append(items[0]);excluded.extend(dict(source=x['source'],reason='identical source image') for x in items[1:])
    provenance={s:[] for s in ('train','val','test')};counts={s:Counter() for s in provenance};backgrounds=Counter()
    for subset in provenance:
        (OUT/'images'/subset).mkdir(parents=True);(OUT/'labels'/subset).mkdir(parents=True)
    for record in kept:
        subset=record['subset'];name=record['file'];label=('\n'.join(str(r[0])+' '+' '.join(f'{v:.8f}' for v in r[1:]) for r in record['rows'])+ ('\n' if record['rows'] else '')).encode()
        (OUT/'images'/subset/name).write_bytes(record['bytes']);(OUT/'labels'/subset/Path(name).with_suffix('.txt')).write_bytes(label)
        counts[subset].update(r[0] for r in record['rows']);backgrounds[subset]+=not record['rows']
        provenance[subset].append({**{k:v for k,v in record.items() if k not in ('bytes','rows','subset')},
            'sha256':hashlib.sha256(record['bytes']).hexdigest(),'labelsSha256':hashlib.sha256(label).hexdigest()})
    groups={s:{r['group'] for r in rows} for s,rows in provenance.items()}
    assert not groups['train']&groups['val'] and not groups['train']&groups['test'] and not groups['val']&groups['test']
    for subset in provenance:
        if not all(counts[subset][i] for i in range(2)):raise ValueError('Missing target class in '+subset)
    inputs={'source':json.loads((SOURCE/'source-manifest.json').read_text()),'caseSummarySha256':hashlib.sha256((CASE/'summary.json').read_bytes()).hexdigest()}
    summary=dict(pilot=True,independenceConfirmed=False,images=len(kept),boxes=sum(sum(c.values()) for c in counts.values()),
        archiveSha256=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest(),
        splitImages={s:len(rows) for s,rows in provenance.items()},backgrounds=dict(backgrounds),
        classes=[dict(trainingId=i,code=c,name=c,splitBoxes={s:counts[s][i] for s in counts}) for i,c in enumerate(CLASSES)],
        sourceClasses=dict(source_labels),excluded=excluded,
        originalSourceFamiliesCrossingSplits={k:sorted(v) for k,v in original_family_splits.items() if len(v)>1},
        limitations=['RF100 data mostly shows bridge surfaces, not operating pump assemblies; transfer is experimental.',
            'All LPT/VLC and unknown IMG families are training-only. Other named source families stay together.',
            'Grouping uses filename families; physical independence remains unconfirmed.',
            'User train/val/test memberships preserved; targets filtered to corrosion and crack in a derived copy.',
            'Empty labels mean no annotated target class, not a certified healthy machine.',
            'Publisher Slippage targets are excluded; source annotations remain unchanged.',
            'Case and external test subsets must be reported separately.'])
    for name,value in [('summary.json',summary),('provenance.json',provenance),('source-inputs.json',inputs)]:
        (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
    (OUT/'data.yaml').write_text(yaml.safe_dump(dict(path=str(OUT),train='images/train',val='images/val',test='images/test',names=dict(enumerate(CLASSES))),sort_keys=False))
    print(json.dumps({k:summary[k] for k in ('images','boxes','splitImages','backgrounds','excluded')},ensure_ascii=False),flush=True)
    print(OUT/'data.yaml',flush=True)


if __name__=='__main__':main()
