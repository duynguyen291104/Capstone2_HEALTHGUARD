param(
    [string]$Config = "configs/mvp.yaml",
    [string]$Checkpoint = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $arguments = @("serve", "--config", $Config)
    if ($Checkpoint) {
        $arguments += @("--checkpoint", $Checkpoint)
    }
    & ".\.venv\Scripts\fallguard.exe" @arguments
}
finally {
    Pop-Location
}
