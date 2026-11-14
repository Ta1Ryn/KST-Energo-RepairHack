function Get-PostgresBinaryCandidates {
    param([string]$PageContent,[string]$SourceIndexContent)
    $page=[Net.WebUtility]::HtmlDecode($PageContent).Replace('\/','/').Replace('\u002F','/').Replace('\u002f','/')
    $pageUrls=[regex]::Matches($page,'https?://get\.enterprisedb\.com/postgresql/postgresql-(?<version>(?:18|17|16)\.\d+)(?:-(?<build>\d+))?-windows(?:-x64)?-binaries\.zip')
    $versions=[Collections.Generic.HashSet[string]]::new()
    foreach($match in $pageUrls){[void]$versions.Add($match.Groups['version'].Value)}
    foreach($match in [regex]::Matches($SourceIndexContent,'(?:href\s*=\s*["'']/)?v(?<version>(?:18|17|16)\.\d+)/')){
        [void]$versions.Add($match.Groups['version'].Value)
    }
    # If a page exposes only version labels, the archive is still probed before selection.
    foreach($match in [regex]::Matches($page,'\b(?<version>(?:18|17|16)\.\d+)\b')){
        [void]$versions.Add($match.Groups['version'].Value)
    }
    $ordered=@($versions|ForEach-Object{[version]$_}|Sort-Object -Descending)
    $chosen=@()
    foreach($major in @(18,17,16)){
        $count=if($major -eq 18){3}else{1}
        $chosen+=@($ordered|Where-Object{$_.Major -eq $major}|Select-Object -First $count)
    }
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach($version in $chosen){
        $versionText=$version.ToString()
        foreach($match in $pageUrls){
            if($match.Groups['version'].Value -ne $versionText){continue}
            $url=$match.Value.Replace('http://','https://')
            if($seen.Add($url)){[PSCustomObject]@{Url=$url;Version=$version;Source='EDB page'}}
        }
        foreach($build in @(1,2,3)){
            $url="https://get.enterprisedb.com/postgresql/postgresql-$versionText-$build-windows-x64-binaries.zip"
            if($seen.Add($url)){[PSCustomObject]@{Url=$url;Version=$version;Source='Verified direct archive'}}
        }
    }
}
function Test-PostgresZipUrl {
    param([string]$Url)
    $uri=[uri]$Url
    if($uri.Scheme -ne 'https' -or $uri.Host -ne 'get.enterprisedb.com'){return $false}
    $response=$null
    try{
        $request=[Net.HttpWebRequest]::Create($uri)
        $request.Method='GET'
        $request.Timeout=10000
        $request.ReadWriteTimeout=10000
        $request.UserAgent='BackendFork-Setup/1.1'
        $request.AddRange(0,3)
        $request.AutomaticDecompression=[Net.DecompressionMethods]::GZip -bor [Net.DecompressionMethods]::Deflate
        $response=$request.GetResponse()
        if($response.ResponseUri.Scheme -ne 'https'){return $false}
        $stream=$response.GetResponseStream()
        $bytes=New-Object byte[] 4
        $count=0
        while($count -lt 4){
            $read=$stream.Read($bytes,$count,4-$count)
            if($read -eq 0){break}
            $count+=$read
        }
        return $count -eq 4 -and $bytes[0] -eq 80 -and $bytes[1] -eq 75 -and $bytes[2] -eq 3 -and $bytes[3] -eq 4
    }catch{return $false}
    finally{if($null -ne $response){$response.Close()}}
}
function Resolve-PostgresBinaries {
    [CmdletBinding()]
    param(
        [string]$PageContent,
        [string]$SourceIndexContent,
        [scriptblock]$Probe
    )
    [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
    if(-not $PSBoundParameters.ContainsKey('PageContent')){
        try{$PageContent=(Invoke-WebRequest 'https://www.enterprisedb.com/download-postgresql-binaries' -UseBasicParsing -TimeoutSec 30).Content}
        catch{Write-Verbose 'EDB HTML unavailable; trying the official PostgreSQL version index.';$PageContent=''}
    }
    if(-not $PSBoundParameters.ContainsKey('SourceIndexContent')){
        try{$SourceIndexContent=(Invoke-WebRequest 'https://ftp.postgresql.org/pub/source/' -UseBasicParsing -TimeoutSec 30).Content}
        catch{Write-Verbose 'PostgreSQL version index unavailable.';$SourceIndexContent=''}
    }
    $candidates=@(Get-PostgresBinaryCandidates -PageContent $PageContent -SourceIndexContent $SourceIndexContent)
    if($candidates.Count -eq 0){throw 'Could not read supported PostgreSQL versions from the official sites. Check network access and retry setup.'}
    foreach($candidate in $candidates){
        Write-Verbose "Probing $($candidate.Url)"
        $available=if($null -ne $Probe){& $Probe $candidate.Url}else{Test-PostgresZipUrl $candidate.Url}
        if($available){return $candidate}
    }
    throw 'Official versions were found, but no Windows ZIP could be reached. Check network access and retry setup.'
}
