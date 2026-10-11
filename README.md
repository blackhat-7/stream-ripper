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
./stream --web                         # web app: watch in your browser
./stream                               # terminal UI: plays in mpv
./stream liverpool                     # terminal UI, filtered by name
./stream --list                        # list matches and exit
```

(Installed copies use `streamer` the same way.)

### Web app

Opens `http://127.0.0.1:8765`. Pick a match from the home screen or the Matches panel; the best stream starts on its own and switches if it stalls or drops.

- **Streams panel**: every stream with its real resolution (read from the playlist and the player), language and flag where the source says, a stability meter (share of health checks passed) and load time. Sort by best, stable or fastest; filter by language or HD.
- **Settings** (`,`): switch to better streams automatically, prefer HD, preferred language (these steer which stream plays), show offline streams.
- **Layout**: hide either panel (`[` `]`) or both for theater mode (`t`); star matches to pin them as favorites.
- **Keys**: `space` play/pause, `f` full screen, `m` mute, `n` next stream, `/` search.

### Terminal UI

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
- **Resolving** (`resolve.py`): disk cache → known-host resolver → page/iframe HTML scan → headless Chrome sniff (`browser.py`). Every URL is checked end to end, with the Referer that works, before it's used.
- **Watching** (`watch.py`): plays the best live stream and switches on stall, death, or a much better stream. The player is mpv (TUI) or the web page (`web.py`, which plays through the local proxy with hls.js).

Resolved URLs are cached at `~/.stream-ripper/resolved.json` (re-checked on use); the FMHY list at `~/.stream-ripper/fmhy.json`.

## Adding a new site

1. Create `_streamer/sources/<site>.py` with `NAME`, `fetch_matches()`, and `load_candidates(match)`
2. Add it to `_SITES` in `_streamer/sources/__init__.py`

For a new embed host whose playlist can be pulled over plain HTTP, add a resolver to `_HTTP_RESOLVERS` in `_streamer/resolve.py`.

## Settings

Edit `_streamer/settings.py`: health-check interval, stall limits, switch margin, retry backoff, headed-browser fallback, cache TTL.
