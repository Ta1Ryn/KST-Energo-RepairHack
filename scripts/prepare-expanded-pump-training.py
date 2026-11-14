#!/usr/bin/env python3
"""Add reviewed pump batches to training while keeping the original holdouts fixed."""
import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import yaml

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'ComputerVision/datasets/case-training-ac35f818-v2'
OUT=ROOT/'ComputerVision/datasets/pump-expanded-20261114-v1'


def main():
    if OUT.exists():raise ValueError('Frozen output already exists; use another version.')
    summary=json.loads((BASE/'summary.json').read_text())
    provenance=json.loads((BASE/'provenance.json').read_text())
    classes=json.loads((ROOT/'ComputerVision/annotation/classes.json').read_text())
    mapping={entry['annotationId']:entry['trainingId'] for entry in summary['classes']}
    archives=[('pump-web-v1',Path('/home/nok1d/Downloads/case-annotations-yolo (1).zip')),
              ('pump-web-v2',Path('/home/nok1d/Downloads/pump-web-v2-annotations-yolo.zip'))]
    additions=[];snapshots=[];seen={r['pixelSha256'] for rows in provenance.values() for r in rows}
    for batch,source in archives:
        with zipfile.ZipFile(source) as archive:
            if archive.testzip() is not None:raise ValueError('Corrupt export')
            manifest=json.loads(archive.read('manifest.json'))
            state=json.loads(archive.read('annotations.json'))
            local=json.loads((ROOT/'ComputerVision/datasets'/batch/'annotations.json').read_text())
            if state!=local:raise ValueError('Saved labels and export differ: '+batch)
            if state['classes']!=classes:raise ValueError('Class definitions differ')
            for item in manifest['images']:
                if not item['reviewed'] or item['pixelSha256'] in seen:raise ValueError('Unreviewed or repeated image')
                name=item['file'];annotation=state['images'][Path(name).stem]
                if annotation['status'] not in ('labeled','negative'):raise ValueError('Unreviewed status')
                lines=[]
                for b in annotation['boxes']:
                    if b['classId'] not in mapping:raise ValueError('New class requires explicit checkpoint mapping')
                    lines.append(f"{mapping[b['classId']]} {b['x']+b['w']/2:.8f} {b['y']+b['h']/2:.8f} {b['w']:.8f} {b['h']:.8f}")
                label=('\n'.join(lines)+('\n' if lines else '')).encode()
                data=archive.read('images/'+name)
                additions.append((name,data,label,dict(file=name,source=batch+'/'+item['sourcePath'],
                    group='new-web-pumps-training-only',sha256=hashlib.sha256(data).hexdigest(),
                    pixelSha256=item['pixelSha256'],labelsSha256=hashlib.sha256(label).hexdigest())))
                seen.add(item['pixelSha256'])
            snapshots.append(dict(batch=batch,source=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),images=len(manifest['images'])))
    shutil.copytree(BASE,OUT,ignore=shutil.ignore_patterns('*.cache','original-annotations.zip'))
    for name,data,label,record in additions:
        (OUT/'images/train'/name).write_bytes(data)
        (OUT/'labels/train'/Path(name).with_suffix('.txt')).write_bytes(label)
        provenance['train'].append(record)
    for snap in snapshots:
        directory=ROOT/'ComputerVision/datasets'/snap['batch']/'exports';directory.mkdir(exist_ok=True)
        source=Path(snap['source']);target=directory/(snap['batch']+'-user-'+snap['sha256'][:12]+'.zip')
        if not target.exists():shutil.copy2(source,target)
        shutil.copy2(source,OUT/(snap['batch']+'-user-export.zip'))
    counts={subset:Counter() for subset in provenance};negatives=0
    for subset,records in provenance.items():
        for record in records:
            lines=(OUT/'labels'/subset/Path(record['file']).with_suffix('.txt')).read_text().splitlines()
            negatives+=not lines
            counts[subset].update(int(line.split()[0]) for line in lines)
    summary.update(images=sum(map(len,provenance.values())),sourceImages=sum(map(len,provenance.values())),
        boxes=sum(sum(c.values()) for c in counts.values()),negatives=negatives,
        groups=len({r['group'] for rows in provenance.values() for r in rows}),
        splitImages={key:len(rows) for key,rows in provenance.items()},
        archiveSha256=hashlib.sha256(json.dumps(snapshots,sort_keys=True).encode()).hexdigest(),
        sourceBatches=snapshots,baseDataset=BASE.name,
        limitations=['Original 5 validation and 12 test images remain unchanged; all new reviewed photos are training-only.',
            'New web photos are one conservative training-only group; physical equipment independence is unconfirmed.',
            'All user labels are preserved; no automated fault or normal labels were added.',
            '20 user-marked normal images are available in training, not in a new independent normal holdout.',
            'Original case holdout is small and has previously been evaluated; no industrial accuracy guarantee.'])
    for c in summary['classes']:
        cid=c['trainingId'];c['splitBoxes']={key:counts[key][cid] for key in counts};c['boxes']=sum(counts[key][cid] for key in counts)
        c['images']=sum(any(int(line.split()[0])==cid for line in (OUT/'labels'/key/Path(r['file']).with_suffix('.txt')).read_text().splitlines()) for key,rows in provenance.items() for r in rows)
        c['groups']=len({r['group'] for key,rows in provenance.items() for r in rows if any(int(line.split()[0])==cid for line in (OUT/'labels'/key/Path(r['file']).with_suffix('.txt')).read_text().splitlines())})
    config=yaml.safe_load((BASE/'data.yaml').read_text());config['path']=str(OUT)
    (OUT/'data.yaml').write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    (OUT/'provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2))
    for subset in ('val','test'):
        for record in provenance[subset]:
            for folder in ('images','labels'):
                name=record['file'] if folder=='images' else str(Path(record['file']).with_suffix('.txt'))
                assert (OUT/folder/subset/name).read_bytes()==(BASE/folder/subset/name).read_bytes()
    print(json.dumps({k:summary[k] for k in ('images','boxes','negatives','splitImages')},ensure_ascii=False),flush=True)
    print(OUT/'data.yaml',flush=True)


if __name__=='__main__':main()
