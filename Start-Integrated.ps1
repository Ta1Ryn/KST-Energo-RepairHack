param([string]$Target=$PSScriptRoot,[ValidateRange(1024,65535)][int]$Port=5080,[switch]$LocalOnly)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($Target).TrimEnd('\')
$project=Join-Path $root 'BackendFork'
if(-not(Test-Path -LiteralPath (Join-Path $project 'integration.local.json'))){throw 'Run Install-Integration.ps1 first.'}
& (Join-Path $root 'Start-Postgres.ps1') -Target $root
$url='http://127.0.0.1:'+$Port
$ready=$false
try{$status=Invoke-RestMethod -Uri ($url+'/api/workforce/status') -TimeoutSec 2;$ready=$status.enabled -eq $true}catch{}
if(-not $ready){
    $listener=Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if($listener){throw "Port $Port is occupied by another server. Stop the old backend or choose -Port."}
    $logs=Join-Path $root '.integration'
    New-Item -ItemType Directory -Path $logs -Force|Out-Null
    $dotnet=(Get-Command dotnet.exe -ErrorAction Stop).Source
    $bind=if($LocalOnly){'127.0.0.1'}else{'0.0.0.0'}
    $process=Start-Process -FilePath $dotnet -ArgumentList @('run','--project',('"'+(Join-Path $project 'BackendFork.csproj')+'"'),'--no-build','--no-launch-profile','--','--urls',('http://'+$bind+':'+$Port)) -WorkingDirectory $project -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs 'api.log') -RedirectStandardError (Join-Path $logs 'api-error.log')
    [IO.File]::WriteAllText((Join-Path $logs 'api.pid'),[string]$process.Id)
    for($i=0;$i -lt 60;$i++){
        Start-Sleep -Milliseconds 500
        try{$status=Invoke-RestMethod -Uri ($url+'/api/workforce/status') -TimeoutSec 1;if($status.enabled){$ready=$true;break}}catch{}
        if($process.HasExited){break}
    }
    if(-not $ready){throw "API did not become ready. Check $logs\api.log and api-error.log."}
}
Write-Host "API ready: $url; PostgreSQL schema v7."
Write-Host 'Android emulator address: http://10.0.2.2:5080'
if(-not $LocalOnly){
    $addresses=Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue|Where-Object{$_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*'}
    foreach($address in $addresses){Write-Host ('Phone, same Wi-Fi: http://'+$address.IPAddress+':'+$Port)}
}
if(-not $status.configured){Write-Host 'Create the first admin locally: powershell -NoProfile -ExecutionPolicy Bypass -File .\Bootstrap-Admin.ps1'}
