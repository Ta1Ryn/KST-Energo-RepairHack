#!/usr/bin/env python3
"""Activate the prepared API/CV update while retaining the existing database."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.integration'


def process(pid):
    directory = Path('/proc') / str(pid)
    args = [os.fsdecode(part) for part in (directory / 'cmdline').read_bytes().split(b'\0') if part]
    cwd = (directory / 'cwd').resolve(strict=True)
    stat = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
    return args, cwd, stat[19]


def alive(pid, stamp):
    try:
        directory = Path('/proc') / str(pid)
        stat = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
        return stat[19] == stamp and stat[0] not in ('Z', 'X')
    except FileNotFoundError:
        return False


def stop_child(pid, stamp):
    if not alive(pid, stamp):
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 20
    while alive(pid, stamp) and time.monotonic() < deadline:
        time.sleep(.2)
    if alive(pid, stamp):
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while alive(pid, stamp) and time.monotonic() < deadline:
            time.sleep(.2)
    if alive(pid, stamp):
        raise RuntimeError('The previous service did not stop.')


def get(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


def main():
    report_path = STATE / 'segmentation-update.json'
    report = json.loads(report_path.read_text())
    state = json.loads((STATE / 'stack.json').read_text())
    supervisor = int(state['supervisor'])
    args, cwd, supervisor_stamp = process(supervisor)
    expected_script = str(ROOT / 'scripts/local-stack.py')
    if expected_script not in args or 'start' not in args or cwd != ROOT:
        raise RuntimeError('The expected original supervisor is not running.')
    children = []
    for pid in state['children']:
        child_args, child_cwd, stamp = process(int(pid))
        if child_cwd == ROOT / 'ComputerVision' and 'http_service:app' in child_args:
            children.append((int(pid), stamp))
        elif child_cwd == ROOT / 'BackendFork' and str(ROOT / 'BackendFork/bin/Debug/net10.0/BackendFork.dll') in child_args:
            children.append((int(pid), stamp))
        else:
            raise RuntimeError('An unexpected child process was found; nothing was stopped.')
    if len(children) != 2:
        raise RuntimeError('Exactly the existing CV and API must be present.')
    for key in ('apk', 'backend'):
        artifact = Path(report[key]['path'])
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != report[key]['sha256']:
            raise RuntimeError('Prepared artifact checksum mismatch.')
    selection_path = ROOT / 'ComputerVision/model-selection.local.json'
    pending_path = STATE / 'segmentation-liquid-selection.pending.json'
    pending = json.loads(pending_path.read_text())
    catalog = json.loads((ROOT / 'ComputerVision/models/catalog.json').read_text())
    if pending['models'] != ['oil', 'oil_trained', 'metal', 'case_pilot', 'crack'] or pending['threshold'] != .25:
        raise RuntimeError('Unexpected pending model selection.')
    if not set(pending['models']).issubset({entry['id'] for entry in catalog}):
        raise RuntimeError('A requested model is unavailable.')
    pg_path = ROOT / '.postgres/data/postmaster.pid'
    pg_pid = int(pg_path.read_text().splitlines()[0])
    _, _, pg_stamp = process(pg_pid)
    # Preserve inherited runtime configuration in memory; never write credentials.
    environment = {}
    for pair in (Path('/proc') / str(supervisor) / 'environ').read_bytes().split(b'\0'):
        if b'=' in pair:
            key, value = pair.split(b'=', 1)
            environment[os.fsdecode(key)] = os.fsdecode(value)
    executable = (Path('/proc') / str(supervisor) / 'exe').resolve(strict=True)
    (STATE / 'model-selection.before-contours.json').write_bytes(selection_path.read_bytes())
    (STATE / 'stack.before-contours.json').write_text(json.dumps(state, indent=2) + '\n')
    if not alive(supervisor, supervisor_stamp):
        raise RuntimeError('The supervisor changed before reload.')
    # SIGTERM runs the old supervisor's database cleanup. Handoff it directly,
    # then gracefully stop just its two verified service children.
    os.kill(supervisor, signal.SIGKILL)
    for pid, stamp in reversed(children):
        stop_child(pid, stamp)
    temporary = selection_path.with_suffix('.contours.tmp')
    temporary.write_text(json.dumps(pending, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(selection_path)
    with (STATE / 'segmentation-reload.log').open('a') as log:
        new_supervisor = subprocess.Popen([str(executable), expected_script, 'resume'], cwd=ROOT,
            env=environment, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    print('CV/API reload started; database retained.', flush=True)
    deadline = time.monotonic() + 330
    while time.monotonic() < deadline:
        if new_supervisor.poll() is not None:
            raise RuntimeError('CV/API reload failed; see .integration/segmentation-reload.log.')
        try:
            cv_health = get('http://127.0.0.1:8001/health')
            get('http://127.0.0.1:5081/api/workforce/status')
            new_state = json.loads((STATE / 'stack.json').read_text())
            if new_state['supervisor'] == new_supervisor.pid:
                if cv_health['activeModels'] != pending['models'] or cv_health.get('geometry') != 'segmentation-when-available':
                    raise RuntimeError('The requested segmentation profile was not activated.')
                if int(pg_path.read_text().splitlines()[0]) != pg_pid or not alive(pg_pid, pg_stamp):
                    raise RuntimeError('The database changed during the service reload.')
                report['activation'] = {'status': 'active', 'activatedAt': datetime.now(timezone.utc).isoformat(),
                    'supervisor': new_supervisor.pid, 'children': new_state['children'],
                    'databasePidUnchanged': True, 'cvHealth': cv_health}
                report['status'] = 'services_active_apk_pending'
                report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
                print(json.dumps({'status': 'services_active', 'activeModels': cv_health['activeModels'],
                    'modelVersion': cv_health['modelVersion'], 'databasePidUnchanged': True}), flush=True)
                return
        except (OSError, ValueError):
            pass
        time.sleep(.5)
    raise RuntimeError('Reload readiness timed out; the database remains running.')


if __name__ == '__main__':
    main()
