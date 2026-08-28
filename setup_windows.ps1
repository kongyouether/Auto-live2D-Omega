$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pythonCandidates = @(
    "C:\Users\ZhangQian\anaconda3\python.exe",
    "C:\Users\ZhangQian\miniconda3\python.exe"
)

Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath $venvPython)) {
    $basePython = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $basePython) {
        throw "Python 3.10 or newer was not found. Install Python and rerun this script."
    }
    & $basePython -m venv ".venv"
    if ($LASTEXITCODE -ne 0) { throw "Failed to create .venv." }
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip." }

& $venvPython -m pip install -r "requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Failed to install dependencies." }

& $venvPython -c "import webview; print('pywebview import OK')"
if ($LASTEXITCODE -ne 0) { throw "pywebview verification failed." }

Write-Host "Setup completed. Double-click run.bat to start Auto Live2D."
