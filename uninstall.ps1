# stream-ripper uninstaller — Windows
# Run with: irm https://raw.githubusercontent.com/blackhat-7/stream-ripper/main/uninstall.ps1 | iex

$INSTALL_DIR = "$env:LOCALAPPDATA\stream-ripper"
$BIN_DIR     = "$env:LOCALAPPDATA\Programs\stream-ripper"

function Info { Write-Host "  [ok] $args" -ForegroundColor Green }

Write-Host "`n  stream-ripper uninstaller`n" -ForegroundColor White

$removed = $false

if (Test-Path $INSTALL_DIR) {
    Remove-Item -Recurse -Force $INSTALL_DIR
    Info "Removed app: $INSTALL_DIR"
    $removed = $true
}

if (Test-Path $BIN_DIR) {
    Remove-Item -Recurse -Force $BIN_DIR
    Info "Removed launcher: $BIN_DIR"
    $removed = $true
}

# Remove from user PATH
$userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -like "*$BIN_DIR*") {
    $newPath = ($userPath -split ";" | Where-Object { $_ -ne $BIN_DIR }) -join ";"
    [System.Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    Info "Removed from PATH"
}

if (-not $removed) {
    Write-Host "  Nothing to uninstall — stream-ripper is not installed.`n"
    exit 0
}

Write-Host "`n  Uninstalled.`n" -ForegroundColor Green
Write-Host "  Note: mpv and Chrome were not removed (they may be used by other apps)."
Write-Host "  Remove them manually via Settings > Apps if you wish.`n"
