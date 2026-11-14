param([string]$Target=$PSScriptRoot)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath($Target).TrimEnd('\')
$runtime=[IO.Path]::GetFullPath((Join-Path $taskRoot '.postgres'))
if(-not $runtime.StartsWith($taskRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Runtime path outside project.'}
. (Join-Path $taskRoot 'Get-PostgresNativePath.ps1')
$runtime=Get-PostgresNativePath $runtime
$control=Join-Path $runtime 'pgsql\bin\pg_ctl.exe'
$data=Join-Path $runtime 'data'
$infoPath=Join-Path $runtime 'credentials.local.json'
if(-not(Test-Path -LiteralPath $control) -or -not(Test-Path -LiteralPath $infoPath)){throw 'Run Setup-Postgres.ps1 first.'}
$info=Get-Content -LiteralPath $infoPath -Raw -Encoding UTF8|ConvertFrom-Json
$statusLog=Join-Path $runtime 'status.log'
$statusError=Join-Path $runtime 'status-error.log'
$status=Start-Process -FilePath $control -ArgumentList @('status','-D',('"'+$data+'"')) -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $statusLog -RedirectStandardError $statusError
if($status.ExitCode -eq 0){Write-Host "PostgreSQL already running on port $($info.Port).";return}
$serverLog=Join-Path $runtime 'server.log'
$process=Start-Process -FilePath $control -ArgumentList @('start','-D',('"'+$data+'"'),'-l',('"'+$serverLog+'"'),'-o',('"-h 127.0.0.1 -p '+[int]$info.Port+'"'),'-w') -WindowStyle Hidden -Wait -PassThru
if($process.ExitCode -ne 0){throw "PostgreSQL startup failed. Check $serverLog"}
Write-Host "PostgreSQL started on 127.0.0.1:$($info.Port)."
