"""Local image review window backed by the running, real ComputerVision service."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import math
import mimetypes
import urllib.parse
import urllib.request
import urllib.error
import importlib
import sys
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
case=importlib.import_module("case-demo")
scenario_lock=threading.Lock()
scenario_state={'running':False,'name':None,'error':None}
review_client=None

def reader():
    global review_client
    if review_client is None:
        review_client=case.Client(json.loads((case.STATE/"demo-engineer-login.json").read_text())["pin"])
    return review_client

def case_documents():
    fixture=json.loads((case.STATE/'case-demo.json').read_text())
    allowed={item['id'] for item in fixture['documents']}
    documents=reader().call('GET','/api/documents?equipmentId='+fixture['equipmentId'])
    return [item for item in documents if item['id'] in allowed]

def scenario(name,value=None,seconds=20):
    global scenario_state
    sim=importlib.import_module("sensor-console")
    try:
        runner=sim.Simulator(verbose=False)
        if name in ('heat','cool'):getattr(runner,name)(seconds)
        elif name in ('pressure','vibration'):getattr(runner,name)(value,seconds)
        elif name=='temperature':runner.ramp({'bearing':value,'winding':value+15},seconds)
        else:runner.send(dict(zip(sim.KEYS,sim.PRESETS[name])))
        scenario_state={'running':False,'name':name,'error':None}
    except (OSError,ValueError,RuntimeError) as error:
        scenario_state={'running':False,'name':name,'error':str(error)}
    finally:
        scenario_lock.release()

ROOT=Path(__file__).resolve().parent
PHOTOS=ROOT.parent/'CaseFiles/Насосные агрегаты'


def ml_status():
    datasets=[]
    for folder in ('case-v1','pump-web-v1','pump-web-v2','defects-review-200-v1'):
        directory=ROOT/'datasets'/folder
        catalog=directory/'catalog.json';annotations=directory/'annotations.json'
        if not catalog.exists():continue
        images=json.loads(catalog.read_text())['images']
        labels=json.loads(annotations.read_text())['images'] if annotations.exists() else {}
        accepted=[labels[i['id']] for i in images if labels.get(i['id'],{}).get('status') in ('labeled','negative')]
        datasets.append({'name':folder,'photos':len(images),'reviewed':len(accepted),'boxes':sum(len(a['boxes']) for a in accepted)})
    pointers=[ROOT.parent/'.integration'/name for name in ('case-training.json','reviewed200-training.json')]
    pointer=max((p for p in pointers if p.is_file()),key=lambda p:p.stat().st_mtime)
    training=json.loads(pointer.read_text())
    live=Path(training.get('statusFile',ROOT/'runs'/Path(training['output']).name/'status.json'))
    if live.is_file():training.update(json.loads(live.read_text()))
    deployed=next((entry for entry in json.loads((ROOT/'models/catalog.json').read_text()) if entry['id']=='case_pilot'),{})
    summary={}
    deployed_status=ROOT/'runs'/deployed.get('sourceRun','missing')/'status.json'
    if deployed_status.is_file():
        info=json.loads(deployed_status.read_text())
        summary_file=ROOT/'datasets'/Path(info['data']).parent.name/'summary.json'
        if summary_file.is_file():summary=json.loads(summary_file.read_text())
    batches={item['batch'] for item in summary.get('sourceBatches',[])}
    return {'datasets':datasets,'trainingState':training.get('state'),'epoch':training.get('epoch'),'epochs':training.get('epochs'),
        'trainingRun':Path(training['output']).name,
        'trainedOnPhotos':summary.get('splitImages',{}).get('train'),'modelDatasetPhotos':summary.get('images'),
        'newBatchTrained':{'pump-web-v1','pump-web-v2'}<=batches,'deployedRun':deployed.get('sourceRun')}


class Preview(BaseHTTPRequestHandler):
    def send(self,status,content,mime):
        self.send_response(status);self.send_header('Content-Type',mime)
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
    def do_GET(self):
        route=urllib.parse.urlparse(self.path).path
        if route=='/': self.send(200,(ROOT/'preview.html').read_bytes(),'text/html; charset=utf-8')
        elif route in ('/sensors','/sensors/'):
            self.send(200,(ROOT/'sensor-panel.html').read_bytes(),'text/html; charset=utf-8')
        elif route in ('/presentation','/presentation/'):
            self.send(200,(ROOT/'presentation.html').read_bytes(),'text/html; charset=utf-8')
        elif route.startswith('/screen/'):
            name=urllib.parse.unquote(route[8:])
            allowed={'pin.png','home.png','passport.png','graphs.png','inspection.png','analysis.png','admin.png','admin-access.png','nameplate.png'}
            path=ROOT.parent/'.integration'/('presentation-'+name)
            if name not in allowed or not path.exists():self.send_error(404);return
            self.send(200,path.read_bytes(),'image/png')
        elif route=='/demo/verification':
            path=ROOT.parent/'.integration/presentation-verification.json'
            self.send(200,path.read_bytes() if path.exists() else b'{"checks":[],"evidence":""}','application/json')
        elif route=='/demo/ml-status':
            self.send(200,json.dumps(ml_status(),ensure_ascii=False).encode(),'application/json')
        elif route=='/demo/documents' or route.startswith('/demo/document/'):
            try:
                documents=case_documents()
                if route=='/demo/documents':
                    items=[{'id':d['id'],'name':d['originalName'],'category':d['category'],
                            'url':'/demo/document/'+d['id']} for d in documents]
                    self.send(200,json.dumps(items,ensure_ascii=False).encode(),'application/json')
                else:
                    document_id=route.removeprefix('/demo/document/')
                    if document_id not in {d['id'] for d in documents}:
                        self.send_error(404);return
                    request=urllib.request.Request(case.BASE+'/api/documents/'+document_id,
                        headers={'Authorization':'Bearer '+reader().token})
                    with urllib.request.urlopen(request,timeout=30) as response:
                        content=response.read(10*1024*1024+1)
                    if len(content)>10*1024*1024 or not content.startswith(b'%PDF'):
                        self.send_error(502);return
                    self.send(200,content,'application/pdf')
            except urllib.error.HTTPError as error:
                self.send(error.code,b'{"detail":"Document is unavailable"}','application/json')
            except (OSError,ValueError):
                self.send(503,b'{"detail":"Documents are temporarily unavailable"}','application/json')
        elif route=='/demo/state':
            try:
                eid=json.loads((case.STATE/'case-demo.json').read_text())['equipmentId']
                client=reader()
                data={'card':client.call('GET','/api/equipment/'+eid),'history':client.call('GET','/api/equipment/'+eid+'/telemetry-history?hours=24&points=120'),'scenario':scenario_state}
                self.send(200,json.dumps(data,ensure_ascii=False).encode(),'application/json')
            except (OSError,ValueError):self.send(503,b'{"detail":"Demo unavailable"}','application/json')
        elif route=='/photos':
            items=[p.name for p in sorted(PHOTOS.iterdir()) if p.suffix.lower() in ('.jpg','.jpeg','.png')]
            self.send(200,json.dumps(items,ensure_ascii=False).encode(),'application/json')
        elif route.startswith('/photo/'):
            name=urllib.parse.unquote(route[7:]);path=(PHOTOS/name).resolve()
            if path.parent!=PHOTOS.resolve() or path.suffix.lower() not in ('.jpg','.jpeg','.png') or not path.exists():
                self.send_error(404);return
            self.send(200,path.read_bytes(),mimetypes.guess_type(path.name)[0] or 'image/jpeg')
        else: self.send_error(404)
    def do_POST(self):
        global scenario_state
        if self.path=='/demo/assistant':
            host=self.headers.get('Host')
            if host not in ('127.0.0.1:5099','localhost:5099') or self.headers.get('Origin') not in (None,'http://'+host):
                self.send_error(403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=10000:raise ValueError()
                payload=json.loads(self.rfile.read(size));question=payload.get('question','')
                if not isinstance(question,str) or not 0<len(question.strip())<=2000:raise ValueError()
                fixture=json.loads((case.STATE/'case-demo.json').read_text())
                result=reader().call('POST','/api/operations/assistant',{'question':question,'equipmentId':fixture['equipmentId']})
                self.send(200,json.dumps(result,ensure_ascii=False).encode(),'application/json')
            except (ValueError,KeyError,AttributeError):self.send_error(400)
            except urllib.error.HTTPError as error:self.send(error.code,error.read(),'application/json')
            except OSError:self.send(503,b'{"detail":"Assistant unavailable"}','application/json')
            return
        if self.path=='/demo/scenario':
            host=self.headers.get('Host')
            if host not in ('127.0.0.1:5099','localhost:5099') or self.headers.get('Origin') not in (None,'http://'+host):
                self.send_error(403);return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=256:raise ValueError()
                payload=json.loads(self.rfile.read(size));name=payload['scenario']
                if name not in ('normal','warning','alarm','heat','cool','pressure','vibration','temperature'):raise ValueError()
                seconds=payload.get('seconds',20 if name in ('heat','cool') else 5)
                if type(seconds) is not int or not 1<=seconds<=60:raise ValueError()
                value=payload.get('value')
                if name in ('pressure','vibration','temperature'):
                    limits={'pressure':25,'vibration':30,'temperature':130}
                    if type(value) not in (float,int) or not math.isfinite(value) or not 0<=value<=limits[name]:raise ValueError()
                if not scenario_lock.acquire(blocking=False):
                    self.send(409,json.dumps({'detail':'Предыдущий сценарий ещё выполняется.'},ensure_ascii=False).encode(),'application/json');return
                scenario_state={'running':True,'name':name,'error':None}
                threading.Thread(target=scenario,args=(name,value,seconds),daemon=True).start()
                self.send(202,json.dumps({'message':'Сценарий запущен. Показания обновляются и в Android.'},ensure_ascii=False).encode(),'application/json')
            except (ValueError,KeyError,TypeError):self.send_error(400)
            return
        if self.path!='/analyze': self.send_error(404);return
        size=int(self.headers.get('Content-Length','0'))
        if size<=0 or size>11*1024*1024: self.send_error(413);return
        body=self.rfile.read(size)
        request=urllib.request.Request('http://127.0.0.1:8001/analyze',data=body,
            headers={'Content-Type':self.headers.get('Content-Type','application/octet-stream')})
        try:
            with urllib.request.urlopen(request,timeout=125) as response:
                self.send(response.status,response.read(),'application/json')
        except urllib.error.HTTPError as error: self.send(error.code,error.read(),'application/json')
        except OSError: self.send(503,b'{"detail":"CV service unavailable"}','application/json')


if __name__=='__main__': ThreadingHTTPServer(('127.0.0.1',5099),Preview).serve_forever()
