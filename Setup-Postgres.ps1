param(
    [string]$Target = $PSScriptRoot,
    [string]$BinariesZip = '',
    [ValidateRange(1024,65535)][int]$Port = 5433,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath($Target).TrimEnd('\')
function In-Project([string]$Relative) {
    $resolved = [IO.Path]::GetFullPath((Join-Path $taskRoot $Relative))
    if (-not $resolved.StartsWith($taskRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw "Path outside project: $Relative" }
    return $resolved
}
$project = In-Project 'BackendFork\BackendFork.csproj'
$schema = In-Project 'database\schema.sql'
if (-not (Test-Path -LiteralPath $project -PathType Leaf)) { throw "Project not found: $project" }
if (-not (Test-Path -LiteralPath $schema -PathType Leaf)) { throw "SQL schema not found: $schema" }
if ($CheckOnly) { Write-Host 'Setup validation OK. Nothing downloaded or changed.'; return }

$runtimeRoot = In-Project '.postgres'
$bin = In-Project '.postgres\pgsql\bin'
$data = In-Project '.postgres\data'
$credentialsFile = In-Project '.postgres\credentials.local.json'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
. (In-Project 'Get-PostgresNativePath.ps1')
$runtimeRoot = Get-PostgresNativePath $runtimeRoot
$bin = Join-Path $runtimeRoot 'pgsql\bin'
$data = Join-Path $runtimeRoot 'data'
$credentialsFile = Join-Path $runtimeRoot 'credentials.local.json'
$schema = Get-PostgresNativePath $schema
if (-not (Test-Path -LiteralPath (Join-Path $bin 'pg_ctl.exe'))) {
    if (-not $BinariesZip) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Host 'Finding and verifying PostgreSQL Windows binaries...'
        . (In-Project 'Get-PostgresBinaries.ps1')
        $selected = Resolve-PostgresBinaries
        $BinariesZip = In-Project '.postgres\postgresql-binaries.zip'
        Write-Host "Downloading PostgreSQL $($selected.Version)..."
        Invoke-WebRequest $selected.Url -OutFile $BinariesZip -UseBasicParsing -TimeoutSec 900
    }
    $BinariesZip = [IO.Path]::GetFullPath($BinariesZip)
    if (-not (Test-Path -LiteralPath $BinariesZip -PathType Leaf)) { throw "Archive missing: $BinariesZip" }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($BinariesZip)
    try {
        foreach ($entry in $archive.Entries) {
            $destination = [IO.Path]::GetFullPath((Join-Path $runtimeRoot $entry.FullName))
            if (-not $destination.StartsWith($runtimeRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Archive entry outside runtime directory.' }
        }
    } finally { $archive.Dispose() }
    [IO.Compression.ZipFile]::ExtractToDirectory($BinariesZip,$runtimeRoot)
}
foreach ($name in @('pg_ctl.exe','initdb.exe','psql.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $bin $name))) { throw "PostgreSQL binary missing: $name" }
}
function New-LocalPassword {
    $bytes = New-Object byte[] 32
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($bytes) } finally { $random.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-','').ToLowerInvariant()
}
if (Test-Path -LiteralPath $credentialsFile) {
    $credentials = Get-Content -LiteralPath $credentialsFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$credentials.Port -ne $Port) { throw "Existing cluster uses port $($credentials.Port). Pass that port." }
} else {
    $credentials = [PSCustomObject]@{ Port=$Port; AdminPassword=(New-LocalPassword); AppPassword=(New-LocalPassword) }
    [IO.File]::WriteAllText($credentialsFile,($credentials | ConvertTo-Json),[Text.UTF8Encoding]::new($false))
}
if (-not (Test-Path -LiteralPath (Join-Path $data 'PG_VERSION'))) {
    $passwordFile = Join-Path $runtimeRoot 'init-password.tmp'
    [IO.File]::WriteAllText($passwordFile,$credentials.AdminPassword,[Text.UTF8Encoding]::new($false))
    try {
        & (Join-Path $bin 'initdb.exe') '-D' $data '--username=fork_admin' '--encoding=UTF8' '--locale=C' '--auth-host=scram-sha-256' '--auth-local=scram-sha-256' ('--pwfile='+$passwordFile)
        if ($LASTEXITCODE -ne 0) { throw 'initdb failed. Check PostgreSQL output.' }
    } finally { if (Test-Path -LiteralPath $passwordFile) { Remove-Item -LiteralPath $passwordFile } }
}
& (In-Project 'Start-Postgres.ps1') -Target $taskRoot

$previousPassword = [Environment]::GetEnvironmentVariable('PGPASSWORD','Process')
$previousEncoding = [Environment]::GetEnvironmentVariable('PGCLIENTENCODING','Process')
$bootstrapFile = Join-Path $runtimeRoot 'bootstrap.tmp.sql'
try {
    [Environment]::SetEnvironmentVariable('PGPASSWORD',$credentials.AdminPassword,'Process')
    [Environment]::SetEnvironmentVariable('PGCLIENTENCODING','UTF8','Process')
    $bootstrap = @'
DO $role$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='fork_app') THEN
        CREATE ROLE fork_app LOGIN PASSWORD '__APP_PASSWORD__';
    END IF;
END
$role$;
SELECT 'CREATE DATABASE fork OWNER fork_app ENCODING ''UTF8'''
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname='fork')
\gexec
'@
    $bootstrap = $bootstrap.Replace('__APP_PASSWORD__',$credentials.AppPassword.Replace("'","''"))
    [IO.File]::WriteAllText($bootstrapFile,$bootstrap,[Text.UTF8Encoding]::new($false))
    & (Join-Path $bin 'psql.exe') '-X' '-h' '127.0.0.1' '-p' "$Port" '-U' 'fork_admin' '-d' 'postgres' '-v' 'ON_ERROR_STOP=1' '-f' $bootstrapFile
    if ($LASTEXITCODE -ne 0) { throw 'Creating application role/database failed.' }
    [Environment]::SetEnvironmentVariable('PGPASSWORD',$credentials.AppPassword,'Process')
    & (Join-Path $bin 'psql.exe') '-X' '-h' '127.0.0.1' '-p' "$Port" '-U' 'fork_app' '-d' 'fork' '-v' 'ON_ERROR_STOP=1' '-f' $schema
    if ($LASTEXITCODE -ne 0) { throw 'Applying SQL schema failed.' }
} finally {
    [Environment]::SetEnvironmentVariable('PGPASSWORD',$previousPassword,'Process')
    [Environment]::SetEnvironmentVariable('PGCLIENTENCODING',$previousEncoding,'Process')
    if (Test-Path -LiteralPath $bootstrapFile) { Remove-Item -LiteralPath $bootstrapFile }
}

$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
Write-Host 'Restoring Npgsql and its dependencies from NuGet...'
dotnet restore $project '-p:UsePostgreSql=true' --source 'https://api.nuget.org/v3/index.json' --force --nologo
if ($LASTEXITCODE -ne 0) { throw 'Npgsql restore failed. PostgreSQL is installed; repeat setup when NuGet is reachable.' }
dotnet build $project '-p:UsePostgreSql=true' --no-restore --nologo
if ($LASTEXITCODE -ne 0) { throw 'Build with Npgsql failed.' }
$configPath = In-Project 'BackendFork\postgres.local.json'
$config = @{ Database=@{ Provider='PostgreSql'; ConnectionString="Host=127.0.0.1;Port=$Port;Database=fork;Username=fork_app;Password=$($credentials.AppPassword);Timeout=15;Command Timeout=30"; ImportExistingJson=$true } }
[IO.File]::WriteAllText($configPath,($config | ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
Write-Host "PostgreSQL is ready: 127.0.0.1:$Port, database fork, role fork_app."
Write-Host 'Passwords are stored locally under .postgres and are not printed.'
Write-Host 'The API imports existing database.json on its first PostgreSQL startup.'
Write-Host "Run: dotnet run --project '$taskRoot\BackendFork'"
