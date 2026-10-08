<#
.SYNOPSIS
  Installs everything Digital Forge 3D Slicer needs INSIDE this project folder.

  runtime\python   portable CPython 3.12 + engine packages (no system Python used)
  runtime\orca     OrcaSlicer 2.4.2 portable (slicing engine, Kobra S1 profiles)
  runtime\blender  Blender 4.4.3 portable (rendering and deep editing, later milestones)
  runtime\trellis  TRELLIS.2 native runtime + models\trellis2\q8 weights (already present here)
  app\...\release  the desktop app (needs Node.js and Rust; skip with -SkipApp)

  Every download is checked against a pinned SHA-256. Re-running is safe: finished
  steps are skipped and interrupted downloads resume.
#>
param(
    [switch]$SkipBlender,
    [switch]$SkipTrellis,
    [switch]$SkipApp
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Downloads = Join-Path $Root 'downloads'
$Runtime = Join-Path $Root 'runtime'
New-Item -ItemType Directory -Force $Downloads, $Runtime | Out-Null

$Pins = @{
    Python  = @{ Url = 'https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-x86_64-pc-windows-msvc-install_only.tar.gz'
                 File = 'cpython-3.12.14-windows-x64.tar.gz'; Sha = 'e90c1b6419da3bd812dd73bb3de40287a21abf153438147639ec5e20375ea93f' }
    Orca    = @{ Url = 'https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.4.2/OrcaSlicer_Windows_V2.4.2_x64_portable.zip'
                 File = 'OrcaSlicer_Windows_V2.4.2_x64_portable.zip'; Sha = 'feba3009dfb9d268779cca5758a1a5bc3b7d0722bf8fa48d5c57340de975d6be' }
    Blender = @{ Url = 'https://download.blender.org/release/Blender4.4/blender-4.4.3-windows-x64.zip'
                 File = 'blender-4.4.3-windows-x64.zip'; Sha = '60a9703b07f2cf42509f699ccdec4f5ede71c1932f6c14329f0e14c023e27d5c' }
}

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

function Get-Pinned($pin) {
    $path = Join-Path $Downloads $pin.File
    if ((Test-Path $path) -and ((Get-FileHash $path -Algorithm SHA256).Hash -eq $pin.Sha.ToUpper())) {
        Write-Host "  verified $($pin.File)"
        return $path
    }
    Write-Host "  downloading $($pin.File)"
    & curl.exe -L --fail --retry 5 --retry-delay 3 -C - -o $path $pin.Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $($pin.Url)" }
    $hash = (Get-FileHash $path -Algorithm SHA256).Hash
    if ($hash -ne $pin.Sha.ToUpper()) {
        Remove-Item $path -Force
        throw "Checksum mismatch for $($pin.File). The file was removed; run setup again."
    }
    return $path
}

# 1. Portable Python ---------------------------------------------------------------
Step 'Portable Python 3.12'
$Python = Join-Path $Runtime 'python\python.exe'
if (-not (Test-Path $Python)) {
    $archive = Get-Pinned $Pins.Python
    & tar.exe -xzf $archive -C $Runtime
    if ($LASTEXITCODE -ne 0) { throw 'Could not unpack Python' }
}
& $Python -s -m pip install --disable-pip-version-check --no-warn-script-location -r (Join-Path $Root 'engine\requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installing engine packages failed' }

# 2. OrcaSlicer engine ---------------------------------------------------------------
Step 'OrcaSlicer 2.4.2 (slicing engine)'
$OrcaDir = Join-Path $Runtime 'orca'
if (-not (Test-Path (Join-Path $OrcaDir 'orca-slicer.exe'))) {
    $archive = Get-Pinned $Pins.Orca
    New-Item -ItemType Directory -Force $OrcaDir | Out-Null
    & tar.exe -xf $archive -C $OrcaDir
    if ($LASTEXITCODE -ne 0) { throw 'Could not unpack OrcaSlicer' }
}
New-Item -ItemType Directory -Force (Join-Path $Runtime 'orca-data') | Out-Null

# 3. Blender -----------------------------------------------------------------------------
if (-not $SkipBlender) {
    Step 'Blender 4.4.3 portable'
    $BlenderDir = Join-Path $Runtime 'blender'
    if (-not (Get-ChildItem $BlenderDir -Recurse -Filter blender.exe -ErrorAction SilentlyContinue)) {
        $archive = Get-Pinned $Pins.Blender
        New-Item -ItemType Directory -Force $BlenderDir | Out-Null
        & tar.exe -xf $archive -C $BlenderDir
        if ($LASTEXITCODE -ne 0) { throw 'Could not unpack Blender' }
    }
}

# 4. TRELLIS.2 ----------------------------------------------------------------------------
if (-not $SkipTrellis) {
    Step 'TRELLIS.2 runtime and weights'
    & $Python -s (Join-Path $Root 'scripts\download_trellis.py')
    if ($LASTEXITCODE -ne 0) { throw 'TRELLIS download failed' }
}

# 5. Desktop app -------------------------------------------------------------------------
if (-not $SkipApp) {
    Step 'Desktop app'
    $node = Get-Command npm.cmd -ErrorAction SilentlyContinue
    $cargo = Get-Command cargo.exe -ErrorAction SilentlyContinue
    if (-not $node -or -not $cargo) {
        Write-Warning 'Node.js and Rust are needed to build the app (as for Digital Forge Audio Studio). Install them, then run scripts\build.ps1.'
    } else {
        & (Join-Path $Root 'scripts\build.ps1')
    }
}

Step 'Check'
$env:PYTHONPATH = Join-Path $Root 'engine'
& $Python -s -m dforge doctor
Write-Host "`nDone. Start the app with 'Digital Forge 3D Slicer.cmd'." -ForegroundColor Green
