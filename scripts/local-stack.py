#!/usr/bin/env python3
"""Local Linux supervisor: an isolated demo database, existing API and real CV."""
from pathlib import Path
import argparse
import getpass
import json
import os
import secrets
import shutil
import signal
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.integration'
PG = ROOT / '.tools/postgresql/usr/bin'
DATA = ROOT / '.postgres/data'
SOCKET = ROOT / '.postgres/socket'
PGPORT = 5433
API = 'http://127.0.0.1:5081'
CV = 'http://127.0.0.1:8001'


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def pg_bin(name):
    return str(PG / name) if (PG / name).exists() else shutil.which(name)


def request(url, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url,data=data,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=3) as response:
        return json.load(response)


def ready(url, process=None, seconds=180):
    until = time.monotonic()+seconds
    while time.monotonic()<until:
        if process and process.poll() is not None:
            raise RuntimeError('Service exited. See .integration logs.')
        try:
            return request(url)
        except (OSError,ValueError):
            time.sleep(.5)
    raise RuntimeError('Readiness timeout: '+url+'; see .integration logs.')


def database():
    if not pg_bin('initdb'):
        raise RuntimeError('PostgreSQL server tools missing; run Setup-Project.sh.')
    SOCKET.mkdir(parents=True,mode=0o700,exist_ok=True)
    SOCKET.chmod(0o700)
    if not (DATA/'PG_VERSION').exists():
        run(pg_bin('initdb'),'-D',DATA,'-U',getpass.getuser(),'--auth-local=peer',
            '--auth-host=scram-sha-256','--encoding=UTF8','--no-locale',stdout=subprocess.DEVNULL)
    running = subprocess.run([pg_bin('pg_ctl'),'-D',str(DATA),'status'],capture_output=True).returncode==0
    if not running:
        run(pg_bin('pg_ctl'),'-D',DATA,'-l',STATE/'postgres.log','-w','start',
            '-o',f"-k {SOCKET} -h '' -p {PGPORT}")
    common = ['-h',str(SOCKET),'-p',str(PGPORT),'-U',getpass.getuser()]
    exists = subprocess.check_output(['psql',*common,'-d','postgres','-AtX','-c',
        "SELECT 1 FROM pg_database WHERE datname='fork_demo'"],text=True).strip()
    if not exists:
        run('createdb',*common,'fork_demo')
    connection=f'Host={SOCKET};Port={PGPORT};Database=fork_demo;Username={getpass.getuser()};Pooling=true'
    font=subprocess.check_output(['fc-match','-f','%{file}','sans'],text=True).strip()
    config={'Database':{'Provider':'PostgreSql','ConnectionString':connection,'ImportExistingJson':True},
            'Reports':{'FontPath':font},'Ai':{'BaseUrl':CV,'TimeoutSeconds':120}}
    path=ROOT/'BackendFork/postgres.local.json'
    path.write_text(json.dumps(config,ensure_ascii=False,indent=2))
    path.chmod(0o600)
    return connection


def setup():
    if not pg_bin('initdb'):
        if not shutil.which('pacman'):
            raise RuntimeError('Install PostgreSQL server tools for your distribution and retry.')
        url=subprocess.check_output(['pacman','-Sp','--print-format','%l','postgresql'],text=True).strip().splitlines()[-1]
        archive=ROOT/'.tools/postgresql.pkg.tar.zst'
        archive.parent.mkdir(parents=True,exist_ok=True)
        run('curl','-fsSL',url,'-o',archive)
        PG.parents[1].mkdir(parents=True,exist_ok=True)
        run('tar','-xf',archive,'-C',PG.parents[1])
    database()
    venv=ROOT/'ComputerVision/.venv'
    if not (venv/'bin/python').exists():
        run('uv','venv','--python','3.12',venv)
    run('uv','pip','install','--python',venv/'bin/python','--index','https://download.pytorch.org/whl/cpu',
        '--index-strategy','unsafe-best-match','torch','torchvision')
    run('uv','pip','install','--python',venv/'bin/python','-r',ROOT/'ComputerVision/requirements.http.txt')
    run('dotnet','build',ROOT/'BackendFork/BackendFork.csproj','-p:UsePostgreSql=true','--nologo')
    print('Setup complete. Start with ./Start-Integrated.sh',flush=True)


