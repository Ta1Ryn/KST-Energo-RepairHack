param([ValidateRange(1024,65535)][int]$DatabasePort=5433,[string]$BinariesZip='', [switch]$CheckOnly)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath($PSScriptRoot)
foreach($relative in @('BackendFork\BackendFork.csproj','BackendFork\integration.local.json','database\schema.sql','RepairHack\gradlew.bat','ComputerVision\cv_camera.py')) {
    if(-not(Test-Path -LiteralPath (Join-Path $root $relative) -PathType Leaf)){throw "Project file missing: $relative"}
}
if($CheckOnly){
    & (Join-Path $root 'Setup-Postgres.ps1') -Target $root -CheckOnly
    Write-Host 'Transfer project validation OK. No download or database changes.'
    return
}
if(-not(Get-Command dotnet.exe -ErrorAction SilentlyContinue)){throw 'Install .NET 10 SDK: https://dotnet.microsoft.com/download/dotnet/10.0'}
$sdkVersion=& dotnet --version
if($LASTEXITCODE -ne 0 -or [int]($sdkVersion.Split('.')[0]) -lt 10){throw '.NET 10 SDK or newer is required.'}
& (Join-Path $root 'Setup-Postgres.ps1') -Target $root -Port $DatabasePort -BinariesZip $BinariesZip
Write-Host 'Setup complete. Start the API with Start-Integrated.ps1, then run Bootstrap-Admin.ps1.'
