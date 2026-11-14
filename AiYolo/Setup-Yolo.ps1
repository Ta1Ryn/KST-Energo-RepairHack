param([switch]$SkipPretrained)
$ErrorActionPreference='Stop'
$root=$PSScriptRoot
$python=Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if(-not(Test-Path -LiteralPath $python)){
    $installer=Join-Path $env:TEMP 'fork-python-3.12.10-amd64.exe'
    Write-Host 'Downloading Python 3.12.10 from python.org...'
    [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $installer -UseBasicParsing -TimeoutSec 600
    $signature=Get-AuthenticodeSignature -LiteralPath $installer
    if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Python Software Foundation*'){throw 'Python installer signature verification failed.'}
    $pythonDirectory=Split-Path -Parent $python
    $install=Start-Process -FilePath $installer -ArgumentList @('/quiet','InstallAllUsers=0','Include_launcher=0','Include_test=0','PrependPath=0',('TargetDir="'+$pythonDirectory+'"')) -WindowStyle Hidden -Wait -PassThru
    if($install.ExitCode -notin @(0,3010) -or -not(Test-Path -LiteralPath $python)){throw 'Python installation failed.'}
}
$venv=Join-Path $root '.venv'
$venvPython=Join-Path $venv 'Scripts\python.exe'
if(-not(Test-Path -LiteralPath $venvPython)){& $python -m venv $venv;if($LASTEXITCODE -ne 0){throw 'Creating the virtual environment failed.'}}
& $venvPython -m pip install --upgrade pip
if($LASTEXITCODE -ne 0){throw 'pip setup failed.'}
Write-Host 'Installing GPU PyTorch. This download can exceed 2 GB.'
& $venvPython -m pip install 'torch==2.7.1' 'torchvision==0.22.1' --index-url 'https://download.pytorch.org/whl/cu126'
if($LASTEXITCODE -ne 0){throw 'CUDA PyTorch installation failed.'}
& $venvPython -m pip install -r (Join-Path $root 'requirements.txt')
if($LASTEXITCODE -ne 0){throw 'YOLO dependencies installation failed.'}
& $venvPython -m compileall -q $root -x '[/\\](\.venv|runs)[/\\]'
if($LASTEXITCODE -ne 0){throw 'Python syntax check failed.'}
& $venvPython -m unittest discover -s (Join-Path $root 'tests') -p 'test_*.py'
if($LASTEXITCODE -ne 0){throw 'YOLO contract checks failed.'}
if(-not $SkipPretrained){& $venvPython (Join-Path $root 'prepare_environment.py');if($LASTEXITCODE -ne 0){throw 'GPU / pretrained weights setup failed.'}}
$frozen=& $venvPython -m pip freeze
if($LASTEXITCODE -ne 0){throw 'Dependency version capture failed.'}
[IO.File]::WriteAllLines((Join-Path $root 'requirements.local.lock.txt'),[string[]]$frozen,[Text.UTF8Encoding]::new($false))
Write-Host 'YOLO environment is prepared. Training requires reviewed and split defect images.'
