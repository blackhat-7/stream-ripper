"""ppv.st (formerly ppv.to) — JSON API; the api.* host rotates with the site domain."""
import time

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.net import get_first
from _streamer.sources import fmhy

NAME = "ppv"
_MIRRORS = [f"https://{d}" for d in ("ppv.st", "ppv.tj", "ppvs.pk", "ppv.rw", "ppv.ms", "ppv.bi", "ppv.ug")]
_PREROLL = 30 * 60  # streams usually open ~30 min before kickoff


def fetch_matches() -> list[LiveMatch]:
    now = time.time()
    out = []
    for cat in get_first([m.replace("://", "://api.", 1) + "/api/streams" for m in fmhy.mirrors("ppv.st", _MIRRORS)],
                         json=True).json().get("streams", []):
        if cat.get("category") != "Football":
            continue
        for s in cat.get("streams", []):
            if s.get("always_live") or not s["starts_at"] - _PREROLL <= now < s["ends_at"]:
                continue
            out.append(LiveMatch(id=str(s["id"]), title=s["name"], source_site=NAME,
                                 sport="football", raw=s, league=s.get("tag", "")))
    return out


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    streams = [match.raw] + match.raw.get("substreams", [])
    return [StreamCandidate(label=s.get("source_tag") or "main", embed_url=s["iframe"], site=NAME)
            for s in streams if s.get("iframe")]
