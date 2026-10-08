# Runs the engine test suite with the project's own Python.
$Root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $Root 'engine')
try {
    & (Join-Path $Root 'runtime\python\python.exe') -s -m unittest discover -s tests -v
} finally { Pop-Location }
