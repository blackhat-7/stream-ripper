"""cdnlivetv (backend of StreamSports99) — JSON API of events with TV channels."""
from datetime import datetime, timezone

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.net import get_first

NAME = "cdnlive"
_MIRRORS = ["https://api.cdnlivetv.is", "https://api.cdnlivetv.tv", "https://cdnlivetv.tv"]


def fetch_matches() -> list[LiveMatch]:
    data = get_first([m + "/api/v1/events/sports/soccer/?user=cdnlivetv&plan=free" for m in _MIRRORS],
                     json=True).json()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")  # API times are UTC
    # "status" stays "NS" during play, so go by the start/end window.
    return [LiveMatch(id=e["gameID"], title=e["event"], source_site=NAME, sport="football",
                      raw=e, league=e.get("tournament", ""))
            for e in data.get("cdn-live-tv", {}).get("Soccer", [])
            if e.get("channels") and e["start"] <= now < e["end"]]


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    return [StreamCandidate(label=c["channel_name"] + (" · HD" if " HD" in c["channel_name"] else ""),
                            embed_url=c["url"], site=NAME, viewers=c.get("viewers", 0))
            for c in match.raw["channels"] if c.get("url")]
