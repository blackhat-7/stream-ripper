"""matchora.to — documented free JSON API with its own restream CDN."""
from _streamer.models import LiveMatch, StreamCandidate
from _streamer.net import get_first
from _streamer.sources import fmhy

NAME = "matchora"
_MIRRORS = ["https://matchora.to", "https://matchora.pro"]


def _api(path: str):
    return get_first([m + path for m in fmhy.mirrors("matchora", _MIRRORS)], json=True).json()


def fetch_matches() -> list[LiveMatch]:
    return [LiveMatch(id=e["id"], title=f"{e['home']} vs {e['away']}", source_site=NAME,
                      sport="football", raw=e, league=e.get("league", ""))
            for e in _api("/api/v1/schedule?live=1").get("events", [])
            if e.get("sport") == "Soccer" and e.get("channel_count")]


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    # resolve.py turns /embed/channel/{ch}?fx={event} into a playlist via matchora's play API.
    return [StreamCandidate(label=c["name"] + (" · HD" if c.get("quality") in ("HD", "FHD") else ""),
                            embed_url=f"{_MIRRORS[0]}/embed/channel/{c['id']}?fx={match.id}", site=NAME,
                            viewers=c.get("watching", 0))
            for c in _api(f"/api/v1/events/{match.id}").get("channels", []) if not c.get("dead")]
