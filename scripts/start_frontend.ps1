# Run from the project root after npm install in frontend/.
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '../frontend'))
npm run dev
