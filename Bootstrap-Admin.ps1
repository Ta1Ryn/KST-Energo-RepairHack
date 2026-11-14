param([ValidateRange(1024,65535)][int]$Port=5080)
$ErrorActionPreference='Stop'
$number=Read-Host 'Admin employee number'
$name=Read-Host 'Admin full name'
$secure=Read-Host 'Set your own 4-digit PIN' -AsSecureString
$pointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try{
    $pin=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    $body=@{employeeNumber=$number;fullName=$name;pin=$pin}|ConvertTo-Json
    $response=Invoke-RestMethod -Method Post -Uri ('http://127.0.0.1:'+$Port+'/api/workforce/bootstrap') -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
    Write-Host 'Admin created. Sign in on the phone using the employee number and PIN you just set.'
}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer);$pin=$null;$body=$null;$response=$null;$secure.Dispose()}
