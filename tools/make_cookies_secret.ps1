# Builds the value for the YT_COOKIES_B64 GitHub secret from a cookies.txt export.
#
#   .\tools\make_cookies_secret.ps1                       # uses $HOME\Downloads\cookies.txt
#   .\tools\make_cookies_secret.ps1 -Path C:\path\to\cookies.txt
#
# It validates the file, base64-encodes it, proves the encoding round-trips, then
# copies the result to your clipboard. Paste it into the GitHub secret. Nothing is
# uploaded and no secret is written to disk.

param(
    [string]$Path = (Join-Path $HOME "Downloads\cookies.txt")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Path)) {
    Write-Host "FAIL: no file at $Path" -ForegroundColor Red
    Write-Host "Export cookies from a signed-in youtube.com tab (extension: 'Get cookies.txt LOCALLY')."
    exit 1
}

$bytes = [IO.File]::ReadAllBytes($Path)
if ($bytes.Length -eq 0) {
    Write-Host "FAIL: $Path is empty" -ForegroundColor Red
    exit 1
}

$text = [IO.File]::ReadAllText($Path)

if ($text -notmatch "youtube\.com") {
    Write-Host "FAIL: this file has no youtube.com cookies." -ForegroundColor Red
    Write-Host "You probably exported while on a different site. Open youtube.com while signed in, then export."
    exit 1
}

$youtubeRows = ($text -split "`n" | Where-Object { $_ -match "youtube\.com" }).Count

# Round-trip check: decode what we are about to hand over.
$b64 = [Convert]::ToBase64String($bytes)
$roundTrip = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
if ($roundTrip -ne $text) {
    Write-Host "FAIL: base64 round-trip did not match the original file." -ForegroundColor Red
    exit 1
}

$limit = 48000
$state = if ($b64.Length -gt $limit) { "TOO BIG" } else { "ok" }
$colour = if ($b64.Length -gt $limit) { "Red" } else { "Green" }

Write-Host ""
Write-Host ("source file : {0}" -f $Path)
Write-Host ("size        : {0:N0} bytes" -f $bytes.Length)
Write-Host ("youtube rows: {0}" -f $youtubeRows)
Write-Host ("base64      : {0:N0} characters ({1})" -f $b64.Length, $state) -ForegroundColor $colour
Write-Host ""

if ($b64.Length -gt $limit) {
    Write-Host "GitHub secrets max out at 48,000 characters. Re-export cookies while ONLY youtube.com" -ForegroundColor Red
    Write-Host "tabs are open so the file stays small, then run this again." -ForegroundColor Red
    exit 1
}

Set-Clipboard -Value $b64
$check = Get-Clipboard
if ($check -ne $b64) {
    $out = Join-Path $env:TEMP "yt_cookies_b64.txt"
    Set-Content -Path $out -Value $b64 -NoNewline -Encoding ascii
    Write-Host "Clipboard copy failed, so the value was written to:" -ForegroundColor Yellow
    Write-Host "  $out" -ForegroundColor Yellow
    Write-Host "Open it, copy all of it, and delete the file afterwards." -ForegroundColor Yellow
    exit 0
}

Write-Host "SUCCESS: value is on your clipboard." -ForegroundColor Green
Write-Host "GitHub -> Settings -> Secrets and variables -> Actions -> YT_COOKIES_B64 -> Update -> paste -> Save."
Write-Host "Paste must be a single line of A-Z a-z 0-9 + / = and nothing else."
Write-Host ""
Write-Host "Cookies expire every few weeks. Re-run this script and update the secret when runs start"
Write-Host "failing with 'Sign in to confirm you're not a bot'."
