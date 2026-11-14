param([string]$Target=$PSScriptRoot)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath($Target).TrimEnd('\')
$runtime=Join-Path $taskRoot '.postgres'
. (Join-Path $taskRoot 'Get-PostgresNativePath.ps1')
$runtime=Get-PostgresNativePath $runtime
$info=Get-Content -LiteralPath (Join-Path $runtime 'credentials.local.json') -Raw -Encoding UTF8|ConvertFrom-Json
$psql=Join-Path $runtime 'pgsql\bin\psql.exe'
& (Join-Path $taskRoot 'Start-Postgres.ps1') -Target $taskRoot
$testDatabase='fork_test_'+[Guid]::NewGuid().ToString('N')
if($testDatabase -notmatch '^fork_test_[a-f0-9]{32}$'){throw 'Invalid test database name.'}
$oldPassword=[Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$oldTestConnection=[Environment]::GetEnvironmentVariable('FORK_TEST_POSTGRES_CONNECTION','Process')
try {
    [Environment]::SetEnvironmentVariable('PGPASSWORD',$info.AdminPassword,'Process')
    & $psql '-X' '-h' '127.0.0.1' '-p' "$($info.Port)" '-U' 'fork_admin' '-d' 'postgres' '-v' 'ON_ERROR_STOP=1' '-c' ("CREATE DATABASE "+$testDatabase+" OWNER fork_app")
    if($LASTEXITCODE -ne 0){throw 'Could not create isolated test database.'}
    $env:DOTNET_CLI_TELEMETRY_OPTOUT='1'
    dotnet build (Join-Path $taskRoot 'BackendFork\BackendFork.csproj') '-p:UsePostgreSql=true' --nologo
    if($LASTEXITCODE -ne 0){throw 'Build failed.'}
    $connection="Host=127.0.0.1;Port=$($info.Port);Database=$testDatabase;Username=fork_app;Password=$($info.AppPassword)"
    [Environment]::SetEnvironmentVariable('FORK_TEST_POSTGRES_CONNECTION',$connection,'Process')
    node (Join-Path $taskRoot 'tests\smoke.mjs')
    if($LASTEXITCODE -ne 0){throw 'PostgreSQL integration checks failed.'}
} finally {
    [Environment]::SetEnvironmentVariable('FORK_TEST_POSTGRES_CONNECTION',$oldTestConnection,'Process')
    [Environment]::SetEnvironmentVariable('PGPASSWORD',$info.AdminPassword,'Process')
    & $psql '-X' '-h' '127.0.0.1' '-p' "$($info.Port)" '-U' 'fork_admin' '-d' 'postgres' '-c' ("DROP DATABASE IF EXISTS "+$testDatabase+" WITH (FORCE)")
    [Environment]::SetEnvironmentVariable('PGPASSWORD',$oldPassword,'Process')
}
