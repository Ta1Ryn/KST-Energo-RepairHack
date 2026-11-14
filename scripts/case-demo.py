"""Load a clearly labelled case demo; never overwrite another database."""
from pathlib import Path
import json
import subprocess
import urllib.request
import zipfile
from xml.etree import ElementTree as E

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.integration'
BASE='http://127.0.0.1:5081'
MODEL='CASE-PUMP-DEMO'
SERIAL='CASE-001'
SPECS=[
    ('bearing','Температура подшипника','°C',75,90,'Подшипник',
     'Перегрев подшипника: возможны сухое трение и износ; риск заклинивания. Проверить смазку и состояние узла.'),
    ('winding','Температура обмоток двигателя','°C',90,110,'Электродвигатель',
     'Перегрев обмоток: проверить нагрузку и вентиляцию двигателя; риск пробоя изоляции.'),
    ('vibration1x','Вибрация 1x','мм/с',2.8,4.5,'Подшипник',
     'Рост вибрации 1x: возможен дисбаланс рабочего колеса. Проверить загрязнение, эрозию и балансировку.'),
    ('vibration2x','Вибрация 2x','мм/с',1.8,3,'Подшипник',
     'Рост вибрации 2x: возможна расцентровка. Проверить муфту, опоры и центровку.'),
    ('hf','ВЧ шум','g',2.5,None,'Подшипник',
     'ВЧ шум выше 2.5 g: возможны износ подшипника или кавитация. Проверить подшипник и режим всасывания. Критический порог в кейсе не задан.')]

