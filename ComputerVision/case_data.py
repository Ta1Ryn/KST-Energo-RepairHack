"""Read-only access to the frozen, user-labelled pilot dataset."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT/'datasets/case-training-ac35f818-v2'


def records(verify=False):
    # The optional annotation gallery is supplied separately from the app source.
    # An empty gallery must not prevent a clean installation from serving photos.
    if not (DATA/'summary.json').is_file() or not (DATA/'provenance.json').is_file():
        return []
    summary = json.loads((DATA/'summary.json').read_text())
    classes = [entry['code'] for entry in summary['classes']]
    result = []
    for split, entries in json.loads((DATA/'provenance.json').read_text()).items():
        for entry in entries:
            path = DATA/'images'/split/entry['file']
            label = DATA/'labels'/split/Path(entry['file']).with_suffix('.txt')
            if verify:
                if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256'] or hashlib.sha256(label.read_bytes()).hexdigest() != entry['labelsSha256']:
                    raise ValueError('Frozen dataset changed: '+entry['file'])
            boxes=[]
            for row in label.read_text().splitlines():
                cid,x,y,w,h=map(float,row.split())
                boxes.append(dict(label=classes[int(cid)],box=dict(x=x-w/2,y=y-h/2,width=w,height=h)))
            result.append(dict(id=path.stem, name=Path(entry['source']).name, source=entry['source'],
                               category=entry['source'].split('/')[0], split=split, group=entry['group'],
                               imageUrl='/review/image/'+path.stem, path=path, reference=boxes))
    return result
