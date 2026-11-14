param([string]$Target=$PSScriptRoot)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath($Target).TrimEnd('\')
$runtime=[IO.Path]::GetFullPath((Join-Path $taskRoot '.postgres'))
if(-not $runtime.StartsWith($taskRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Runtime path outside project.'}
. (Join-Path $taskRoot 'Get-PostgresNativePath.ps1')
$runtime=Get-PostgresNativePath $runtime
$control=Join-Path $runtime 'pgsql\bin\pg_ctl.exe';$data=Join-Path $runtime 'data'
if(-not(Test-Path -LiteralPath $control)){throw 'PostgreSQL runtime missing.'}
$process=Start-Process -FilePath $control -ArgumentList @('stop','-D',('"'+$data+'"'),'-m','fast','-w') -WindowStyle Hidden -Wait -PassThru
if($process.ExitCode -ne 0){throw 'PostgreSQL stop failed or it is not running.'}
