# Stops the old TRELLIS Creation Studio server (GPT build) and moves its last two logs to legacy.
$Root = Split-Path -Parent $PSScriptRoot
$marker = Join-Path $Root 'legacy\gpt-studio\data\server-process.json'
if (Test-Path $marker) {
    $record = Get-Content $marker -Raw | ConvertFrom-Json
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)" -ErrorAction SilentlyContinue
    if ($process -and $process.CommandLine -match '-m studio serve') {
        & taskkill.exe /PID $record.pid /T /F | Out-Null
        Write-Host "Stopped old server (PID $($record.pid))."
    } elseif ($process) {
        Write-Warning "PID $($record.pid) is now a different program; leaving it alone."
    } else {
        Write-Host 'Old server is not running.'
    }
}
# Any other leftover old-studio processes started from this folder
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '-m studio serve' -and $_.ExecutablePath -like "$Root*" } | ForEach-Object {
    & taskkill.exe /PID $_.ProcessId /T /F | Out-Null
    Write-Host "Stopped old server (PID $($_.ProcessId))."
}
Start-Sleep -Seconds 1
foreach ($name in 'server.log', 'server-error.log') {
    $file = Join-Path $Root "data\$name"
    if (Test-Path $file) { Move-Item $file (Join-Path $Root 'legacy\gpt-studio\data') -Force }
}
Write-Host 'Old data moved to legacy\gpt-studio\data.'
