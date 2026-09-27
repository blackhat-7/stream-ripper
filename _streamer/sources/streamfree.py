"""streamfree.top — JSON API; embeds are served from strmfree.st."""
import re

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.net import get_first

NAME = "streamfree"
_MIRRORS = ["https://streamfree.top", "https://strmfree.st"]


def fetch_matches() -> list[LiveMatch]:
    # "soccer" is football here ("football" means NFL); sources fill in once live.
    data = get_first([m + "/api/v1/streams?category=soccer" for m in _MIRRORS], json=True).json()
    return [LiveMatch(id=s["stream_key"], title=s["name"], source_site=NAME, sport="football",
                      raw=s, league=s.get("league", ""))
            for s in data.get("streams", []) if s.get("sources")]


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    return [StreamCandidate(label=_label(url), embed_url=url, site=NAME) for url in match.raw["sources"]]


def _label(url: str) -> str:
    """.../embed/soccer/{key}1080p2 -> '1080p #2 · HD'"""
    m = re.search(r"(\d{3,4})p(\d?)$", url)
    if not m:
        return "main"
    return f"{m[1]}p #{m[2] or 1}" + (" · HD" if int(m[1]) >= 720 else "")
