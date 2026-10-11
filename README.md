# stream-ripper

Finds live matches and plays the best stream in mpv. Monitors all sources in the background and auto-switches if the current one dies or stalls.

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
./stream                               # pick from all live matches
./stream liverpool                     # filter by name
./stream --list                        # list matches and exit
```

(`./stream` wraps `uv run python -m _streamer.tui`, or `_streamer.cli` for `--list`.)

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

## How it works

- **Sites** (`sources/`): one module per aggregator. Each keeps a few known mirrors and adds the domains [FMHY](https://fmhy.net/video#live-sports) currently lists (`sources/fmhy.py`, 12 h cache), so a domain move doesn't break discovery. The same match found on several sites is merged, so every site's streams become fallbacks.
- **One HTTP client** (`net.py`, curl_cffi) for scraping, resolving, probing and the local proxy, so a stream that scrapes fine can't 403 the health check.
- **Resolving** (`resolve.py`): disk cache → known-host resolver → page/iframe HTML scan → Chrome sniff (`browser.py`, headless then an off-screen visible retry). Every URL is checked end to end, with the Referer that works, before it's used.
- **Watching** (`watch.py`): plays the best live stream and switches on stall, death, or a much better stream.

Resolved URLs are cached at `~/.stream-ripper/resolved.json` (re-checked on use); the FMHY list at `~/.stream-ripper/fmhy.json`.

## Adding a new site

1. Create `_streamer/sources/<site>.py` with `NAME`, `fetch_matches()`, and `load_candidates(match)`
2. Add it to `_SITES` in `_streamer/sources/__init__.py`

For a new embed host whose playlist can be pulled over plain HTTP, add a resolver to `_HTTP_RESOLVERS` in `_streamer/resolve.py`.

## Settings

Edit `_streamer/settings.py`: health-check interval, stall limits, switch margin, retry backoff, headed-browser fallback, cache TTL.
