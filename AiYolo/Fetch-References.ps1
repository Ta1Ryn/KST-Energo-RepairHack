$ErrorActionPreference='Stop'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$destination=Join-Path $PSScriptRoot ('references\download-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $destination -Force|Out-Null
$sources=@(
    @{Code='d160-112b';Url='https://www.hms-livgidromash.ru/catalog/nasosy/d-1d-2d/d_160_112b_model_479.html'}
)
$records=[Collections.Generic.List[object]]::new()
foreach($source in $sources){
    $folder=Join-Path $destination $source.Code
    New-Item -ItemType Directory -Path $folder -Force|Out-Null
    try{
        Write-Host ('Reading '+$source.Url)
        $page=Invoke-WebRequest -Uri $source.Url -UseBasicParsing -TimeoutSec 45
        [IO.File]::WriteAllText((Join-Path $folder 'source.html'),$page.Content,[Text.UTF8Encoding]::new($false))
        $urls=[Collections.Generic.HashSet[string]]::new()
        foreach($match in [regex]::Matches($page.Content,'(?is)(?:src|data-src|href|content)\s*=\s*["'']([^"'']+\.(?:jpe?g|png|webp)(?:\?[^"'']*)?)["'']')){
            $candidate=[Net.WebUtility]::HtmlDecode($match.Groups[1].Value)
            try{$uri=[uri]::new([uri]$source.Url,$candidate)}catch{continue}
            if($uri.Scheme -ne 'https' -or $uri.Host -ne ([uri]$source.Url).Host){continue}
            if($uri.AbsolutePath -match '(?i)(logo|icon|avatar|banner|sprite)'){continue}
            [void]$urls.Add($uri.AbsoluteUri)
        }
        $count=0
        foreach($url in $urls){
            if($count -ge 12){break};$count++
            $extension=[IO.Path]::GetExtension(([uri]$url).AbsolutePath).ToLowerInvariant()
            $file=Join-Path $folder ('reference-'+$count.ToString('00')+$extension)
            try{Invoke-WebRequest -Uri $url -OutFile $file -UseBasicParsing -TimeoutSec 60;$records.Add([PSCustomObject]@{Model=$source.Code;Page=$source.Url;ImageUrl=$url;File=$file;Status='reference-only; not annotated'})}
            catch{Write-Warning ('Image could not be downloaded: '+$url)}
        }
        if($count -eq 0){Write-Warning 'No static image URLs found. Open the page in your browser and save the relevant pump photos manually.'}
    }catch{Write-Warning ('Page could not be read: '+$source.Url+'; '+$_.Exception.Message)}
}
[IO.File]::WriteAllText((Join-Path $destination 'sources.json'),(ConvertTo-Json -InputObject @($records.ToArray()) -Depth 4),[Text.UTF8Encoding]::new($false))
Write-Host ("Downloaded reference images: $($records.Count). Folder: "+$destination)
Write-Host 'Review downloaded images manually. Catalogue pictures do not automatically represent any defect class.'
