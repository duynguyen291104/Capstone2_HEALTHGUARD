param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
$sampleSnapshot = Join-Path $projectRoot "demo_videos\telegram_samples\01_FALL_CONFIRMED_snapshot.jpg"
$fallguardExe = Join-Path $projectRoot ".venv\Scripts\fallguard.exe"

if (-not (Test-Path -LiteralPath $fallguardExe -PathType Leaf)) {
    $fallguardCommand = Get-Command fallguard -ErrorAction SilentlyContinue
    if ($null -eq $fallguardCommand) {
        throw "Khong tim thay fallguard. Hay chay .\scripts\bootstrap.ps1 truoc."
    }
    $fallguardExe = $fallguardCommand.Source
}

Write-Host ""
Write-Host "FallGuard Telegram setup" -ForegroundColor Cyan
Write-Host "Token da tung gui trong chat phai duoc /revoke tai @BotFather." -ForegroundColor Yellow
$revoked = (Read-Host "Ban da revoke token cu va tao token MOI? Nhap YES de tiep tuc").Trim()
if ($revoked -notin @("YES", "yes", "Y", "y")) {
    throw "Da dung: khong luu hoac su dung token da bi lo."
}

$secureToken = Read-Host "Nhap token MOI (ky tu se khong hien thi)" -AsSecureString
$token = [System.Net.NetworkCredential]::new("", $secureToken).Password.Trim()
if ($token -notmatch "^\d+:[A-Za-z0-9_-]{20,}$") {
    throw "Token khong dung dinh dang Telegram Bot API."
}

try {
$env:FALLGUARD_TELEGRAM_BOT_TOKEN = $token
$env:FALLGUARD_TELEGRAM_CHAT_ID = $null
$env:FALLGUARD_TELEGRAM_CHAT_IDS = $null
$env:FALLGUARD_TELEGRAM_ENABLED = "true"

Write-Host ""
Write-Host "Mo bot Telegram, bam Start hoac gui /start." -ForegroundColor Cyan
$null = Read-Host "Sau khi da gui /start, nhan Enter de tim Chat ID"
& $fallguardExe telegram discover
if ($LASTEXITCODE -ne 0) {
    throw "Khong the tim Chat ID. Kiem tra token, /start va webhook roi chay lai."
}

$chatId = (Read-Host "Nhap Chat ID vua hien thi").Trim()
if ($chatId -notmatch "^-?\d+$") {
    throw "Chat ID phai la mot so; Chat ID nhom co the bat dau bang dau tru."
}
$env:FALLGUARD_TELEGRAM_CHAT_ID = $chatId

function Set-DotEnvValues {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [System.Collections.Specialized.OrderedDictionary]$Values
    )

    $lines = [System.Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        foreach ($line in Get-Content -LiteralPath $Path) {
            $lines.Add([string]$line)
        }
    }

    foreach ($key in $Values.Keys) {
        $replacement = "$key=$($Values[$key])"
        $pattern = "^\s*$([regex]::Escape([string]$key))\s*="
        for ($index = $lines.Count - 1; $index -ge 0; $index--) {
            if ($lines[$index] -match $pattern) {
                $lines.RemoveAt($index)
            }
        }
        $lines.Add($replacement)
    }

    [System.IO.File]::WriteAllLines(
        $Path,
        [string[]]$lines,
        [System.Text.UTF8Encoding]::new($false)
    )
}

$dotenvValues = [ordered]@{
    FALLGUARD_TELEGRAM_BOT_TOKEN = $token
    FALLGUARD_TELEGRAM_CHAT_ID = $chatId
    FALLGUARD_TELEGRAM_CHAT_IDS = ""
    FALLGUARD_TELEGRAM_ENABLED = "true"
    FALLGUARD_TELEGRAM_TIMEOUT_SECONDS = "8"
}
Set-DotEnvValues -Path $envPath -Values $dotenvValues

Write-Host ""
Write-Host "Da luu cau hinh vao .env (file nay da nam trong .gitignore)." -ForegroundColor Green
Write-Host "Dang gui anh canh bao thu..." -ForegroundColor Cyan
& $fallguardExe telegram test --snapshot $sampleSnapshot
if ($LASTEXITCODE -ne 0) {
    throw "Telegram test that bai. Xem loi o tren va chay lai script."
}

Write-Host ""
Write-Host "THANH CONG: Telegram da nhan tin nhan va anh mau." -ForegroundColor Green
Write-Host "Neu web dang chay, hay Ctrl+C va khoi dong lai de nap .env moi."
Write-Host "Bay gio co the chay webcam hoac START_WEB_DEMO.ps1."
}
finally {
$env:FALLGUARD_TELEGRAM_BOT_TOKEN = $null
$env:FALLGUARD_TELEGRAM_CHAT_ID = $null
$env:FALLGUARD_TELEGRAM_CHAT_IDS = $null
$token = $null
$secureToken = $null
}
