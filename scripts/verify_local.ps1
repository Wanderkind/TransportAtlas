param([switch]$Browser, [switch]$Feeds)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $root
try {
    $python = Join-Path $root '.venv/Scripts/python.exe'
    if (!(Test-Path $python)) { throw 'Missing .venv. Run py -3.12 -m venv .venv and install .[dev].' }
    & $python -m pytest -q backend/tests
    if ($LASTEXITCODE -ne 0) { throw 'Python tests failed' }
    & $python scripts/check_startup.py
    if ($LASTEXITCODE -ne 0) { throw 'Backend startup failed' }
    Push-Location (Join-Path $root 'frontend')
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'TypeScript/Vite build failed' }
    } finally { Pop-Location }
    if ($Feeds) {
        # No network or repeated imports: verify current cached feeds and DB.
        # Backend must be stopped before this optional writable check.
        & $python scripts/import_local_feeds.py --offline --sources aspo,trenitalia_sardegna,arst,atp_sassari --report feeds-recheck.json
        if ($LASTEXITCODE -ne 0) { throw 'Feed verification failed' }
    }
    if ($Browser) {
        # Start backend and frontend in separate terminals first.
        & $python scripts/smoke_real_ui.py
        if ($LASTEXITCODE -ne 0) { throw 'Real-data browser smoke failed' }
    }
} finally { Pop-Location }
