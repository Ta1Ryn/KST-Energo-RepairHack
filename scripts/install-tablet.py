#!/usr/bin/env python3
"""Install the debug APK on one explicitly connected Android tablet via USB."""
import argparse
from pathlib import Path
import subprocess
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
ADB=ROOT/'.tools/android-sdk/platform-tools/adb'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--serial');args=parser.parse_args()
    devices=subprocess.check_output([str(ADB),'devices'],text=True)
    ready=[row.split()[0] for row in devices.splitlines()[1:] if len(row.split())>=2 and row.split()[1]=='device' and not row.startswith('emulator-')]
    serial=args.serial
    if serial is None:
        if len(ready)!=1:raise RuntimeError('Подключите один планшет и разрешите USB-отладку; при нескольких устройствах укажите --serial.')
        serial=ready[0]
    if serial not in ready:raise RuntimeError('Планшет не авторизован или не подключён.')
    prefix=[str(ADB),'-s',serial]
    sdk=int(subprocess.check_output(prefix+['shell','getprop','ro.build.version.sdk'],text=True).strip())
    if sdk<29:raise RuntimeError('Эта сборка требует Android 10 или новее.')
    with urllib.request.urlopen('http://127.0.0.1:5081/health',timeout=5) as response:
        if response.status!=200:raise RuntimeError('Сначала запустите Launch-Test.sh --presentation-only')
    apk=ROOT/'RepairHack/app/build/outputs/apk/debug/app-debug.apk'
    subprocess.run(prefix+['install','-r',str(apk)],check=True)
    subprocess.run(prefix+['reverse','tcp:5081','tcp:5081'],check=True)
    subprocess.run(prefix+['shell','am','start','-n','rapairhack.ru/.presentation.splashscreen.SplashScreen'],check=True)
    print('Установлено. На экране входа: Адрес сервера → http://127.0.0.1:5081')
    print('Учётные данные находятся в .integration/. USB должен оставаться подключённым.')
    print('После переподключения USB повторите этот скрипт: данные приложения сохраняются.')


if __name__=='__main__':main()
