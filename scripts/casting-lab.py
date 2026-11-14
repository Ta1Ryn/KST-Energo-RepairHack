#!/usr/bin/env python3
"""Local demonstration of the externally trained, top-view impeller classifier."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import urlparse
import zipfile

from PIL import Image,ImageOps

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ComputerVision'))
from casting_engine import CastingClassifier

LOCK=threading.Lock()
TOKEN=secrets.token_urlsafe(32)
REPORT=ROOT/'ComputerVision/benchmarks/casting-vit/report.json'
ARCHIVE=ROOT/'ExternalData/pump-impeller-casting/original-kaggle.zip'
predictions=json.loads((REPORT.parent/'predictions.json').read_text())
chosen=[]
for label in ('defective','ok'):
    chosen.extend([x for x in predictions if x['target']==label and x['target']==x['predicted']][:3])
chosen.extend([x for x in predictions if x['target']!=x['predicted']])
EXAMPLES=[{'id':str(i),'name':Path(x['file']).name,'reference':x['target'],'file':x['file'],'publishedModelMistake':x['target']!=x['predicted']} for i,x in enumerate(chosen)]


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,status,value,mime='application/json'):
        data=json.dumps(value,ensure_ascii=False).encode() if mime=='application/json' else value
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
    def local(self):
        return self.headers.get('Host') in ('127.0.0.1:5103','localhost:5103')
    def do_GET(self):
        if not self.local():self.send(403,{'error':'Local access only'});return
        route=urlparse(self.path).path
        if route=='/':self.send(200,(ROOT/'ComputerVision/casting-lab.html').read_bytes(),'text/html; charset=utf-8')
        elif route=='/api/status':self.send(200,{'version':self.server.model.version,'report':json.loads(REPORT.read_text()),'token':TOKEN,'examples':[{k:v for k,v in x.items() if k!='file'} for x in EXAMPLES]})
        elif route.startswith('/image/'):
            item=next((x for x in EXAMPLES if x['id']==route[7:]),None)
            if item is None:self.send(404,{'error':'Unknown example'});return
            with zipfile.ZipFile(ARCHIVE) as archive:data=archive.read(item['file'])
            self.send(200,data,'image/jpeg')
        else:self.send(404,{'error':'Not found'})
    def do_POST(self):
        host=self.headers.get('Host')
        if not self.local() or self.headers.get('X-Lab-Token')!=TOKEN or self.headers.get('Origin') not in (None,'http://'+host):
            self.send(403,{'error':'Обновите страницу перед анализом.'});return
        if self.path!='/api/predict':self.send(404,{'error':'Not found'});return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=10*1024*1024:self.send(413,{'error':'Фото должно быть меньше 10 МиБ.'});return
            data=self.rfile.read(size)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in ('JPEG','PNG','WEBP') or min(source.size)<64 or source.width*source.height>25_000_000:
                    raise ValueError('Нужен JPEG/PNG/WebP, от 64 px до 25 мегапикселей.')
                pixels=ImageOps.exif_transpose(source).convert('RGB')
        except (ValueError,OSError,Image.DecompressionBombError) as error:self.send(415,{'error':str(error)});return
        if not LOCK.acquire(blocking=False):self.send(409,{'error':'Модель занята предыдущим фото.'});return
        try:
            started=time.perf_counter();scores=self.server.model.predict([pixels])[0]
            self.send(200,{'prediction':max(scores,key=scores.get),'scores':scores,'elapsedMs':round((time.perf_counter()-started)*1000),'modelVersion':self.server.model.version})
        except Exception as error:
            print('Inference failed:',type(error).__name__,str(error),file=sys.stderr,flush=True)
            self.send(500,{'error':'Ошибка обработки фото. Проверьте окно сервера модели.'})
        finally:LOCK.release()


if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',5103),Handler)
    server.model=CastingClassifier()
    print('Ready: http://127.0.0.1:5103/ — pretrained impeller classifier (CPU)',flush=True)
    server.serve_forever()
