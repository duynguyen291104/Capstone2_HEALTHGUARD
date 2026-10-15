$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$fallguardExe = Join-Path $projectRoot ".venv\Scripts\fallguard.exe"
$configPath = Join-Path $projectRoot "configs\urfd_demo.yaml"
$checkpointPath = Join-Path $projectRoot "artifacts\urfd_preview_gru\best.pt"

if (-not (Test-Path -LiteralPath $fallguardExe)) {
    throw "Environment not found. Run .\scripts\bootstrap.ps1 first."
}
if (-not (Test-Path -LiteralPath $checkpointPath)) {
    throw "Trained checkpoint not found: $checkpointPath"
}

Push-Location $projectRoot
try {
    & $fallguardExe serve `
        --config $configPath `
        --checkpoint $checkpointPath `
        --open
}
finally {
    Pop-Location
}
