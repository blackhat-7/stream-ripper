"""futbol-x.xyz — small curated JSON list with direct m3u8 links."""
import time
from datetime import datetime, timezone

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.net import get_first

NAME = "futbolx"
_MIRRORS = ["https://www.futbol-x.xyz"]
_PREROLL = 30 * 60


def fetch_matches() -> list[LiveMatch]:
    now = time.time()
    ts = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()  # times are UTC
    data = get_first([m + "/api/football.json" for m in _MIRRORS], json=True).json()
    return [LiveMatch(id=s["uri_name"], title=s["name"], source_site=NAME, sport="football",
                      raw=s, league=s.get("tag", ""))
            for cat in data.get("streams", []) for s in cat.get("streams", [])
            if s.get("streams") and s.get("ends_at") and ts(s["starts_at"]) - _PREROLL <= now <= ts(s["ends_at"])]


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    return [StreamCandidate(label=s.get("title") or "main", embed_url=s["url"], site=NAME)
            for s in match.raw["streams"] if s.get("url")]
