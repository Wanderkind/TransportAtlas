# Run from the project root after installing Python dependencies.
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$env:PYTHONPATH = Join-Path (Get-Location) 'backend'
$env:TRANSPORT_ATLAS_DB = Join-Path (Get-Location) 'data/transport_atlas.duckdb'
$env:TRANSPORT_ATLAS_DB_BACKEND = 'duckdb'
$python = Join-Path (Get-Location) '.venv/Scripts/python.exe'
if (!(Test-Path $python)) { throw 'Create .venv with Python 3.12 and install .[dev] first.' }
& $python -m transport_atlas serve
if ($LASTEXITCODE -ne 0) { throw "Backend exited with code $LASTEXITCODE" }