class Client:
    def __init__(self,pin,timeout=125):
        self.token=''
        self.timeout=timeout
        self.token=self.call('POST','/api/workforce/login',{'pin':pin})['token']
    def call(self,method,route,body=None):
        headers={'Content-Type':'application/json'}
        if self.token:headers['Authorization']='Bearer '+self.token
        req=urllib.request.Request(BASE+route,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
        with urllib.request.urlopen(req,timeout=self.timeout) as r:
            data=r.read();return json.loads(data) if data else None
    def document(self,path,eid,category):
        boundary='KstCaseDocument'
        chunks=[]
        for key,value in [('category',category),('equipmentId',eid)]:
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        chunks += [f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\nContent-Type: application/pdf\r\n\r\n'.encode(),path.read_bytes(),f'\r\n--{boundary}--\r\n'.encode()]
        req=urllib.request.Request(BASE+'/api/documents',data=b''.join(chunks),headers={'Authorization':'Bearer '+self.token,'Content-Type':'multipart/form-data; boundary='+boundary})
        with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)

def source_pdf():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer
    from reportlab.lib.styles import ParagraphStyle
    from xml.sax.saxutils import escape
    font=subprocess.check_output(['fc-match','-f','%{file}','sans'],text=True).strip()
    pdfmetrics.registerFont(TTFont('CaseText',font))
    style=ParagraphStyle('case',fontName='CaseText',fontSize=10,leading=15)
    content=[]
    for part in ['Температура','Вибрация']:
        path=ROOT/'CaseFiles'/part/f'Описание БД ({part.lower()}).docx'
        doc=E.fromstring(zipfile.ZipFile(path).read('word/document.xml'))
        content += [Paragraph(escape('Источник: '+str(path.relative_to(ROOT/'CaseFiles'))),style),Spacer(1,12)]
        for para in doc.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
            text=''.join(t.text or '' for t in para.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
            if text:content += [Paragraph(escape(text),style),Spacer(1,7)]
    output=STATE/'case-sensor-norms.pdf'
    SimpleDocTemplate(str(output)).build(content)
    return output

def nameplate():
    """Readable, printable test plate for the same equipment as the sensor simulator."""
    from PIL import Image,ImageDraw,ImageFont
    font_path=subprocess.check_output(['fc-match','-f','%{file}','sans'],text=True).strip()
    font=lambda size:ImageFont.truetype(font_path,size)
    image=Image.new('RGB',(1280,820),'#e5ebee');draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((18,18,1262,802),radius=30,fill='#cbd4da',outline='#536574',width=5)
    draw.rounded_rectangle((50,50,1230,770),radius=20,fill='#f1f4f5',outline='#8b9da7',width=2)
    draw.rectangle((90,86,1190,192),fill='#15334d')
    draw.text((120,108),'KST ENERGO',font=font(52),fill='white')
    draw.text((865,121),'ТЕСТОВЫЙ',font=font(34),fill='#a6e8da')
    draw.text((110,220),'НАСОСНЫЙ АГРЕГАТ',font=font(34),fill='#536574')
    for y,label,value in ((295,'MODEL',MODEL),(470,'SERIAL',SERIAL)):
        draw.line((105,y-14,1175,y-14),fill='#8b9da7',width=2)
        draw.text((110,y),label,font=font(26),fill='#536574')
        draw.text((110,y+45),value,font=font(66),fill='#112f42')
    draw.text((110,688),'Демонстрационные данные · не паспорт изготовителя',font=font(28),fill='#536574')
    for x,y in ((68,68),(1212,68),(68,752),(1212,752)):
        draw.ellipse((x-10,y-10,x+10,y+10),fill='#96a7b2',outline='#536574',width=2)
        draw.line((x-6,y,x+6,y),fill='#536574',width=2)
    image.save(STATE/'test-nameplate.png')
    image.save(ROOT/'BackendFork/wwwroot/demo-nameplate.png')
    (ROOT/'BackendFork/wwwroot/demo-nameplate.html').write_text(
        '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>КСТ Энерго · тестовый шильдик</title><style>body{margin:0;background:#15334d;color:white;font:18px system-ui;text-align:center;padding:24px}'
        'img{max-width:100%;max-height:80vh}a{color:#a6e8da}@media print{body{background:white;padding:0}p{display:none}}</style>'
        '<img src="/demo-nameplate.png" alt="Тестовый шильдик: MODEL CASE-PUMP-DEMO, SERIAL CASE-001">'
        '<p>Откройте сканер шильдика на планшете и наведите камеру на модель и номер.</p>'
        '<p><a href="/demo-nameplate.png" download="КСТ-тестовый-шильдик.png">Скачать шильдик</a></p></html>')
    for old in (STATE/'test-qr.png',ROOT/'BackendFork/wwwroot/demo-qr.png',ROOT/'BackendFork/wwwroot/demo-qr.html'):
        old.unlink(missing_ok=True)

def main():
    config=json.loads((ROOT/'BackendFork/postgres.local.json').read_text())
    if 'Database=fork_demo;' not in config['Database']['ConnectionString']:raise RuntimeError('Only fork_demo is permitted.')
    marker=STATE/'case-demo.json'
    nameplate()
    if marker.exists():
        saved=json.loads(marker.read_text());saved.pop('qr',None);saved['serialNumber']=SERIAL
        saved['nameplateUrl']=BASE+'/demo-nameplate.html'
        marker.write_text(json.dumps(saved,ensure_ascii=False,indent=2))
        return
    api=Client(json.loads((STATE/'demo-login.json').read_text())['pin'])
    all=api.call('GET','/api/equipment?limit=100')['items']
    existing=next((e for e in all if e['model']==MODEL and e['serialNumber']==SERIAL),None)
    norms=api.call('GET','/api/standards/sensor-norms')
    source=None
    definitions=[]
    for title,kind in [('Подшипник','case-bearing'),('Электродвигатель','case-motor')]:
        sensors=[]
        for key,name,unit,warning,alarm,owner,message in SPECS:
            if title!=owner:continue
            norm=next((n for n in norms if n.get('equipmentModel')==MODEL and n['sensorKind']==key),None)
            if norm is None:
                # The HF source has one diagnostic threshold, no invented alarm.
                norm=api.call('POST','/api/standards/sensor-norms',{'name':name+' · нормы кейса','sensorKind':key,'unit':unit,
                    'min':None,'max':alarm if alarm is not None else warning,'warningMax':warning if alarm else None,
                    'equipmentModel':MODEL,'componentType':kind,'isDemo':False})
            sensors.append({'key':key,'kind':key,'name':name,'unit':unit,'normId':norm['id']})
        if title=='Подшипник':
            sensors += [{'key':'rpm','name':'Частота вращения','unit':'об/мин'}, {'key':'rms','name':'Общая вибрация СКЗ','unit':'мм/с'}]
        definitions.append({'name':title,'type':kind,'installedAt':'2026-01-01T00:00:00Z','sensors':sensors})
    card=api.call('GET','/api/equipment/'+existing['id']) if existing else api.call('POST','/api/equipment',{
        'name':'Насос · демонстрация кейса','model':MODEL,'serialNumber':SERIAL,
        'characteristics':{'Источник':'Кейс КСТ Энерго; тестовый объект','Точные модель и заводской номер':'Условные данные демонстрации'},'components':definitions})
    eid=card['id']
    stored=api.call('GET','/api/documents?equipmentId='+eid)
    def upload(path,category):
        return next((d for d in stored if d['originalName']==path.name),None) or api.document(path,eid,category)
    source=upload(source_pdf(),'instruction')
    passport=upload(ROOT/'CaseFiles/Документация/Паспорта Насосов/1) Насос №1 ПС.pdf','passport')
    manual=upload(ROOT/'CaseFiles/Документация/Паспорта Насосов/Руководство по монтажу и эксплуатации.pdf','instruction')
    for norm in api.call('GET','/api/standards/sensor-norms'):
        if norm.get('equipmentModel')==MODEL and norm.get('sourceDocumentId')!=source['id']:
            norm['sourceDocumentId']=source['id'];api.call('PUT','/api/standards/sensor-norms/'+norm['id'],norm)
    api.call('PUT','/api/equipment/'+eid+'/passport',{'nameplate':{'model':MODEL,'serialNumber':'CASE-001','marking':'Тестовый шильдик'},
        'passport':{'purpose':'Демонстрация диагностики насосного агрегата','location':'Тестовый цех','operatingMode':'Демонстрационный',
        'parameters':{'Подшипник':'Предупреждение >75 °C; тревога >90 °C','Обмотки':'Предупреждение >90 °C; тревога >110 °C',
            'Вибрация 1x':'Предупреждение >2.8; тревога >4.5 мм/с','Вибрация 2x':'Предупреждение >1.8; тревога >3.0 мм/с',
            'ВЧ шум':'Диагностический порог >2.5 g; критическая граница не задана',
            'Документы':'Справочные документы из кейса. Принадлежность конкретному фото насоса не подтверждена.'},
        'documentIds':[passport['id'],manual['id'],source['id']]}})
    instructions=api.call('GET','/api/instructions');rules=api.call('GET','/api/recommendation-rules')
    for key,name,unit,warning,alarm,owner,message in SPECS:
        title='Кейс · '+name
        instruction=next((i for i in instructions if i['title']==title),None) or api.call('POST','/api/instructions',{
            'title':title,'text':message+' Диагноз подтвердить осмотром. Выполнять работы по регламенту предприятия.',
            'documentId':source['id'],'isDemo':False})
        for trigger in ['sensorAttention','sensorOutOfRange']:
            if not any(r.get('equipmentModel')==MODEL and r.get('sensorKind')==key and r['trigger']==trigger for r in rules):
                api.call('POST','/api/recommendation-rules',{'trigger':trigger,'equipmentModel':MODEL,'sensorKind':key,'instructionId':instruction['id'],'message':message})
    fixture=json.loads((STATE/'demo-fixtures.json').read_text());reason='Демонстрация данных полученного кейса'
    api.call('PUT','/api/admin/equipment/'+eid+'/facility',{'facilityId':fixture['objects'][-1],'reason':reason})
    api.call('POST','/api/admin/access',{'employeeId':fixture['employee'],'equipmentId':eid,'includeDescendants':False,'reason':reason})
    marker.write_text(json.dumps({'equipmentId':eid,'model':MODEL,'serialNumber':SERIAL,'nameplateUrl':BASE+'/demo-nameplate.html','documents':[passport,manual,source]},ensure_ascii=False,indent=2))
    print('Case demo, passport, original thresholds and instructions linked.',flush=True)

if __name__=='__main__':main()
