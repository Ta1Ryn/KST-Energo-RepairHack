"""Interactive synthetic temperature, pressure and vibration over the real telemetry API."""
from importlib import import_module
import argparse
import datetime as dt
import json
import math
import select
import sys
import time

case = import_module('case-demo')
ROOT = case.ROOT
KEYS = ['bearing', 'winding', 'vibration1x', 'vibration2x', 'hf', 'rpm', 'rms', 'pressure']
PRESETS = {'normal': [50, 65, 1.4, .8, 1, 1480, 2, 3],
           'warning': [80, 95, 3.2, 2.2, 2.7, 1480, 3.5, 8],
           'alarm': [96, 118, 5.2, 3.4, 3.2, 1480, 6, 12]}
NORMAL = dict(zip(KEYS, PRESETS['normal']))
HELP = '''Команды:
  normal / норма          вернуть все показатели в норму
  heat [20] / cool [20]   нагреть / охладить только температуры
  pressure 8 [5]          давление до 8 бар за 5 секунд
  vibration 6 [5]         вибрация СКЗ до 6 мм/с за 5 секунд
  warning / alarm        все показатели: предупреждение / тревога
  set bearing=80         задать отдельный датчик (pressure, winding, rms…)
  status                 текущие значения и статусы
  replay [30]            строки температуры и вибрации из исходного XLSX
  exit                   остановить симулятор
'''


class Simulator:
    def __init__(self, verbose=True):
        self.api = case.Client(json.loads((ROOT/'.integration/demo-login.json').read_text())['pin'], timeout=10)
        self.eid = json.loads((ROOT/'.integration/case-demo.json').read_text())['equipmentId']
        self.verbose = verbose
        card = self.card()
        if not any(s['key'] == 'pressure' for c in card['components'] for s in c['sensors']):
            import_module('ensure-test-pressure').ensure(self.api, self.eid)

    def card(self):
        return self.api.call('GET', '/api/equipment/'+self.eid)

    def snapshot(self, card=None):
        card = card if card is not None else self.card()
        values = dict(NORMAL)
        for c in card['components']:
            for s in c['sensors']:
                if s['key'] in KEYS and s.get('value') is not None:
                    values[s['key']] = float(s['value'])
        return values

    def describe(self, card):
        return '\n'.join(' | '.join(f'{s["name"]}: {s.get("value", "—")} {s["unit"]} [{s["status"]}]'
            for s in c['sensors']) for c in card['components'])

    def send(self, values, label='', verbose=None):
        if not values or any(k not in KEYS or not isinstance(v, (int, float)) or not math.isfinite(v) for k, v in values.items()):
            raise ValueError('Нужны конечные числовые значения. Ключи: '+', '.join(KEYS))
        verbose = self.verbose if verbose is None else verbose
        card = self.card()
        now = dt.datetime.now(dt.timezone.utc)
        readings = [s['measuredAt'] for c in card['components'] for s in c['sensors'] if s.get('measuredAt')]
        if readings:
            latest = max(dt.datetime.fromisoformat(s.replace('Z', '+00:00')) for s in readings)
            now = max(now, latest+dt.timedelta(milliseconds=1))
        if now > dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=30):
            raise RuntimeError('Текущее показание имеет будущую дату')
        for c in card['components']:
            if not any(s['key'] in values for s in c['sensors']):
                continue
            result = self.api.call('POST', f'/api/equipment/{self.eid}/components/{c["id"]}/telemetry', {
                'installationId': c['current']['id'], 'measuredAt': now.isoformat(),
                'sensors': [{'key': s['key'], 'unit': s['unit'], 'value': values[s['key']]} for s in c['sensors'] if s['key'] in values]})
            if verbose:
                print(self.describe({'components': [result]}))
                for r in result.get('recommendations', []):
                    print('  ! '+r['message'])
        if label and verbose:
            print(label)

    def ramp(self, target, seconds=20):
        target = dict(zip(KEYS, target)) if isinstance(target, (list, tuple)) else dict(target)
        if type(seconds) is not int or not 1 <= seconds <= 300:
            raise ValueError('Продолжительность от 1 до 300 секунд')
        if any(k not in KEYS or not math.isfinite(v) for k, v in target.items()):
            raise ValueError('Некорректная цель изменения')
        start = self.snapshot()
        for i in range(1, seconds+1):
            self.send({k: round(start[k]+(value-start[k])*i/seconds, 3) for k, value in target.items()})
            if i < seconds:
                time.sleep(1)

    def heat(self, seconds=20):
        self.ramp({'bearing': 96, 'winding': 118}, seconds)

    def cool(self, seconds=20):
        self.ramp({'bearing': NORMAL['bearing'], 'winding': NORMAL['winding']}, seconds)

    def pressure(self, value=12, seconds=5):
        self.ramp({'pressure': float(value)}, seconds)

    def vibration(self, value=6, seconds=5):
        value = float(value)
        if not 0 <= value <= 30:
            raise ValueError('Тестовая вибрация от 0 до 30 мм/с')
        alarm = dict(zip(KEYS, PRESETS['alarm']))
        self.ramp({k: round(alarm[k]*value/6, 3) for k in ('rms', 'vibration1x', 'vibration2x', 'hf')}, seconds)

    def replay(self, count=30, delay=1):
        import openpyxl
        if not 1 <= count <= 1000:
            raise ValueError('От 1 до 1000 строк')
        def rows(part):
            path = next((ROOT/'CaseFiles'/part).glob('*.xlsx'))
            book = openpyxl.load_workbook(path, read_only=True, data_only=True)
            data = list(book.active.values)[1:]
            book.close()
            return data
        for t, v in list(zip(rows('Температура'), rows('Вибрация')))[:count]:
            self.send(dict(zip(KEYS, [t[2], t[3], v[3], v[4], v[5], t[1], v[2]])), f'Строка кейса: {t[0]} · {t[4]} · {v[6]}')
            time.sleep(delay)


