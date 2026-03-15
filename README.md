# stream-ripper

Finds live matches and plays the best stream in mpv. Monitors all sources in the background and auto-switches if the current one dies.

## Setup

```bash
brew install mpv
brew install --cask google-chrome
brew install yt-dlp streamlink  # optional fallbacks
```

## Usage

```bash
uv run streamer.py                 # pick from all live matches
uv run streamer.py "liverpool"     # filter by name
uv run streamer.py --list          # list and exit
```

## Adding a new sport

1. Create `_streamer/sources/<sport>.py` with `SPORT`, `fetch_matches(query)`, and `load_candidates(match)`
2. Add it to `_SOURCES` in `_streamer/sources/__init__.py`

## Settings

Edit `_streamer/settings.py` to tune health-check interval, switch threshold, timeouts, etc.
