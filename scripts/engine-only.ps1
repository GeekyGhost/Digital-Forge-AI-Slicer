# Starts just the engine on http://127.0.0.1:7863 (for browser testing with `npm run dev`).
$Root = Split-Path -Parent $PSScriptRoot
$env:DFORGE_ROOT = $Root
Push-Location (Join-Path $Root 'engine')
try {
    & (Join-Path $Root 'runtime\python\python.exe') -s -m dforge serve --port 7863
} finally { Pop-Location }
