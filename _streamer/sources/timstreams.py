"""timst.cfd (TimStreams) — JSON API; embeds on exmxbxe.cfd."""
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.net import get_first

NAME = "timstreams"
_MIRRORS = ["https://timst.cfd"]
_TZ = ZoneInfo("America/New_York")  # event times are US Eastern
_WINDOW = (-30 * 60, 3 * 3600)       # "live" = from 30 min before kickoff to 3 h after
_SOCCER = 1                          # genre id, see /api/settings


def fetch_matches() -> list[LiveMatch]:
    now = time.time()
    out = []
    for e in get_first([m + "/api/live-upcoming" for m in _MIRRORS], json=True).json().get("events", []):
        start = datetime.fromisoformat(e["time"]).replace(tzinfo=_TZ).timestamp()
        if e.get("genre") == _SOCCER and e.get("streams") and _WINDOW[0] <= now - start <= _WINDOW[1]:
            out.append(LiveMatch(id=e["url"], title=e["name"], source_site=NAME, sport="football", raw=e))
    return out


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    return [StreamCandidate(label=s.get("name") or "main", embed_url=s["url"], site=NAME,
                            viewers=match.raw.get("viewers", 0))
            for s in match.raw["streams"] if s.get("url") and not s.get("vip")]
