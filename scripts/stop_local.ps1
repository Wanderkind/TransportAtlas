$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$record = Join-Path $root 'data/local-services.json'
if (!(Test-Path $record)) { Write-Output 'No managed services recorded.'; return }
$state = Get-Content -Raw -LiteralPath $record | ConvertFrom-Json
if ($state.root -ne $root) { throw 'Service record belongs to another checkout.' }
foreach ($service in $state.services) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($service.id)" -ErrorAction SilentlyContinue
    if (!$process) { continue }
    if (!$process.CommandLine -or $process.CommandLine.Replace('/','\').IndexOf($service.token.Replace('/','\'), [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw "PID $($service.id) no longer matches this checkout's $($service.name); refusing to stop it."
    }
    # The Windows venv launcher can own a separate Python child process.
    # PowerShell 5.1 turns taskkill's stderr (e.g. a child that already exited) into a
    # terminating error under 'Stop'; judge success by whether the process survived.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & taskkill.exe /PID $service.id /T /F 2>$null | Out-Null } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $service.id -ErrorAction SilentlyContinue)) {
        throw "Could not stop $($service.name) process tree."
    }
    Write-Output "Stopped $($service.name) (PID $($service.id))."
}
Remove-Item -LiteralPath $record
