# Builds the desktop app. Output: app\src-tauri\target\release\digital-forge-3d-slicer.exe
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $Root 'app')
try {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    & npm.cmd run tauri build
    if ($LASTEXITCODE -ne 0) { throw 'tauri build failed' }
} finally { Pop-Location }
Write-Host 'Built app\src-tauri\target\release\digital-forge-3d-slicer.exe' -ForegroundColor Green
