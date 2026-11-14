#!/usr/bin/env python3
"""Collect attributed pump candidates; selection and labels remain manual."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request
import urllib.error

from PIL import Image, ImageOps, ImageDraw, ImageFont
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
AGENT = 'KST-Energo pump photo research/1.1 (educational local prototype)'
QUERIES = [
    ('"centrifugal pump" filetype:bitmap', 200),
    ('incategory:"Centrifugal pumps" filetype:bitmap', 150),
    ('"Kreiselpumpe" filetype:bitmap', 100),
    ('"pump impeller" filetype:bitmap', 70),
    ('"pump casing" filetype:bitmap', 60),
    ('"submersible pump" filetype:bitmap', 90),
    ('"water pump" "motor" filetype:bitmap', 90),
    ('"slurry pump" filetype:bitmap', 60),
    ('"boiler feed pump" filetype:bitmap', 60),
    ('"vertical pump" filetype:bitmap', 60),
    ('"pump" "corrosion" filetype:bitmap', 60),
    ('"pump" "USACE" "repair" filetype:bitmap', 100),
    ('"KSB" "pump" filetype:bitmap', 60),
    ('"насос" "электродвигатель" filetype:bitmap', 60),
    ('"насосная" "насосы" filetype:bitmap', 60),
    ('"bomba" "centrifuga" filetype:bitmap', 80),
    ('"pompe centrifuge" filetype:bitmap', 80),
    ('"pumping station" "pumps" -Crossness -steam -windmill filetype:bitmap', 150),
]
EXCLUDE = re.compile(r'schematic|diagram|chart|theory|scale model|drawing|illustration|'
    r'cross.section|sectional|plot|logo|symbol|patent|engraving|air blower|'
    r'fuel dispenser|gas station|petrol station|gasoline pump|hand pump|windpump|'
    r'painting|fortepan|historic american|\.svg|\.gif|deformation of', re.I)


def atomic_json(path, value):
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def get(url, limit=15*1024*1024):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': AGENT}), timeout=25) as response:
                data = response.read(limit+1)
            if len(data)>limit: raise ValueError('File exceeds download limit')
            return data
        except urllib.error.HTTPError as error:
            if attempt==3 or error.code not in (429, 502, 503, 504): raise
            delay=max(15*(attempt+1),int(error.headers.get('Retry-After','0')) if error.headers.get('Retry-After','0').isdigit() else 0)
            if delay>60: raise RuntimeError('Download deferred by source; Retry-After: '+str(delay)) from error
            print('Source temporarily busy; retry in',delay,'seconds',flush=True)
            time.sleep(delay)
        except (OSError, TimeoutError):
            if attempt==3: raise
            time.sleep(1+attempt*2)


def clean(text):
    return html.unescape(re.sub('<[^>]+>', ' ', text)).strip()


def search(out, query, limit):
    pages = []
    continuation = {}
    for number in range((limit+49)//50):
        cache = out/'search'/f'{hashlib.sha256(query.encode()).hexdigest()[:12]}-{number}.json'
        if cache.exists(): data=json.loads(cache.read_text())
        else:
            params = dict(action='query', generator='search', gsrsearch=query, gsrnamespace=6,
                gsrlimit=min(50, limit-len(pages)), prop='imageinfo', iiprop='url|extmetadata|size',
                iiurlwidth=1600, format='json', **continuation)
            data = json.loads(get('https://commons.wikimedia.org/w/api.php?'+urllib.parse.urlencode(params)))
            if data.get('error'): raise RuntimeError(data['error'])
            atomic_json(cache,data)
            time.sleep(4)
        pages.extend(data.get('query',{}).get('pages',{}).values())
        continuation=data.get('continue',{})
        if not continuation or len(pages)>=limit: break
    print('Search:',len(pages),query,flush=True)
    return pages


def photo_hash(pixels):
    # Difference hash marks candidate near-duplicates, never makes a defect label.
    gray=np.asarray(pixels.convert('L').resize((9,8),Image.Resampling.LANCZOS))
    bits=gray[:,1:]>gray[:,:-1]
    return f'{int("".join("1" if x else "0" for x in bits.flat),2):016x}'


def fetch(out,item,original=False):
    title=item['title']; info=item['imageinfo'][0]; meta=info.get('extmetadata',{})
    license_name=meta.get('LicenseShortName',{}).get('value','')
    if not (license_name.startswith('CC ') or license_name in ('CC0','Public domain')): return None
    if EXCLUDE.search(title) or not re.search(r'\.(jpg|jpeg|png)$',title,re.I): return None
    if min(info['width'],info['height'])<360: return None
    filename=str(item['pageid'])+'-'+re.sub(r'[^A-Za-z0-9]+','-',title[5:])[:70].strip('-')+'.jpg'
    path=out/'candidates'/filename
    record=out/'records'/f"{item['pageid']}.json"
    if path.exists() and record.exists(): return json.loads(record.read_text())
    try:
        url=info['url'] if original else info.get('thumburl',info['url'])
        data=get(url)
        with Image.open(io.BytesIO(data)) as image:
            if image.width*image.height>25_000_000: raise ValueError('Oversized image')
            pixels=ImageOps.exif_transpose(image).convert('RGB')
            pixels.thumbnail((1600,1600))
            raw_digest=hashlib.sha256(str(pixels.size).encode()+pixels.tobytes()).hexdigest()
            pixels.save(path,'JPEG',quality=95)
            result=dict(file=path.relative_to(out).as_posix(),title=title,pageId=item['pageid'],
                sourcePage=info['descriptionurl'],sourceImage=info['url'],downloadUrl=url,
                license=license_name,licenseUrl=meta.get('LicenseUrl',{}).get('value',''),
                author=clean(meta.get('Artist',{}).get('value','')),
                description=clean(meta.get('ImageDescription',{}).get('value','')),
                width=pixels.width,height=pixels.height,sourcePixelSha256=raw_digest,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),dhash=photo_hash(pixels),
                status='unreviewed',condition='unknown',sourceGroup='unknown')
        atomic_json(record,result)
        return result
    except Exception as error:
        return dict(title=title,pageId=item['pageid'],error=str(error))


def contacts(out,images):
    font=ImageFont.load_default(size=14)
    for start in range(0,len(images),36):
        sheet=Image.new('RGB',(1440,1320),'white');draw=ImageDraw.Draw(sheet)
        for offset,item in enumerate(images[start:start+36]):
            x=(offset%6)*240; y=(offset//6)*220
            with Image.open(out/item['file']) as original:
                thumb=ImageOps.contain(original,(234,184))
                sheet.paste(thumb,(x+(234-thumb.width)//2,y+(184-thumb.height)//2))
            text=f"{start+offset+1:03d} {item['title'][5:]}"
            draw.text((x+3,y+185),text[:30],font=font,fill='black')
            draw.text((x+3,y+203),text[30:60],font=font,fill='black')
        sheet.save(out/f'contact-{start//36+1:02d}.jpg',quality=92)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'ExternalData/pump-web-v2')
    parser.add_argument('--max-downloads',type=int,default=600)
    args=parser.parse_args();out=args.output.resolve()
    for sub in ['search','candidates','records']: (out/sub).mkdir(parents=True,exist_ok=True)
    old=json.loads((ROOT/'ExternalData/pump-web-v1/manifest.json').read_text())['images']
    excluded={item['pageId'] for item in old}
    known_pixels={item['sourcePixelSha256'] for item in old}
    lists=[];errors=[]
    for query,limit in QUERIES:
        try: lists.append(search(out,query,limit))
        except Exception as error:
            errors.append(dict(query=query,error=str(error)))
            print('Search error:',query,str(error),flush=True)
    # Interleave queries to avoid one pump series filling the entire batch.
    found={}
    for row in range(max(map(len,lists),default=0)):
        for pages in lists:
            if row>=len(pages): continue
            page=pages[row]
            if page.get('imageinfo') and page['pageid'] not in excluded:
                found.setdefault(page['pageid'],page)
    atomic_json(out/'candidates-api.json',list(found.values()))
    print('Unique new search candidates:',len(found),flush=True)
    images=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for item in pool.map(lambda value:fetch(out,value),list(found.values())[:args.max_downloads]):
            if item is None:continue
            if 'error' in item:errors.append(item);continue
            item['exactDuplicate']=item['sourcePixelSha256'] in known_pixels
            known_pixels.add(item['sourcePixelSha256'])
            if item['exactDuplicate']:continue
            images.append(item)
            if len(images)%20==0:print('Downloaded',len(images),flush=True)
    for index,item in enumerate(images):item['reviewIndex']=index+1
    atomic_json(out/'manifest.json',dict(createdAt=datetime.now(timezone.utc).isoformat(),
        purpose='Manual pump photo review; no machine labels',labelsVerified=False,
        excludedPreviousPageIds=sorted(excluded),queries=QUERIES,images=images,errors=errors))
    (out/'ATTRIBUTION.txt').write_text('\n\n'.join('\n'.join([item['file'],item['title'],
        item['author'],item['license'],item['licenseUrl'],item['sourcePage']]) for item in images)+'\n')
    contacts(out,images)
    print(json.dumps(dict(collected=len(images),errors=len(errors),directory=str(out)),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
