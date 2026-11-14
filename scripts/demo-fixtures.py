#!/usr/bin/env python3
"""Explicitly synthetic fixtures for this project's local fork_demo database."""
from pathlib import Path
import json
import secrets
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.integration'
BASE='http://127.0.0.1:5081'


def main():
    marker=STATE/'demo-fixtures.json'
    if marker.exists(): return
    login_file=STATE/'demo-login.json'
    if not login_file.exists(): return
    login=json.loads(login_file.read_text())
    config=json.loads((ROOT/'BackendFork/postgres.local.json').read_text())
    if 'Database=fork_demo;' not in config['Database']['ConnectionString']:
        raise RuntimeError('Demo fixtures may only be applied to fork_demo.')
    token=''
    def call(method,route,body=None):
        data=None if body is None else json.dumps(body).encode()
        headers={'Content-Type':'application/json'}
        if token: headers['Authorization']='Bearer '+token
        req=urllib.request.Request(BASE+route,data=data,headers=headers,method=method)
        with urllib.request.urlopen(req,timeout=30) as response:
            content=response.read()
            return json.loads(content) if content else None
    token=call('POST','/api/workforce/login',{'employeeNumber':login['employeeNumber'],'pin':login['pin']})['token']
    facilities=call('GET','/api/admin/facilities')
    parent=None
    objects=[]
    for code,name,kind in [('DEMO-ENTERPRISE','Демонстрационное предприятие','enterprise'),
                           ('DEMO-SITE','Демонстрационная площадка','site'),
                           ('DEMO-WORKSHOP','Тестовый цех','workshop')]:
        existing=next((f for f in facilities if f['code']==code),None)
        current=existing or call('POST','/api/admin/facilities',{'code':code,'name':name,'type':kind,'parentId':parent})
        parent=current['id'];objects.append(parent)
    departments=call('GET','/api/admin/departments')
    department=next((d for d in departments if d['code']=='DEMO-DEPT'),None) or call('POST','/api/admin/departments',{'code':'DEMO-DEPT','name':'Тестовое подразделение'})
    reason='Демонстрационные данные локального тестового запуска'
    call('POST','/api/admin/department-facilities',{'departmentId':department['id'],'facilityId':parent,'reason':reason})
    equipment=call('GET','/api/equipment')['items']
    for item in equipment:
        call('PUT','/api/admin/equipment/'+item['id']+'/facility',{'facilityId':parent,'reason':reason})
    employees=call('GET','/api/admin/employees')
    employee=next((e for e in employees if e['employeeNumber']=='DEMO-ENGINEER'),None)
    if employee is None:
        initial=f'{secrets.randbelow(10000):04d}'
        employee=call('POST','/api/admin/employees',{'employeeNumber':'DEMO-ENGINEER','fullName':'Демонстрационный инженер',
                      'pin':initial,'departmentId':department['id'],'positionTitle':'Тестовый инженер','reason':reason})
        current=f'{(int(initial)+1)%10000:04d}'
        engineer=call('POST','/api/workforce/login',{'employeeNumber':'DEMO-ENGINEER','pin':initial})
        admin=token;token=engineer['token']
        call('POST','/api/workforce/pin',{'currentPin':initial,'newPin':current});token=admin
        path=STATE/'demo-engineer-login.json'
        path.write_text(json.dumps({'employeeNumber':'DEMO-ENGINEER','fullName':'Демонстрационный инженер','pin':current},ensure_ascii=False,indent=2));path.chmod(0o600)
    grants=call('GET','/api/admin/access')
    for item in equipment:
        if not any(g['employeeId']==employee['id'] and g['equipmentId']==item['id'] and g['active'] for g in grants):
            call('POST','/api/admin/access',{'employeeId':employee['id'],'equipmentId':item['id'],'includeDescendants':False,'reason':reason})
    marker.write_text(json.dumps({'objects':objects,'department':department['id'],'employee':employee['id'],
                     'equipment':[e['id'] for e in equipment]},indent=2))
    print('Synthetic demo hierarchy, department and engineer equipment grants prepared.',flush=True)


if __name__=='__main__': main()
