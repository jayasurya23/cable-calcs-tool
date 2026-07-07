# Local dev launcher (Windows / PowerShell).
#   ./run_dev.ps1
# Creates .venv on first run, installs deps, and starts uvicorn with reload.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

Write-Host "Starting Cable Web at http://127.0.0.1:8000 ..."
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
