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
        # Independent raw ZIP/SQL audit: no network, imports or database writes.
        # Stop the backend first because DuckDB locks across processes.
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        & $python scripts/audit_local_evidence.py --output "data/local-verification/phase3/independent-audit-$stamp.json"
        if ($LASTEXITCODE -ne 0) { throw 'Feed verification failed' }
    }
    if ($Browser) {
        # Start backend and frontend in separate terminals first.
        & $python scripts/smoke_real_ui.py
        if ($LASTEXITCODE -ne 0) { throw 'Real-data browser smoke failed' }
        & $python scripts/smoke_filter_races.py
        if ($LASTEXITCODE -ne 0) { throw 'Delayed/error/filter-race browser checks failed' }
    }
} finally { Pop-Location }
