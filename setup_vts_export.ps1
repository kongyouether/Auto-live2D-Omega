param(
    [string]$AdapterRoot = "",
    [switch]$IUnderstandExperimentalMoc3
)

$ErrorActionPreference = "Stop"

if (-not $IUnderstandExperimentalMoc3) {
    throw "This installs an experimental, unofficial MOC3 writer. Re-run with -IUnderstandExperimentalMoc3 after reviewing VTS_EXPORT.md."
}

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $AdapterRoot) {
    $AdapterRoot = Join-Path (Split-Path -Parent $projectRoot) "_research\image2live2d"
}
$AdapterRoot = [System.IO.Path]::GetFullPath($AdapterRoot)
$repository = "https://github.com/Wzhang3912/image2live2d.git"
$revision = "b3fea7536f2d680897dbf5cce5a13046da75803c"

if (-not (Test-Path -LiteralPath (Join-Path $AdapterRoot ".git"))) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $AdapterRoot) | Out-Null
    git clone $repository $AdapterRoot
}

git -C $AdapterRoot fetch origin $revision
git -C $AdapterRoot checkout --detach $revision

$venvPython = Join-Path $AdapterRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    py -3.12 -m venv (Join-Path $AdapterRoot ".venv")
}

& $venvPython -m pip install -e "$AdapterRoot[decompose,mesh,rig,dev]"
& $venvPython -m pytest -q `
    (Join-Path $AdapterRoot "tests\test_live2d_emitter.py") `
    (Join-Path $AdapterRoot "tests\test_moc3_emit.py") `
    (Join-Path $AdapterRoot "tests\test_moc3_binary.py")

Write-Host "VTube Studio export adapter is ready: $AdapterRoot"