def start():
    (STATE/'stop.request').unlink(missing_ok=True)
    database()
    children=[]
    logs=[]
    def cleanup():
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
        for log in logs: log.close()
        (STATE/'stack.json').unlink(missing_ok=True)
        if (DATA/'postmaster.pid').exists():
            run(pg_bin('pg_ctl'),'-D',DATA,'-m','fast','-w','stop')
    def interrupt(signum,frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupt)
    signal.signal(signal.SIGINT,interrupt)
    try:
        for name,cmd,cwd,url in [
            ('cv',[ROOT/'ComputerVision/.venv/bin/python','-m','uvicorn','http_service:app','--host','127.0.0.1','--port','8001'],ROOT/'ComputerVision',CV+'/health'),
            ('api',['dotnet',ROOT/'BackendFork/bin/Debug/net10.0/BackendFork.dll','--urls','http://0.0.0.0:5081'],ROOT/'BackendFork',API+'/api/workforce/status')]:
            log=(STATE/f'{name}.log').open('a');logs.append(log)
            process=subprocess.Popen([str(a) for a in cmd],cwd=cwd,stdout=log,stderr=subprocess.STDOUT)
            children.append(process)
            ready(url,process,300)
            print(name.upper()+' ready: '+url,flush=True)
        status=request(API+'/api/workforce/status')
        account_file=STATE/'demo-login.json'
        if not status['configured']:
            account={'employeeNumber':'DEMO-ADMIN','fullName':'Тестовый администратор','pin':f'{secrets.randbelow(10000):04d}'}
            request(API+'/api/workforce/bootstrap',account)
            account_file.write_text(json.dumps(account,ensure_ascii=False,indent=2));account_file.chmod(0o600)
        run('python3',ROOT/'scripts/demo-fixtures.py')
        run(ROOT/'ComputerVision/.venv/bin/python',ROOT/'scripts/case-demo.py')
        state={'supervisor':os.getpid(),'children':[p.pid for p in children],'api':API,'cv':CV}
        (STATE/'stack.json').write_text(json.dumps(state))
        print('Ready. Android emulator: http://10.0.2.2:5081; demo login: .integration/demo-login.json',flush=True)
        while all(p.poll() is None for p in children):
            if (STATE/'stop.request').exists(): return
            time.sleep(1)
        raise RuntimeError('A service stopped. See .integration logs.')
    finally:
        cleanup()


def resume():
    """Replace API/CV supervision without reseeding or restarting PostgreSQL."""
    backend=STATE/'segmentation-backend/BackendFork.dll'
    if not backend.is_file() or not (DATA/'postmaster.pid').is_file():
        raise RuntimeError('Prepared backend and running database are required.')
    (STATE/'stop.request').unlink(missing_ok=True)
    children=[]
    logs=[]
    stop_requested=False
    def interrupt(signum,frame):
        nonlocal stop_requested
        stop_requested=True
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupt)
    signal.signal(signal.SIGINT,interrupt)
    try:
        for name,cmd,cwd,url in [
            ('cv',[ROOT/'ComputerVision/.venv/bin/python','-m','uvicorn','http_service:app','--host','127.0.0.1','--port','8001'],ROOT/'ComputerVision',CV+'/health'),
            ('api',['dotnet',backend,'--urls','http://0.0.0.0:5081'],ROOT/'BackendFork',API+'/api/workforce/status')]:
            log=(STATE/f'{name}.log').open('a');logs.append(log)
            process=subprocess.Popen([str(a) for a in cmd],cwd=cwd,stdout=log,stderr=subprocess.STDOUT)
            children.append(process)
            ready(url,process,300)
            print(name.upper()+' ready: '+url,flush=True)
        state={'supervisor':os.getpid(),'children':[p.pid for p in children],'api':API,'cv':CV,'backend':str(backend)}
        temporary=STATE/'stack.resume.tmp'
        temporary.write_text(json.dumps(state));temporary.replace(STATE/'stack.json')
        while all(p.poll() is None for p in children):
            if (STATE/'stop.request').exists():
                stop_requested=True
                return
            time.sleep(1)
        raise RuntimeError('A service stopped. See .integration logs.')
    finally:
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired: process.kill();process.wait()
        for log in logs: log.close()
        (STATE/'stack.json').unlink(missing_ok=True)
        # A failed API/CV reload leaves the existing database available.
        if stop_requested and (DATA/'postmaster.pid').exists():
            run(pg_bin('pg_ctl'),'-D',DATA,'-m','fast','-w','stop')


def main():
    STATE.mkdir(mode=0o700,exist_ok=True)
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['setup','start','resume','stop','status'])
    command=parser.parse_args().command
    if command=='setup': setup()
    elif command=='start': start()
    elif command=='resume': resume()
    elif command=='stop':
        state=STATE/'stack.json'
        (STATE/'stop.request').touch()
        # Verify the project and command before signalling. PID namespaces used
        # by development tools can make a saved PID refer to another process.
        found=False
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit(): continue
            try:
                args=(entry/'cmdline').read_bytes().split(b'\0')
                if not any(mode in args for mode in (b'start',b'resume')) or not any(arg.endswith(b'local-stack.py') for arg in args): continue
                if (entry/'cwd').resolve()!=ROOT: continue
                os.kill(int(entry.name),signal.SIGTERM);found=True
            except (OSError,RuntimeError): pass
        if not found and not state.exists() and (DATA/'postmaster.pid').exists():
            run(pg_bin('pg_ctl'),'-D',DATA,'-m','fast','-w','stop')
    else:
        print(json.dumps({'api':request(API+'/api/workforce/status'),'cv':request(CV+'/health')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: pass
