$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$uvDir = Join-Path $projectRoot ".tools\uv"
$uvExe = Join-Path $uvDir "uv.exe"
$uvArchive = Join-Path $uvDir "uv.zip"

New-Item -ItemType Directory -Force -Path $uvDir | Out-Null

if (-not (Test-Path -LiteralPath $uvExe)) {
    Write-Host "Downloading the project-local uv executable..."
    curl.exe -L --fail --retry 3 `
        --output $uvArchive `
        "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
    Expand-Archive -LiteralPath $uvArchive -DestinationPath $uvDir -Force
    Remove-Item -LiteralPath $uvArchive
}

$env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot ".tools\python"
$env:UV_CACHE_DIR = Join-Path $projectRoot ".cache\uv"

Push-Location $projectRoot
try {
    & $uvExe python install 3.12
    if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
        & $uvExe venv --python 3.12 .venv
    }
    & $uvExe sync --extra dev
    & ".\.venv\Scripts\fallguard.exe" doctor
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Environment ready."
Write-Host "Activate: .\.venv\Scripts\Activate.ps1"
Write-Host "Help:     fallguard --help"
