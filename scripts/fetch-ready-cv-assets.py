#!/usr/bin/env python3
"""Download pinned public labelled data and safetensors without remote Python code."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
RESEARCH=ROOT/'.integration/ready-dataset-research'


def download(job):
    url,path,digest=job
    if path.exists() and (not digest or hashlib.sha256(path.read_bytes()).hexdigest()==digest):return str(path.relative_to(ROOT))+' cached'
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.download')
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'KST-Energo CV research/1.0'}),timeout=90) as response,temporary.open('wb') as stream:
        shutil.copyfileobj(response,stream,1024*1024)
    actual=hashlib.sha256(temporary.read_bytes()).hexdigest()
    if digest and digest!=actual:raise ValueError('Checksum mismatch: '+str(path))
    temporary.replace(path)
    return str(path.relative_to(ROOT))+' '+str(path.stat().st_size)+' bytes'


def main():
    jobs=[];manifests=[]
    for kind,meta_file,location,allowed in [
        ('datasets','Francesco--corrosion-bi3q3-files.json','ExternalData/rf100-corrosion-parquet',lambda n:n.endswith(('.parquet','.json','.md'))),
        ('models','casting-files.json','ComputerVision/experimental/casting-vit',lambda n:n in ('model.safetensors','config.json','preprocessor_config.json','README.md')),
    ]:
        meta=json.loads((RESEARCH/meta_file).read_text());repo=meta['id'];revision=meta['sha'];directory=ROOT/location
        directory.mkdir(parents=True,exist_ok=True)
        files=[]
        for entry in meta['siblings']:
            name=entry['rfilename']
            if not allowed(name):continue
            url='https://huggingface.co/'+('datasets/' if kind=='datasets' else '')+repo+'/resolve/'+revision+'/'+urllib.parse.quote(name,safe='/')
            files.append({'file':name,'url':url,'sha256':entry.get('lfs',{}).get('sha256'),'bytes':entry.get('size')})
            jobs.append((url,directory/name,files[-1]['sha256']))
        manifest={'repo':repo,'revision':revision,'source':'https://huggingface.co/'+('datasets/' if kind=='datasets' else '')+repo,'files':files,
            'note':'Source metrics are author claims; local evaluation required. Remote code and training_args.bin are not loaded.'}
        manifests.append((directory,manifest))
    with ThreadPoolExecutor(max_workers=3) as pool:
        for line in pool.map(download,jobs):print(line,flush=True)
    for directory,manifest in manifests:
        for entry in manifest['files']:entry['downloadedSha256']=hashlib.sha256((directory/entry['file']).read_bytes()).hexdigest()
        (directory/'source-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print('All requested assets downloaded and verified.',flush=True)


if __name__=='__main__':main()
