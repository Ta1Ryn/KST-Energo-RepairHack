param([string]$Weights='',[ValidateRange(1024,65535)][int]$Port=8001)
$ErrorActionPreference='Stop'
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if(-not(Test-Path -LiteralPath $python)){throw 'Run Setup-Yolo.ps1 first.'}
if(-not $Weights -and -not(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'model.local.json'))){throw 'Train a defect model first. No trained model is configured.'}
$old=[Environment]::GetEnvironmentVariable('FORK_YOLO_WEIGHTS','Process')
Push-Location -LiteralPath $PSScriptRoot
try{
    if($Weights){$env:FORK_YOLO_WEIGHTS=[IO.Path]::GetFullPath($Weights)}
    & $python -m uvicorn serve:app --host 127.0.0.1 --port $Port --workers 1
    if($LASTEXITCODE -ne 0){throw 'YOLO service failed.'}
}finally{Pop-Location;[Environment]::SetEnvironmentVariable('FORK_YOLO_WEIGHTS',$old,'Process')}
