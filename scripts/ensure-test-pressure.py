#!/usr/bin/env python3
"""Add one explicitly synthetic pressure channel to the isolated case demo."""
from importlib import import_module
import json

case = import_module('case-demo')


def ensure(api, eid):
    config = json.loads((case.ROOT/'BackendFork/postgres.local.json').read_text())
    if 'Database=fork_demo;' not in config['Database']['ConnectionString']:
        raise RuntimeError('Тестовое давление можно добавлять только в fork_demo')
    card = api.call('GET', '/api/equipment/'+eid)
    if card['model'] != case.MODEL or card['serialNumber'] != case.SERIAL:
        raise RuntimeError('Шильдик не совпадает с тестовым насосом')
    existing = next((c for c in card['components'] if c['type'] == 'test-pump-pressure'), None)
    norms = api.call('GET', '/api/standards/sensor-norms')
    norm = next((n for n in norms if n.get('equipmentModel') == case.MODEL and n['sensorKind'] == 'pressure' and n.get('isDemo')), None)
    if norm is None:
        norm = api.call('POST', '/api/standards/sensor-norms', dict(
            name='Давление · тестовая норма, не из исходного кейса', sensorKind='pressure', unit='бар',
            min=0, max=10, warningMax=7, equipmentModel=case.MODEL, componentType='test-pump-pressure', isDemo=True))
    definition = dict(name='Корпус насоса · тест давления', type='test-pump-pressure',
        installedAt='2026-01-01T00:00:00Z', sensors=[dict(key='pressure', kind='pressure', name='Давление насоса · тест', unit='бар', normId=norm['id'])])
    component = api.call('POST', '/api/equipment/'+eid+'/components', definition) if existing is None else existing
    if existing is not None and not any(s['key'] == 'pressure' for s in existing['sensors']):
        component = api.call('PUT', '/api/equipment/'+eid+'/components/'+existing['id']+'/definition', definition)
    message = 'Тестовое давление вышло за демонстрационный порог. Для реального насоса нужны его паспортные нормы.'
    instructions = api.call('GET', '/api/instructions')
    title = 'Давление · тестовая инструкция симулятора'
    instruction = next((i for i in instructions if i['title'] == title), None)
    if instruction is None:
        instruction = api.call('POST', '/api/instructions', dict(title=title, text=message, isDemo=True))
    for trigger in ('sensorAttention', 'sensorOutOfRange'):
        rules = api.call('GET', '/api/recommendation-rules')
        if not any(r.get('equipmentModel') == case.MODEL and r.get('sensorKind') == 'pressure' and r['trigger'] == trigger for r in rules):
            api.call('POST', '/api/recommendation-rules', dict(trigger=trigger, equipmentModel=case.MODEL, sensorKind='pressure',
                message=message, instructionId=instruction['id']))
    passport = card.get('passport') or {}
    params = dict(passport.get('parameters') or {})
    params['Давление · тестовые границы'] = 'Предупреждение >7 бар, тревога >10 бар. Условная норма симулятора; в исходном кейсе не задана.'
    passport['parameters'] = params
    api.call('PUT', '/api/equipment/'+eid+'/passport', dict(nameplate=card.get('nameplate') or {}, passport=passport))
    return component


def main():
    api = case.Client(json.loads((case.STATE/'demo-login.json').read_text())['pin'])
    eid = json.loads((case.STATE/'case-demo.json').read_text())['equipmentId']
    component = ensure(api, eid)
    print('Тестовое давление подключено:', component['id'])


if __name__ == '__main__':
    main()
