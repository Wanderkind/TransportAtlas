param([string]$Database = 'data/transport_atlas.duckdb', [switch]$Production, [ValidateRange(1024,65535)][int]$ApiPort = 8000)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$data = Join-Path $root 'data'
$record = Join-Path $data 'local-services.json'
$logDirectory = Join-Path $data ('local-verification/phase3/services/' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
if (Test-Path $record) { throw 'Managed services already recorded. Run scripts/stop_local.ps1 before restarting.' }
foreach ($port in $ApiPort,5173) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Stop that service before launching this dashboard."
    }
}
$python = Join-Path $root '.venv/Scripts/python.exe'
$vite = Join-Path $root 'frontend/node_modules/vite/bin/vite.js'
if (!(Test-Path $python) -or !(Test-Path $vite)) { throw 'Install backend and frontend dependencies first.' }
if ($Production -and !(Test-Path (Join-Path $root 'frontend/dist/index.html'))) { throw 'Run npm run build --prefix frontend first.' }
$node = (Get-Command node.exe).Source
$db = if ([IO.Path]::IsPathRooted($Database)) { $Database } else { Join-Path $root $Database }
$services = @()
# The frontend proxy and browser checks read the API port from this variable.
$env:TRANSPORT_ATLAS_API_PORT = "$ApiPort"
try {
    $backend = Start-Process -FilePath $python -ArgumentList @('-m','transport_atlas','--db',('"'+$db+'"'),'serve','--port',"$ApiPort") -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'backend.stdout.log') -RedirectStandardError (Join-Path $logDirectory 'backend.stderr.log')
    $services += @{ id=$backend.Id; token=$python; name='backend' }
    $viteArguments = @(('"'+$vite+'"'))
    if ($Production) { $viteArguments += 'preview' }
    $viteArguments += @('--host','127.0.0.1','--port','5173','--strictPort')
    $frontend = Start-Process -FilePath $node -ArgumentList $viteArguments -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDirectory 'frontend.stdout.log') -RedirectStandardError (Join-Path $logDirectory 'frontend.stderr.log')
    $services += @{ id=$frontend.Id; token=$vite; name='frontend' }
    @{ root=$root; services=$services; logs=$logDirectory } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $record -Encoding utf8
    $deadline = (Get-Date).AddSeconds(90)
    do {
        if ($backend.HasExited -or $frontend.HasExited) { throw "Service exited; inspect $logDirectory. Stop the backend before importing feeds." }
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:$ApiPort/api/v1/health" -TimeoutSec 2
            $page = Invoke-WebRequest 'http://127.0.0.1:5173/' -TimeoutSec 2 -UseBasicParsing
            if ($health.status -eq 'ok' -and $page.StatusCode -eq 200) {
                Write-Output 'Dashboard: http://127.0.0.1:5173/?date=2026-11-06'
                Write-Output 'Stop: ./scripts/stop_local.ps1'
                Write-Output "Logs: $logDirectory"
                return
            }
        } catch { }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    throw "Startup timed out; inspect $logDirectory."
} catch {
    foreach ($service in $services) {
        if (Get-Process -Id $service.id -ErrorAction SilentlyContinue) {
            & taskkill.exe /PID $service.id /T /F | Out-Null
        }
    }
    if (Test-Path $record) { Remove-Item -LiteralPath $record }
    throw
}
