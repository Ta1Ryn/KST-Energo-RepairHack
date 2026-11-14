 $ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
dotnet build (Join-Path $PSScriptRoot 'BackendFork\BackendFork.csproj') --configfile (Join-Path $PSScriptRoot 'NuGet.Config') --nologo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
node (Join-Path $PSScriptRoot 'tests\smoke.mjs')
exit $LASTEXITCODE
