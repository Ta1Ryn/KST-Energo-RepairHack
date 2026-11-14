param([string]$ModelUrl='',[string]$CatalogHtml='')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'ModelCandidates.ps1')
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$catalogUrl='https://www.hms-livgidromash.ru/documentation/3d/'
$folder=Join-Path $PSScriptRoot ('models\download-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,6))
New-Item -ItemType Directory -Path $folder -Force|Out-Null
if(-not $ModelUrl){
    Write-Host 'Reading the manufacturer 3D catalogue for D160-112b...'
    if(-not $CatalogHtml){$cache=Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'models') -Recurse -File -Filter 'catalogue.html' -ErrorAction SilentlyContinue|Sort-Object LastWriteTime -Descending|Select-Object -First 1;if($cache){$CatalogHtml=$cache.FullName}}
    if($CatalogHtml){$html=[IO.File]::ReadAllText($CatalogHtml);Write-Host ('Using saved catalogue: '+$CatalogHtml)}else{$html=(Invoke-WebRequest -Uri $catalogUrl -UseBasicParsing -TimeoutSec 60).Content}
    [IO.File]::WriteAllText((Join-Path $folder 'catalogue.html'),$html,[Text.UTF8Encoding]::new($false))
    $candidates=@(Get-PumpModelCandidates -Html $html -BaseUrl $catalogUrl)
    [IO.File]::WriteAllText((Join-Path $folder 'candidates.json'),(ConvertTo-Json -InputObject $candidates -Depth 3),[Text.UTF8Encoding]::new($false))
    if(-not $candidates.Count){throw "No identifiable pump CAD download found in the page. Open $catalogUrl and run this script with -ModelUrl '<direct file link>'. Saved page: $folder"}
    $selected=$candidates[0]
    $ModelUrl=$selected.Url
}else{
    $modelUri=[uri]$ModelUrl
    if($modelUri.Scheme -ne 'https' -or $modelUri.Host -ne 'www.hms-livgidromash.ru'){throw 'Use a direct HTTPS model link from the manufacturer site.'}
    $selected=[PSCustomObject]@{Match='user-selected';Label='User-selected manufacturer model';Url=$ModelUrl}
}
$uri=[uri]$ModelUrl
$filename=[uri]::UnescapeDataString([IO.Path]::GetFileName($uri.AbsolutePath))
$filename=[regex]::Replace($filename,'[<>:"/\\|?*]','_')
if([string]::IsNullOrWhiteSpace($filename) -or $filename -in @('.','..')){throw 'Model URL has no usable filename.'}
$target=Join-Path $folder $filename
Write-Host ('Selected: '+$selected.Label)
Write-Host ('Match: '+$selected.Match)
Write-Host ('Downloading: '+$ModelUrl)
Invoke-WebRequest -Uri $ModelUrl -OutFile $target -UseBasicParsing -TimeoutSec 900
$stream=[IO.File]::OpenRead($target)
try{$head=New-Object byte[] 1024;$read=$stream.Read($head,0,$head.Length)}finally{$stream.Dispose()}
$prefix=[Text.Encoding]::UTF8.GetString($head,0,$read).TrimStart()
if($read -eq 0 -or $prefix -match '^(?is)(<!doctype\s+html|<html\b)'){throw 'The server returned an empty file or HTML instead of a model.'}
$extension=[IO.Path]::GetExtension($target).ToLowerInvariant()
if($extension -eq '.zip' -and -not($read -ge 4 -and $head[0] -eq 80 -and $head[1] -eq 75)){throw 'Invalid ZIP signature.'}
if($extension -in @('.step','.stp') -and $prefix -notmatch 'ISO-10303-21'){throw 'Invalid STEP header.'}
$info=[PSCustomObject]@{RequestedModel='D160-112b';Match=$selected.Match;Description=$selected.Label;Catalogue=$catalogUrl;Url=$ModelUrl;File=$target;Sha256=(Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash;Purpose='single pump demonstration geometry; not defect training data'}
[IO.File]::WriteAllText((Join-Path $folder 'model-source.json'),($info|ConvertTo-Json -Depth 3),[Text.UTF8Encoding]::new($false))
Write-Host ('Saved pump model: '+$target)
Write-Host 'The model has not been modified or automatically labelled as defective.'
