# stream-ripper installer — Windows
# Run with: irm https://raw.githubusercontent.com/blackhat-7/stream-ripper/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

$REPO        = "https://github.com/blackhat-7/stream-ripper"
$INSTALL_DIR = "$env:LOCALAPPDATA\stream-ripper"
$BIN_DIR     = "$env:LOCALAPPDATA\Programs\stream-ripper"
$BIN         = "$BIN_DIR\streamer.bat"

function Info  { Write-Host "  [ok] $args" -ForegroundColor Green }
function Step  { Write-Host "`n-> $args" -ForegroundColor White }
function Warn  { Write-Host "  [!]  $args" -ForegroundColor Yellow }
function Abort { Write-Host "`n  [x] $args`n" -ForegroundColor Red; exit 1 }

Write-Host "`n  stream-ripper installer`n" -ForegroundColor White

# ── Require PowerShell 5+ ─────────────────────────────────────────────────────
if ($PSVersionTable.PSVersion.Major -lt 5) {
    Abort "PowerShell 5 or newer required. Please update Windows."
}

# ── winget ────────────────────────────────────────────────────────────────────
Step "Checking winget"
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Abort "winget not found. Open the Microsoft Store, install 'App Installer', then re-run this script."
}
Info "winget ready"

# ── mpv ───────────────────────────────────────────────────────────────────────
Step "Checking mpv"
if (-not (Get-Command mpv -ErrorAction SilentlyContinue)) {
    Info "Installing mpv..."
    winget install --id=shinchiro.mpv -e --silent
}
Info "mpv ready"

# ── Google Chrome ─────────────────────────────────────────────────────────────
Step "Checking Google Chrome"
$chromePaths = @(
    "$env:PROGRAMFILES\Google\Chrome\Application\chrome.exe",
    "$env:PROGRAMFILES(X86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$hasChrome = $chromePaths | Where-Object { Test-Path $_ }
if (-not $hasChrome) {
    Info "Installing Google Chrome..."
    winget install --id=Google.Chrome -e --silent
}
Info "Chrome ready"

# ── Git ───────────────────────────────────────────────────────────────────────
Step "Checking Git"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Info "Installing Git..."
    winget install --id=Git.Git -e --silent
    # Reload PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
}
Info "Git ready"

# ── uv ────────────────────────────────────────────────────────────────────────
Step "Checking uv (Python package manager)"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Info "Installing uv..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:PATH"
}
Info "uv ready"

# ── clone / update repo ───────────────────────────────────────────────────────
Step "Installing stream-ripper"
if (Test-Path "$INSTALL_DIR\.git") {
    Info "Updating existing installation..."
    git -C $INSTALL_DIR pull --ff-only -q
} else {
    Info "Downloading stream-ripper..."
    New-Item -ItemType Directory -Force -Path (Split-Path $INSTALL_DIR) | Out-Null
    git clone --depth=1 -q $REPO $INSTALL_DIR
}
Info "Installed to $INSTALL_DIR"

# ── Python deps ───────────────────────────────────────────────────────────────
Step "Installing Python dependencies"
Push-Location $INSTALL_DIR
uv sync -q
Pop-Location
Info "Dependencies ready"

# ── Playwright browser ────────────────────────────────────────────────────────
Step "Setting up Playwright"
try {
    Push-Location $INSTALL_DIR
    uv run playwright install chromium --with-deps 2>&1 | Select-Object -Last 2
    Pop-Location
    Info "Playwright ready"
} catch {
    Warn "Playwright setup had issues — some streams may not resolve."
}

# ── launcher .bat ─────────────────────────────────────────────────────────────
Step "Creating launcher"
New-Item -ItemType Directory -Force -Path $BIN_DIR | Out-Null
@"
@echo off
uv run --project "$INSTALL_DIR" python -m _streamer.tui %*
"@ | Set-Content $BIN -Encoding ASCII
Info "Launcher created at $BIN"

# ── add to user PATH ──────────────────────────────────────────────────────────
$userPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notlike "*$BIN_DIR*") {
    Step "Adding to PATH"
    [System.Environment]::SetEnvironmentVariable(
        "PATH", "$BIN_DIR;$userPath", "User"
    )
    $env:PATH = "$BIN_DIR;$env:PATH"
    Info "Added $BIN_DIR to your user PATH"
}

# ── done ──────────────────────────────────────────────────────────────────────
Write-Host "`n  All done!`n" -ForegroundColor Green
Write-Host "  Open a new terminal and run: streamer"
Write-Host "  Or search for a match:       streamer `"team name`"`n"
