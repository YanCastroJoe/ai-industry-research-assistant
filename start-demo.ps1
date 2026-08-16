[CmdletBinding()]
param(
    [int]$Port = 8010,
    [switch]$SkipDependencyCheck
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$requirements = Join-Path $repoRoot "requirements.txt"

Set-Location $repoRoot

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "[DocFlow] Creating the Python virtual environment..."
    python -m venv .venv
}

if (-not $SkipDependencyCheck) {
    & $venvPython -c "import fastapi, uvicorn, mcp, pypdf" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[DocFlow] Installing missing dependencies..."
        & $venvPython -m pip install -r $requirements
    }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependency validation failed."
    }
}

Write-Host ""
Write-Host "[DocFlow] Demo URL: http://127.0.0.1:$Port"
Write-Host "[DocFlow] Press Ctrl+C in this window to stop the service."
Write-Host ""

& $venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
