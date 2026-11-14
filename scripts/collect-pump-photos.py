#!/usr/bin/env python3
"""Collect attributed public pump photos into an UNLABELLED review batch."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
from PIL import Image, ImageOps

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'ExternalData/pump-web-v1'
AGENT='KST-Energo photo research/1.0 (educational local prototype)'
QUERIES=['"centrifugal pump" filetype:bitmap','"electric pump" filetype:bitmap',
         '"pump impeller" filetype:bitmap','"насосный агрегат" filetype:bitmap',
         '"pump" "corrosion" filetype:bitmap']
EXCLUDE=re.compile(r'schematic|diagram|chart|theory|scale model|label|drawing|illustration|cross.section|sectional|plot|logo|symbol|patent|[12][0-9]{3}.*engraving|centrifugal [r2]\.|air blower|schem|historic american',re.I)


def get(url,limit=12*1024*1024):
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':AGENT}),timeout=30) as response:
        data=response.read(limit+1)
    if len(data)>limit:raise ValueError('File exceeds research download limit')
    return data


def clean(value):return html.unescape(re.sub('<[^>]+>',' ',value)).strip()


def search(query):
    params=dict(action='query',generator='search',gsrsearch=query,gsrnamespace=6,gsrlimit=40,
                prop='imageinfo',iiprop='url|extmetadata|size',iiurlwidth=1600,format='json')
    data=json.loads(get('https://commons.wikimedia.org/w/api.php?'+urllib.parse.urlencode(params)))
    (OUT/('search-'+hashlib.sha256(query.encode()).hexdigest()[:10]+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2))
    return list(data.get('query',{}).get('pages',{}).values())


def fetch(item):
    info=item['imageinfo'][0];meta=info.get('extmetadata',{});title=item['title']
    license=meta.get('LicenseShortName',{}).get('value','')
    if not (license.startswith('CC ') or license in ('CC0','Public domain')) or EXCLUDE.search(title):return None
    if not re.search(r'\.(jpg|jpeg|png)$',title,re.I) or min(info['width'],info['height'])<300:return None
    name=str(item['pageid'])+'-'+re.sub(r'[^A-Za-z0-9]+','-',title[5:])[:65].strip('-')+'.jpg'
    path=OUT/'photos/Насосы'/name
    url=info.get('thumburl',info['url'])
    try:
        data=get(url)
        with Image.open(io.BytesIO(data)) as original:
            if original.width*original.height>25_000_000:raise ValueError('Oversized image')
            pixels=ImageOps.exif_transpose(original).convert('RGB');pixels.thumbnail((1600,1600))
            digest=hashlib.sha256(str(pixels.size).encode()+pixels.tobytes()).hexdigest()
            if not path.exists():pixels.save(path,'JPEG',quality=95)
            width,height=pixels.size
        return dict(file=path.relative_to(OUT).as_posix(),title=title,pageId=item['pageid'],
                    sourcePage=info['descriptionurl'],sourceImage=info['url'],downloadUrl=url,
                    license=license,licenseUrl=meta.get('LicenseUrl',{}).get('value',''),
                    author=clean(meta.get('Artist',{}).get('value','')),description=clean(meta.get('ImageDescription',{}).get('value','')),
                    width=width,height=height,sourcePixelSha256=digest,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    status='unreviewed',condition='unknown',sourceGroup='unknown',
                    warning='No verified defect or healthy label. May depict exhibits, cutaways or non-target pumps; review before training.')
    except Exception as error:return dict(title=title,error=str(error))


def main():
    (OUT/'photos/Насосы').mkdir(parents=True,exist_ok=True)
    found={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for pages in pool.map(search,QUERIES):
            for page in pages:
                if page.get('imageinfo'):found[page['pageid']]=page
    images=[];errors=[];seen=set()
    with ThreadPoolExecutor(max_workers=3) as pool:
        for item in pool.map(fetch,list(found.values())[:160]):
            if item is None:continue
            if 'error' in item:errors.append(item);continue
            item['exactDuplicate']=item['sourcePixelSha256'] in seen;seen.add(item['sourcePixelSha256']);images.append(item)
            print('Collected',len(images),item['title'],flush=True)
    report=dict(createdAt=datetime.now(timezone.utc).isoformat(),purpose='Manual review before pump defect training',
                labelsVerified=False,queries=QUERIES,images=images,errors=errors)
    (OUT/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    (OUT/'ATTRIBUTION.txt').write_text('\n\n'.join('\n'.join([item['file'],item['title'],item['author'],item['license'],item['licenseUrl'],item['sourcePage']]) for item in images)+'\n')
    (OUT/'README.txt').write_text('Снимки для ручного отбора и разметки, НЕ готовый обучающий датасет.\n'
        'Сначала исключить схемы, макеты, нерелевантные насосы и повторные ракурсы одного агрегата.\n'
        'Не считать отсутствие заметного дефекта доказательством исправности; статус пока unreviewed.\n'
        'Классы повреждений и группы физического оборудования назначает человек.\n'
        'Источник, автор и лицензия каждого изображения сохранены в manifest.json и ATTRIBUTION.txt.\n')
    print(json.dumps(dict(collected=len(images),errors=len(errors),directory=str(OUT)),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
