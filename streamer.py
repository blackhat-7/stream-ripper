#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "requests>=2.31.0",
#   "m3u8>=3.0.0",
#   "beautifulsoup4>=4.12.0",
#   "lxml>=5.0.0",
#   "yt-dlp>=2024.1.0",
#   "curl-cffi>=0.7.0",
#   "playwright>=1.40.0",
# ]
# ///
"""
Live Stream Player
──────────────────
Finds live matches and plays the best stream in mpv.
Monitors all sources and auto-switches if one dies.

Usage:
  uv run streamer.py                  # pick from live matches
  uv run streamer.py "liverpool"      # filter by name
  uv run streamer.py --list           # list and exit

Requirements:
  brew install mpv
  brew install --cask google-chrome
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from _streamer.cli import main

if __name__ == "__main__":
    main()
