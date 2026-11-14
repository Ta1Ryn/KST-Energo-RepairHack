#!/usr/bin/env python3
"""Package sources, case data, pinned models and a consistent PostgreSQL snapshot."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SKIP_DIRS={'.git','.tools','.postgres','.venv','.venv-train','__pycache__','.gradle','.kotlin','node_modules'}
SKIP_PREFIXES=('BackendFork/bin/','BackendFork/obj/','.integration/model-research/',
               '.integration/test-','ComputerVision/datasets/case-training-ac35f818/')
SKIP_FILES={'BackendFork/postgres.local.json','RepairHack/local.properties',
            '.integration/stack.json','.integration/installed-apk.json','transfer/archive-manifest.json'}
APKS={'RepairHack/app/build/outputs/apk/debug/app-debug.apk',
      'RepairHack/app/build/outputs/apk/debug/output-metadata.json'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);args=parser.parse_args()
    destination=args.output.resolve()
    if destination.is_relative_to(ROOT):raise ValueError('Place archive outside the project')
    if destination.exists():raise FileExistsError(destination)
    dump=ROOT/'transfer/fork_demo.dump'
    temporary=dump.with_suffix('.dump.tmp')
    subprocess.run(['pg_dump','-h',str(ROOT/'.postgres/socket'),'-p','5433','-U',os.environ['USER'],
                    '-d','fork_demo','-Fc','--no-owner','--no-acl','-f',str(temporary)],check=True)
    temporary.replace(dump)
    frozen=subprocess.check_output(['uv','pip','freeze','--python',str(ROOT/'ComputerVision/.venv/bin/python')])
    (ROOT/'transfer/python-packages-linux.txt').write_bytes(frozen)
    paths=[]
    for directory,dirs,files in os.walk(ROOT):
        dirs[:]=sorted(d for d in dirs if d not in SKIP_DIRS and not (Path(directory)/d).is_symlink())
        for name in sorted(files):
            path=Path(directory)/name;relative=path.relative_to(ROOT).as_posix()
            if path.is_symlink() or relative in SKIP_FILES or relative.startswith(SKIP_PREFIXES):continue
            if path.suffix in {'.log','.tmp','.pyc','.download','.cache'}:continue
            if relative.startswith('RepairHack/app/build/') and relative not in APKS:continue
            if relative.startswith('ComputerVision/runs/') and name.startswith('epoch') and path.suffix=='.pt':continue
            paths.append(path)
    print(f'Packaging {len(paths)} files, {sum(p.stat().st_size for p in paths)/1024**2:.1f} MiB before compression',flush=True)
    manifest=dict(createdUtc=datetime.now(timezone.utc).isoformat(),root='fork',sourceProject=str(ROOT),
                  database='PostgreSQL pg_dump custom; consistent snapshot; no owner/ACL',
                  excludedDirectories=sorted(SKIP_DIRS),excludedPrefixes=SKIP_PREFIXES,
                  excludedFiles=sorted(SKIP_FILES),exceptions=sorted(APKS),
                  note='Also excludes logs, temporary/cache files and intermediate epoch checkpoints. Build tools/environments must be installed on the destination.',files=[])
    pending=destination.with_suffix('.zip.partial')
    with zipfile.ZipFile(pending,'x',zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
        for path in paths:
            relative=path.relative_to(ROOT).as_posix();data=path.read_bytes()
            manifest['files'].append(dict(path=relative,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
            info=zipfile.ZipInfo.from_file(path,'fork/'+relative);info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,data,compresslevel=3)
        body=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
        archive.writestr('fork/transfer/archive-manifest.json',body)
    with zipfile.ZipFile(pending) as archive:
        bad=archive.testzip()
        if bad:raise ValueError('ZIP CRC failed: '+bad)
    pending.replace(destination)
    (ROOT/'transfer/archive-manifest.json').write_bytes(body)
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(digest+'  '+destination.name+'\n')
    print(json.dumps(dict(archive=str(destination),files=len(paths)+1,MiB=round(destination.stat().st_size/1024**2,1),sha256=digest),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
