function Get-PumpModelCandidates {
    param([string]$Html,[string]$BaseUrl='https://www.hms-livgidromash.ru/documentation/3d/')
    $baseUri=[uri]$BaseUrl
    $rows=@([regex]::Matches($Html,'(?is)<tr\b[^>]*>.*?</tr>'))
    $cards=@([regex]::Matches($Html,'(?is)<li\b[^>]*class\s*=\s*["''][^"'']*\bevents-item\b[^"'']*\brow\b[^"'']*["''][^>]*>'))
    $results=@(foreach($anchor in [regex]::Matches($Html,'(?is)<a\b(?<attributes>[^>]*\bhref\s*=\s*["''](?<href>[^"'']+)["''][^>]*)>(?<label>.*?)</a>')){
        $href=[Net.WebUtility]::HtmlDecode($anchor.Groups['href'].Value)
        try{$uri=[uri]::new($baseUri,$href)}catch{continue}
        if($uri.Scheme -ne 'https' -or $uri.Host -ne $baseUri.Host){continue}
        $decoded=[uri]::UnescapeDataString($uri.AbsolutePath)
        if($decoded -notmatch '(?i)\.(zip|rar|7z|step|stp|igs|iges|stl|obj|glb|gltf|sldasm|sldprt|a3d|m3d)$'){continue}
        $context=$anchor.Value
        $row=$rows|Where-Object {$anchor.Index -ge $_.Index -and $anchor.Index -lt ($_.Index+$_.Length)}|Select-Object -First 1
        if($null -ne $row){$context=$row.Value}
        elseif($cards.Count){$card=$cards|Where-Object{$_.Index -le $anchor.Index}|Select-Object -Last 1;if($null -ne $card){$end=$Html.IndexOf('</ul>',$card.Index,[StringComparison]::OrdinalIgnoreCase);if($end -gt $anchor.Index){$context=$Html.Substring($card.Index,$end-$card.Index)}}}
        $label=[Net.WebUtility]::HtmlDecode(([regex]::Replace($context,'<[^>]+>',' ')))
        $name=[regex]::Match($context,'(?is)<strong[^>]*>(.*?)</strong>')
        if($name.Success){$label=[Net.WebUtility]::HtmlDecode([regex]::Replace($name.Groups[1].Value,'<[^>]+>',' '))}
        $label=[regex]::Replace($label,'\s+',' ').Trim()
        $search=$label+' '+$decoded
        $normal=[regex]::Replace($search.ToLowerInvariant(),'[^a-zа-я0-9]','').Replace('д','d').Replace('б','b')
        $score=if($normal.Contains('d160112b')){120}elseif($normal.Contains('d160112')){110}elseif($normal.Contains('d160')){80}elseif($search -match '(?i)(?:\b[12]?\s*[дd][ _-]?\d{2,4}[ _–-]+\d{1,3})'){40}elseif($normal.Contains('krm8065160')){30}elseif($normal.Contains('krm')){20}elseif($normal.Contains('krl')){10}else{0}
        if($score -eq 0){continue}
        [PSCustomObject]@{Score=$score;Match=if($score -ge 110){'target-family'}elseif($score -ge 40){'similar-D-series'}else{'alternative-Kordis-pump'};Label=$label;Url=$uri.AbsoluteUri}
    })
    return @($results|Sort-Object Score -Descending|Group-Object Url|ForEach-Object{$_.Group|Select-Object -First 1}|Sort-Object Score -Descending)
}
