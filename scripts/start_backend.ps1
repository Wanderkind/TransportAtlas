# Run from the project root after installing Python dependencies.
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$env:PYTHONPATH = Join-Path (Get-Location) 'backend'
$env:TRANSPORT_ATLAS_DB = Join-Path (Get-Location) 'data/transport_atlas.duckdb'
$env:TRANSPORT_ATLAS_DB_BACKEND = 'duckdb'
python -m transport_atlas serve
