#!/usr/bin/env bash
# stream-ripper uninstaller — Mac & Linux
set -euo pipefail

INSTALL_DIR="$HOME/.local/share/stream-ripper"
BIN="$HOME/.local/bin/streamer"

if [ -t 1 ]; then
  BOLD='\033[1m'; GREEN='\033[0;32m'; RED='\033[0;31m'; RESET='\033[0m'
else
  BOLD=''; GREEN=''; RED=''; RESET=''
fi

info() { echo -e "  ${GREEN}✓${RESET}  $*"; }
die()  { echo -e "\n  ${RED}✗  $*${RESET}\n"; exit 1; }

echo -e "\n${BOLD}  stream-ripper uninstaller${RESET}\n"

REMOVED=false

if [ -f "$BIN" ]; then
  rm "$BIN"
  info "Removed launcher: $BIN"
  REMOVED=true
fi

if [ -d "$INSTALL_DIR" ]; then
  rm -rf "$INSTALL_DIR"
  info "Removed app: $INSTALL_DIR"
  REMOVED=true
fi

if ! $REMOVED; then
  echo -e "  Nothing to uninstall — stream-ripper is not installed.\n"
  exit 0
fi

echo -e "\n  ${BOLD}${GREEN}Uninstalled.${RESET}\n"
echo -e "  Note: mpv and Chrome were not removed (they may be used by other apps)."
echo -e "  Remove them manually if you wish:\n"
echo -e "    brew uninstall mpv"
echo -e "    brew uninstall --cask google-chrome\n"
