"""Small driver for reviewing the real Android UI through adb."""
from pathlib import Path
import json
import re
import subprocess
import sys
import time
from xml.etree import ElementTree as E

ROOT=Path(__file__).resolve().parents[1]
ADB=ROOT/'.tools/android-sdk/platform-tools/adb'


def adb(*args):
    return subprocess.check_output([str(ADB),'-s','emulator-5554',*args],timeout=40)


def nodes():
    adb('shell','uiautomator','dump','/sdcard/kst-window.xml')
    xml=adb('exec-out','cat','/sdcard/kst-window.xml')
    (ROOT/'.integration/android-ui.xml').write_bytes(xml)
    return list(E.fromstring(xml).iter('node'))


def find(text=None,resource=None):
    for node in nodes():
        if (text is not None and node.get('text')==text) or (resource is not None and node.get('resource-id','').endswith('/'+resource)):
            return node
    raise RuntimeError('UI element not found: '+str(text or resource))


def tap(node):
    x1,y1,x2,y2=map(int,re.findall(r'\d+',node.get('bounds')))
    adb('shell','input','tap',str((x1+x2)//2),str((y1+y2)//2))


def main():
    command=sys.argv[1] if len(sys.argv)>1 else 'snapshot'
    if command=='login':
        login=json.loads((ROOT/'.integration/demo-engineer-login.json').read_text())
        if len(sys.argv)>2:login['pin']=sys.argv[2]
        buttons=nodes()
        for digit in login['pin']: tap(next(n for n in buttons if n.get('text')==digit))
        time.sleep(2)
    elif command=='tap': tap(find(text=sys.argv[2]))
    elif command=='resource': tap(find(resource=sys.argv[2]))
    elif command=='back': adb('shell','input','keyevent','4')
    xml=nodes()
    print([(n.get('text'),n.get('resource-id'),n.get('bounds')) for n in xml if n.get('text') or n.get('class')=='android.widget.EditText'])
    image=adb('exec-out','screencap','-p')
    (ROOT/('.integration/android-'+command+'.png')).write_bytes(image)


if __name__=='__main__': main()
