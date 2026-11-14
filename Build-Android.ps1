param([string]$Target=$PSScriptRoot,[string]$JavaHome,[string]$SdkRoot,[switch]$CheckOnly)
$ErrorActionPreference='Stop'
$project=Join-Path ([IO.Path]::GetFullPath($Target)) 'RepairHack'
if(-not(Test-Path -LiteralPath (Join-Path $project 'gradlew.bat'))){throw "Android project not found: $project"}
if(-not $JavaHome){
    $candidates=@($env:JAVA_HOME,(Join-Path $env:ProgramFiles 'Android\Android Studio\jbr'),(Join-Path $env:LOCALAPPDATA 'Programs\Android Studio\jbr'))
    $JavaHome=$candidates|Where-Object{$_ -and (Test-Path -LiteralPath (Join-Path $_ 'bin\javac.exe'))}|Select-Object -First 1
}
if(-not $SdkRoot){
    $candidates=@($env:ANDROID_HOME,$env:ANDROID_SDK_ROOT,(Join-Path $env:LOCALAPPDATA 'Android\Sdk'))
    $SdkRoot=$candidates|Where-Object{$_ -and (Test-Path -LiteralPath (Join-Path $_ 'platform-tools\adb.exe'))}|Select-Object -First 1
}
if(-not $JavaHome -or -not $SdkRoot){throw 'Android Studio JDK/SDK not found. Open RepairHack in Android Studio and install SDK 36.1, or pass -JavaHome <JDK 17+ path> -SdkRoot <Android SDK path>.'}
if($CheckOnly){Write-Host 'Android JDK/SDK found. No build requested.';return}
$previousJava=$env:JAVA_HOME
$previousSdk=$env:ANDROID_HOME
try{
    $env:JAVA_HOME=[IO.Path]::GetFullPath($JavaHome)
    $env:ANDROID_HOME=[IO.Path]::GetFullPath($SdkRoot)
    Push-Location -LiteralPath $project
    try{& '.\gradlew.bat' --no-daemon ':app:assembleDebug';if($LASTEXITCODE -ne 0){throw 'Android build failed; see Gradle output above.'}}
    finally{Pop-Location}
}finally{$env:JAVA_HOME=$previousJava;$env:ANDROID_HOME=$previousSdk}
$apk=Join-Path $project 'app\build\outputs\apk\debug\app-debug.apk'
if(-not(Test-Path -LiteralPath $apk)){throw 'Gradle finished but APK was not found.'}
Write-Host "APK ready: $apk"
