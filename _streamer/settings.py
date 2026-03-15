CHECK_EVERY     = 20    # seconds between background health checks
FAIL_LIMIT      = 2     # consecutive failures before forcing re-resolve
MAX_RESOLVES    = 3     # give up after this many re-resolve attempts
REQ_TIMEOUT     = 8     # HTTP timeout (seconds)
RESOLVE_TIMEOUT = 30    # yt-dlp / streamlink timeout
MPV_SOCKET      = "/tmp/mpv-live.sock"
SWITCH_MARGIN   = 0.70  # only switch if candidate latency is ≤70% of current

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
