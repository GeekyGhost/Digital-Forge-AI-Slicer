# Runs the app with live reload while editing the UI (Vite + Tauri dev).
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $Root 'app')
try {
    if (-not (Test-Path node_modules)) { & npm.cmd ci --no-audit --no-fund }
    & npm.cmd run tauri dev
} finally { Pop-Location }
