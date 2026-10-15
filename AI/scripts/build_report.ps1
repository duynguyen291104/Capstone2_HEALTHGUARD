param(
    [string]$Source = "docs\FallGuard_AI_Full_Report.tex",
    [string]$OutputDirectory = "output\pdf"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourcePath = Join-Path $ProjectRoot $Source
$OutputPath = Join-Path $ProjectRoot $OutputDirectory

if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
    throw "Khong tim thay tep LaTeX: $SourcePath"
}

if (-not (Get-Command xelatex -ErrorAction SilentlyContinue)) {
    throw "Chua tim thay xelatex. Hay cai MiKTeX hoac TeX Live va them xelatex vao PATH."
}

New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null

Push-Location $ProjectRoot
try {
    # Ba lượt giúp ổn định mục lục, liên kết chéo, danh sách hình và bảng.
    for ($Pass = 1; $Pass -le 3; $Pass++) {
        Write-Host "Dang bien dich LaTeX - luot $Pass/3..."
        & xelatex `
            -interaction=nonstopmode `
            -halt-on-error `
            "-output-directory=$OutputDirectory" `
            $Source

        if ($LASTEXITCODE -ne 0) {
            throw "XeLaTeX that bai o luot $Pass. Xem file .log trong $OutputPath."
        }
    }
}
finally {
    Pop-Location
}

$PdfPath = Join-Path $OutputPath "FallGuard_AI_Full_Report.pdf"
Write-Host "Hoan tat: $PdfPath"
