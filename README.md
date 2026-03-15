# stream-ripper

Finds live matches and plays the best stream in mpv. Monitors all sources in the background and auto-switches if the current one dies.

## Install

**Mac / Linux** — paste into Terminal:
```bash
curl -fsSL https://raw.githubusercontent.com/blackhat-7/stream-ripper/main/install.sh | bash
```

**Windows** — paste into PowerShell:
```powershell
irm https://raw.githubusercontent.com/blackhat-7/stream-ripper/main/install.ps1 | iex
```

The installer handles everything: mpv, Chrome, Python, dependencies, and adds a `streamer` command to your PATH.

## Uninstall

**Mac / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/blackhat-7/stream-ripper/main/uninstall.sh | bash
```

**Windows:**
```powershell
irm https://raw.githubusercontent.com/blackhat-7/stream-ripper/main/uninstall.ps1 | iex
```

## Usage

```bash
python -m _streamer.tui                # pick from all live matches
python -m _streamer.tui "liverpool"    # filter by name
```

**Keys**

| key | action |
|-----|--------|
| `↑ ↓` / `enter` | navigate & select match |
| `l` | toggle log pane |
| `r` | force re-probe all streams |
| `q` | quit |

## Debug / headless

```bash
python -m _streamer.cli                # plain-text CLI, no TUI
python -m _streamer.cli --list         # list matches and exit
```

## Adding a new sport

1. Create `_streamer/sources/<sport>.py` with `SPORT`, `fetch_matches(query)`, and `load_candidates(match)`
2. Add it to `_SOURCES` in `_streamer/sources/__init__.py`

## Settings

Edit `_streamer/settings.py` to tune health-check interval, switch threshold, timeouts, etc.
