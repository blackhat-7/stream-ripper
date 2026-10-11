import os

PROBE_WORKERS   = 6     # candidates probed/resolved concurrently
POLL_EVERY      = 2     # seconds between mpv health checks
STALL_SECS      = 8     # switch if playback hasn't advanced for this long
START_TIMEOUT   = 20    # ...or if a freshly loaded stream hasn't started within this
RECHECK_EVERY   = 30    # seconds between background probes of the other streams
DEAD_RETRY_MAX  = 60    # max backoff (seconds) between retries when every stream is dead
MIN_DWELL       = 60    # seconds on a stream before auto-switching to a better one
SWITCH_MARGIN   = 0.70  # ...and only if current score is ≤70% of the candidate's
FAIL_LIMIT      = 2     # consecutive failures before forcing re-resolve
MAX_RESOLVES    = 3     # give up after this many re-resolve attempts
REQ_TIMEOUT     = 8     # HTTP timeout (seconds)
HEADED_FALLBACK = False # also retry the sniff in an off-screen visible Chrome (some players detect headless)
CACHE_TTL       = 30 * 24 * 3600  # resolved-URL disk cache (entries are re-validated on use)
CACHE_PATH      = os.path.expanduser("~/.stream-ripper/resolved.json")
MPV_SOCKET      = "/tmp/mpv-live.sock"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
