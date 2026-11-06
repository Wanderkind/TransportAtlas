param([string]$Database = 'data/transport_atlas.duckdb', [switch]$Production)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$data = Join-Path $root 'data'
$record = Join-Path $data 'local-services.json'
if (Test-Path $record) { throw 'Managed services already recorded. Run scripts/stop_local.ps1 before restarting.' }
foreach ($port in 8000,5173) {
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
try {
    $backend = Start-Process -FilePath $python -ArgumentList @('-m','transport_atlas','--db',('"'+$db+'"'),'serve') -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $data 'backend.stdout.log') -RedirectStandardError (Join-Path $data 'backend.stderr.log')
    $services += @{ id=$backend.Id; token=$python; name='backend' }
    $viteArguments = @(('"'+$vite+'"'))
    if ($Production) { $viteArguments += 'preview' }
    $viteArguments += @('--host','127.0.0.1','--port','5173','--strictPort')
    $frontend = Start-Process -FilePath $node -ArgumentList $viteArguments -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $data 'frontend.stdout.log') -RedirectStandardError (Join-Path $data 'frontend.stderr.log')
    $services += @{ id=$frontend.Id; token=$vite; name='frontend' }
    @{ root=$root; services=$services } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $record -Encoding utf8
    $deadline = (Get-Date).AddSeconds(90)
    do {
        if ($backend.HasExited -or $frontend.HasExited) { throw 'Service exited; inspect data/*.stderr.log. Stop the backend before importing feeds.' }
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 2
            $page = Invoke-WebRequest 'http://127.0.0.1:5173/' -TimeoutSec 2 -UseBasicParsing
            if ($health.status -eq 'ok' -and $page.StatusCode -eq 200) {
                Write-Output 'Dashboard: http://127.0.0.1:5173/?date=2026-11-06'
                Write-Output 'Stop: ./scripts/stop_local.ps1'
                return
            }
        } catch { }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    throw 'Startup timed out; inspect data/*.stderr.log.'
} catch {
    foreach ($service in $services) {
        if (Get-Process -Id $service.id -ErrorAction SilentlyContinue) {
            & taskkill.exe /PID $service.id /T /F | Out-Null
        }
    }
    if (Test-Path $record) { Remove-Item -LiteralPath $record }
    throw
}
