@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-old-server.ps1"
pause