def command(sim, parts):
    name = parts[0].lower()
    if name in ('exit', 'quit', 'выход'):
        return False
    if name in ('normal', 'норма'):
        sim.send(NORMAL)
    elif name in PRESETS:
        sim.send(dict(zip(KEYS, PRESETS[name])))
    elif name in ('heat', 'cool'):
        getattr(sim, name)(int(parts[1]) if len(parts) > 1 else 20)
    elif name in ('pressure', 'давление', 'vibration', 'вибрация'):
        method = sim.pressure if name in ('pressure', 'давление') else sim.vibration
        method(float(parts[1]) if len(parts) > 1 else (12 if method == sim.pressure else 6), int(parts[2]) if len(parts) > 2 else 5)
    elif name == 'set':
        values = {k: float(v) for k, v in (pair.split('=', 1) for pair in parts[1:])}
        sim.send(values)
    elif name == 'replay':
        sim.replay(int(parts[1]) if len(parts) > 1 else 30)
    elif name in ('status', 'статус'):
        print(sim.describe(sim.card()))
    else:
        print(HELP)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preset', choices=PRESETS)
    parser.add_argument('--replay', type=int)
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    sim = Simulator()
    if args.status:
        print(sim.describe(sim.card())); return
    if args.preset:
        sim.send(dict(zip(KEYS, PRESETS[args.preset]))); return
    if args.replay:
        sim.replay(args.replay); return
    print('КСТ Энерго · симулятор датчиков\nТестовые показания поступают в приложение и графики каждые 2 секунды.\n'
          'Давление: условные тестовые пороги >7 / >10 бар.\n'+HELP)
    sim.send(sim.snapshot())
    next_tick = time.monotonic()+2
    print('датчики> ', end='', flush=True)
    while True:
        try:
            ready, _, _ = select.select([sys.stdin], [], [], max(0, next_tick-time.monotonic()))
            if ready:
                line = sys.stdin.readline()
                if not line:
                    break
                parts = line.strip().split()
                if parts and not command(sim, parts):
                    break
                print('датчики> ', end='', flush=True)
                next_tick = time.monotonic()+2
            else:
                sim.send(sim.snapshot(), verbose=False)
                next_tick = time.monotonic()+2
        except KeyboardInterrupt:
            print('\nИзменение остановлено.\nдатчики> ', end='', flush=True)
            next_tick = time.monotonic()+2
        except (ValueError, RuntimeError, OSError) as error:
            print('\nОшибка: '+str(error)+'\nдатчики> ', end='', flush=True)
            next_tick = time.monotonic()+5


if __name__ == '__main__':
    main()
