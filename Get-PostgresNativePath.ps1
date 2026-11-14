function Get-PostgresNativePath {
    param([Parameter(Mandatory=$true)][string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $full)) { throw "PostgreSQL path does not exist: $full" }
    if ($full -notmatch '[^\x00-\x7F]') { return $full }
    # EDB's Windows utilities put installation paths into SQL using the ANSI
    # codepage. Use the existing 8.3 alias, without relocating any files.
    $fileSystem = New-Object -ComObject Scripting.FileSystemObject
    try {
        $short = if (Test-Path -LiteralPath $full -PathType Container) {
            $fileSystem.GetFolder($full).ShortPath
        } else { $fileSystem.GetFile($full).ShortPath }
    } finally { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($fileSystem) }
    if ([string]::IsNullOrWhiteSpace($short) -or $short -match '[^\x00-\x7F]') {
        throw "No ASCII Windows short path is available for $full. PostgreSQL needs an installation path without Cyrillic characters."
    }
    return $short
}
