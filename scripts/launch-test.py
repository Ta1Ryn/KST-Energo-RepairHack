#!/usr/bin/env python3
"""Reuse services, launch a KVM accelerated AOSP tablet, and install changed APKs."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'.integration'
SDK=ROOT/'.tools/android-sdk'
ADB=SDK/'platform-tools/adb'
AVD='kst-energo-fast'

def run(*args,**kwargs):return subprocess.run([str(a) for a in args],check=True,**kwargs)
def online(url):
    try:
        with urllib.request.urlopen(url,timeout=2) as r:return r.status==200
    except OSError:return False

def adb(*args,check=True):
    return subprocess.run([str(ADB),'-s','emulator-5554',*args],check=check,capture_output=True,text=True,timeout=45)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--restart-services',action='store_true');parser.add_argument('--upgrade-emulator',action='store_true');parser.add_argument('--restart-preview',action='store_true');parser.add_argument('--presentation-only',action='store_true')
    args=parser.parse_args()
    STATE.mkdir(exist_ok=True)
    apk=ROOT/'RepairHack/app/build/outputs/apk/debug/app-debug.apk'
    if not apk.exists():raise RuntimeError('Build the APK first: ./Build-Android.sh')
    if args.restart_services:
        run('python3',ROOT/'scripts/local-stack.py','stop',cwd=ROOT)
        for _ in range(60):
            if not online('http://127.0.0.1:5081/health') and not online('http://127.0.0.1:8001/health') and not (ROOT/'.postgres/data/postmaster.pid').exists():break
            time.sleep(.5)
        else:raise RuntimeError('Previous API did not stop.')
    if not online('http://127.0.0.1:5081/health'):
        log=(STATE/'native-stack.log').open('a')
        stack=subprocess.Popen(['python3',str(ROOT/'scripts/local-stack.py'),'start'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        for _ in range(300):
            if stack.poll() is not None:raise RuntimeError('Stack startup failed; see .integration/native-stack.log')
            if online('http://127.0.0.1:5081/api/workforce/status') and (STATE/'case-demo.json').exists():break
            time.sleep(1)
        else:raise RuntimeError('API readiness timeout')
    if args.restart_preview:
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():continue
            try:
                argv=(entry/'cmdline').read_bytes().split(b'\0')
                if str(ROOT/'ComputerVision/preview_server.py').encode() in argv and (entry/'cwd').resolve()==ROOT:os.kill(int(entry.name),signal.SIGTERM)
            except (OSError,RuntimeError):pass
        for _ in range(40):
            if not online('http://127.0.0.1:5099/photos'):break
            time.sleep(.25)
    if not online('http://127.0.0.1:5099/photos'):
        log=(STATE/'preview.log').open('a')
        subprocess.Popen([str(ROOT/'ComputerVision/.venv/bin/python'),str(ROOT/'ComputerVision/preview_server.py')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    if args.presentation_only:
        print('Presentation available: http://127.0.0.1:5099/presentation',flush=True);return
    env=dict(os.environ,JAVA_HOME=str(ROOT/'.tools/java'),ANDROID_AVD_HOME=str(ROOT/'.tools/avd'),ANDROID_HOME=str(SDK),QT_QPA_PLATFORM='xcb')
    config=ROOT/f'.tools/avd/{AVD}.avd/config.ini'
    if not config.exists():
        run(SDK/'cmdline-tools/latest/bin/avdmanager','create','avd','--name',AVD,'--package','system-images;android-33;default;x86_64',input='no\n',text=True,env=env)
    settings={'hw.lcd.width':'1200','hw.lcd.height':'1920','hw.lcd.density':'240','hw.ramSize':'2048',
        'skin.name':'1200x1920','skin.path':'1200x1920','hw.initialOrientation':'landscape','hw.gpu.enabled':'yes',
        'hw.gpu.mode':'host','hw.cpu.ncore':'4','fastboot.forceColdBoot':'no','fastboot.forceFastBoot':'yes',
        'firstboot.saveToLocalSnapshot':'yes','hw.keyboard':'yes','hw.camera.back':'emulated','hw.camera.front':'none'}
    lines=[s for s in config.read_text().splitlines() if s.split('=',1)[0].strip() not in settings]
    config.write_text('\n'.join(lines+[k+'='+v for k,v in settings.items()])+'\n')
    existing=adb('emu','avd','name',check=False)
    name=existing.stdout.splitlines()[0] if existing.returncode==0 and existing.stdout else ''
    if args.upgrade_emulator and name=='kst-energo-test':
        adb('emu','kill')
        for _ in range(40):
            if adb('get-state',check=False).returncode!=0:break
            time.sleep(.5)
        name=''
    if name and name not in (AVD,'kst-energo-test'):raise RuntimeError('Port 5554 is occupied by another emulator.')
    emulator=None;started=time.monotonic()
    if not name:
        acceleration=run(SDK/'emulator/emulator','-accel-check',env=env,capture_output=True,text=True)
        print(acceleration.stdout.strip(),flush=True)
        log=(STATE/'native-emulator.log').open('a')
        camera='imagefile:'+str(STATE/'test-nameplate.png')
        emulator=subprocess.Popen([str(SDK/'emulator/emulator'),'-avd',AVD,'-accel','on','-gpu','host',
            '-no-audio','-no-boot-anim','-memory','2048','-cores','4','-camera-back',camera,'-camera-front','none','-port','5554'],env=env,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    for _ in range(180):
        if emulator and emulator.poll() is not None:raise RuntimeError('Emulator exited; see .integration/native-emulator.log')
        if adb('shell','getprop','sys.boot_completed',check=False).stdout.strip()=='1':break
        time.sleep(1)
    else:raise RuntimeError('Emulator boot timeout')
    digest=hashlib.sha256(apk.read_bytes()).hexdigest()
    record=STATE/'installed-apk.json'
    current=json.loads(record.read_text()) if record.exists() else {}
    package=adb('shell','pm','path','rapairhack.ru',check=False).stdout
    if current.get('sha256')!=digest or current.get('avd')!=(name or AVD) or 'package:' not in package:
        print(adb('install','-r',apk).stdout.strip(),flush=True)
        record.write_text(json.dumps({'sha256':digest,'avd':name or AVD}))
    for namespace,key,value in [('global','window_animation_scale','0'),('global','transition_animation_scale','0'),
        ('global','animator_duration_scale','0'),('global','device_provisioned','1'),('secure','user_setup_complete','1'),
        ('system','accelerometer_rotation','0'),('system','user_rotation','1')]:adb('shell','settings','put',namespace,key,value)
    adb('shell','wm','size','1200x1920');adb('shell','wm','density','240')
    adb('shell','pm','grant','rapairhack.ru','android.permission.CAMERA')
    if name=='kst-energo-test':
        for package in ['com.google.android.gms','com.google.android.setupwizard','com.google.android.googlequicksearchbox']:
            adb('shell','pm','disable-user','--user','0',package,check=False)
    adb('shell','am','start','-n','rapairhack.ru/.presentation.splashscreen.SplashScreen')
    print(f'Tablet ready in {time.monotonic()-started:.1f}s; 1920×1200 landscape. Accounts: .integration/demo-login.json and .integration/demo-engineer-login.json',flush=True)
    print('CV: http://127.0.0.1:5099/ · Presentation: http://127.0.0.1:5099/presentation',flush=True)
    if emulator:emulator.wait()

if __name__=='__main__':main()
