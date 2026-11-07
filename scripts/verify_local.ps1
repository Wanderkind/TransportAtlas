param([switch]$Browser, [switch]$Feeds)
$ErrorActionPreference = 'Stop'
# Native tools (Vite, pytest) write warnings to stderr. With 'Stop', Windows
# PowerShell 5.1 turns redirected stderr lines into terminating errors, so each
# native step runs under 'Continue' and is judged only by its exit code.
function Invoke-Native([scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Command 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $previous }
}
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $root
try {
    $python = Join-Path $root '.venv/Scripts/python.exe'
    if (!(Test-Path $python)) { throw 'Missing .venv. Run py -3.12 -m venv .venv and install .[dev].' }
    Invoke-Native { & $python -m pytest -q backend/tests }
    if ($LASTEXITCODE -ne 0) { throw 'Python tests failed' }
    Invoke-Native { & $python scripts/check_startup.py }
    if ($LASTEXITCODE -ne 0) { throw 'Backend startup failed' }
    Push-Location (Join-Path $root 'frontend')
    try {
        Invoke-Native { & npm.cmd run build }
        if ($LASTEXITCODE -ne 0) { throw 'TypeScript/Vite build failed' }
    } finally { Pop-Location }
    if ($Feeds) {
        # Independent raw ZIP/SQL audit: no network, imports or database writes.
        # Stop the backend first because DuckDB locks across processes.
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        Invoke-Native { & $python scripts/audit_local_evidence.py --output "data/local-verification/phase3/independent-audit-$stamp.json" }
        if ($LASTEXITCODE -ne 0) { throw 'Feed verification failed' }
    }
    if ($Browser) {
        # Start backend and frontend in separate terminals first.
        Invoke-Native { & $python scripts/smoke_real_ui.py }
        if ($LASTEXITCODE -ne 0) { throw 'Real-data browser smoke failed' }
        Invoke-Native { & $python scripts/smoke_filter_races.py }
        if ($LASTEXITCODE -ne 0) { throw 'Delayed/error/filter-race browser checks failed' }
        Invoke-Native { & $python scripts/smoke_quality_ui.py }
        if ($LASTEXITCODE -ne 0) { throw 'Data-quality indicator browser checks failed' }
        # Against the real database this checks the one-snapshot History view; against
        # data/history-demo/history_demo.duckdb it runs the full synthetic comparison.
        Invoke-Native { & $python scripts/smoke_history_ui.py }
        if ($LASTEXITCODE -ne 0) { throw 'History view browser checks failed' }
    }
} finally { Pop-Location }
