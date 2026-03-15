#!/usr/bin/env bash
# stream-ripper installer — Mac & Linux
set -euo pipefail

REPO="https://github.com/blackhat-7/stream-ripper"
INSTALL_DIR="$HOME/.local/share/stream-ripper"
BIN_DIR="$HOME/.local/bin"
BIN="$BIN_DIR/streamer"

# ── colours ───────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  BOLD='\033[1m'; DIM='\033[2m'; GREEN='\033[0;32m'
  YELLOW='\033[0;33m'; RED='\033[0;31m'; RESET='\033[0m'
else
  BOLD=''; DIM=''; GREEN=''; YELLOW=''; RED=''; RESET=''
fi

info()    { echo -e "  ${GREEN}✓${RESET}  $*"; }
step()    { echo -e "\n${BOLD}→ $*${RESET}"; }
warn()    { echo -e "  ${YELLOW}!${RESET}  $*"; }
die()     { echo -e "\n  ${RED}✗  $*${RESET}\n"; exit 1; }

echo -e "\n${BOLD}  stream-ripper installer${RESET}\n"

# ── detect OS ─────────────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
  Darwin) PLATFORM="mac"   ;;
  Linux)  PLATFORM="linux" ;;
  *)      die "Unsupported OS: $OS. Please use the Windows installer (install.ps1)." ;;
esac

# ── helpers ───────────────────────────────────────────────────────────────────
need() { command -v "$1" &>/dev/null; }

pkg_install() {
  # $1 = brew name, $2 = apt name, $3 = dnf name, $4 = pacman name
  if [ "$PLATFORM" = "mac" ]; then
    brew install "$1" --quiet
  elif need apt-get; then
    sudo apt-get install -y "$2" -qq
  elif need dnf; then
    sudo dnf install -y "$3" -q
  elif need pacman; then
    sudo pacman -S --noconfirm "$4"
  else
    die "Could not install $1 — please install it manually and re-run."
  fi
}

# ── macOS: Homebrew ────────────────────────────────────────────────────────────
if [ "$PLATFORM" = "mac" ]; then
  step "Checking Homebrew"
  if ! need brew; then
    info "Installing Homebrew…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for this session
    for candidate in /opt/homebrew/bin /usr/local/bin; do
      [ -f "$candidate/brew" ] && export PATH="$candidate:$PATH" && break
    done
  fi
  info "Homebrew ready"
fi

# ── mpv ───────────────────────────────────────────────────────────────────────
step "Checking mpv"
if ! need mpv; then
  info "Installing mpv…"
  pkg_install mpv mpv mpv mpv
fi
info "mpv $(mpv --version | head -1 | cut -d' ' -f2)"

# ── Chrome (Playwright fallback) ──────────────────────────────────────────────
step "Checking Google Chrome"
HAS_CHROME=false
if [ "$PLATFORM" = "mac" ]; then
  [ -d "/Applications/Google Chrome.app" ] && HAS_CHROME=true
else
  need google-chrome || need google-chrome-stable || need chromium-browser && HAS_CHROME=true || true
fi
if ! $HAS_CHROME; then
  info "Installing Google Chrome…"
  if [ "$PLATFORM" = "mac" ]; then
    brew install --cask google-chrome --quiet
  elif need apt-get; then
    curl -fsSL https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -o /tmp/chrome.deb
    sudo apt-get install -y /tmp/chrome.deb -qq
    rm /tmp/chrome.deb
  else
    warn "Could not auto-install Chrome. Some streams may not resolve — install Chrome manually if needed."
  fi
fi
$HAS_CHROME && info "Chrome found" || info "Chrome installed"

# ── uv ────────────────────────────────────────────────────────────────────────
step "Checking uv (Python package manager)"
if ! need uv; then
  info "Installing uv…"
  curl -fsSL https://astral.sh/uv/install.sh | sh
  # Source the updated PATH
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
info "uv $(uv --version)"

# ── clone / update repo ───────────────────────────────────────────────────────
step "Installing stream-ripper"
if [ -d "$INSTALL_DIR/.git" ]; then
  info "Updating existing installation…"
  git -C "$INSTALL_DIR" pull --ff-only -q
else
  info "Downloading stream-ripper…"
  git clone --depth=1 -q "$REPO" "$INSTALL_DIR"
fi
info "Installed to $INSTALL_DIR"

# ── Python deps ───────────────────────────────────────────────────────────────
step "Installing Python dependencies"
(cd "$INSTALL_DIR" && uv sync -q)
info "Dependencies ready"

# ── Playwright browser ────────────────────────────────────────────────────────
step "Setting up Playwright"
(cd "$INSTALL_DIR" && uv run playwright install chromium --with-deps 2>&1 | tail -2) || \
  warn "Playwright setup had issues — some streams may not resolve."
info "Playwright ready"

# ── launcher script ───────────────────────────────────────────────────────────
step "Creating launcher"
mkdir -p "$BIN_DIR"
cat > "$BIN" <<EOF
#!/usr/bin/env bash
exec uv run --project "$INSTALL_DIR" python -m _streamer.tui "\$@"
EOF
chmod +x "$BIN"
info "Launcher created at $BIN"

# ── PATH check ────────────────────────────────────────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  step "Adding $BIN_DIR to PATH"
  SHELL_RC=""
  case "${SHELL:-}" in
    */zsh)  SHELL_RC="$HOME/.zshrc"  ;;
    */bash) SHELL_RC="$HOME/.bashrc" ;;
  esac
  if [ -n "$SHELL_RC" ]; then
    echo "" >> "$SHELL_RC"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
    warn "Added PATH to $SHELL_RC — restart your terminal or run: source $SHELL_RC"
  else
    warn "Add $BIN_DIR to your PATH to use the 'streamer' command."
  fi
fi

# ── done ──────────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}${GREEN}  All done!${RESET}\n"
echo -e "  Run ${BOLD}streamer${RESET} to launch, or:"
echo -e "  ${DIM}streamer \"team name\"${RESET}  — search for a specific match\n"
