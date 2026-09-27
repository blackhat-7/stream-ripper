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

Two screens: **Matches** (live matches grouped by league, with how many sites carry each) and **Watching** (the stream now playing plus every candidate stream with its status and latency). The bottom bar always shows the keys that work right now; press `?` for all of them.

**Keys** (vim-style; arrow keys also work)

| key | action |
|-----|--------|
| `j` `k` / `↑` `↓` | move down / up (counts work: `5j`) |
| `gg` `G` | top / bottom |
| `ctrl+d` `ctrl+u` / `ctrl+f` `ctrl+b` | half page / full page |
| `enter` / `l` | watch match · play selected stream |
| `/` | search matches (`enter` keeps the filter, `esc` clears it) |
| `n` | skip to the next best stream (Watching) |
| `r` | refresh matches · re-probe all streams |
| `h` / `esc` | back to matches |
| `L` / `ctrl+l` | toggle the log pane |
| `?` | help |
| `q` | quit |

## Debug / headless

```bash
python -m _streamer.cli                # plain-text CLI, no TUI
python -m _streamer.cli --list         # list matches and exit
```

## Adding a new site

1. Create `_streamer/sources/<site>.py` with `NAME`, `fetch_matches()`, and `load_candidates(match)`
2. Add it to `_SITES` in `_streamer/sources/__init__.py`

The same match found on several sites is merged, so every site's streams become fallbacks.

## Settings

Edit `_streamer/settings.py` to tune health-check interval, switch threshold, timeouts, etc.
