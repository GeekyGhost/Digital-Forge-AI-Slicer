@echo off
cd /d "%~dp0"
set "EXE=app\src-tauri\target\release\digital-forge-3d-slicer.exe"
if exist "%EXE%" (
  start "" "%EXE%"
  exit /b 0
)
echo Digital Forge 3D Slicer has not been built yet.
echo Run this once in PowerShell from this folder:
echo   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
pause
