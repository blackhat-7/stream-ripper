"""streamed.pk — JSON API, same backend on every mirror."""
import time
from concurrent.futures import ThreadPoolExecutor

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.net import get_first
from _streamer.sources import fmhy

NAME = "streamed"
_MIRRORS = ["https://streamed.pk", "https://streamed.st", "https://streamed.su"]
_MAX_AGE_MS = 3 * 3600 * 1000  # the "live" list keeps finished matches for hours


def _api(path: str):
    return get_first([m + path for m in fmhy.mirrors("streamed", _MIRRORS)], json=True).json()


def fetch_matches() -> list[LiveMatch]:
    now_ms = time.time() * 1000
    return [
        LiveMatch(id=m["id"], title=m.get("title", m["id"]), source_site=NAME,
                  sport="football", raw=m)
        for m in _api("/api/matches/live")
        if m.get("category") == "football" and now_ms - m.get("date", now_ms) < _MAX_AGE_MS
    ]


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    def streams(src: dict) -> list[dict]:
        try:
            return _api(f"/api/stream/{src['source']}/{src['id']}")
        except Exception:
            return []

    sources = match.raw.get("sources", [])
    with ThreadPoolExecutor(max(len(sources), 1)) as ex:
        found = [s for batch in ex.map(streams, sources) for s in batch]

    out = []
    for s in found:
        if not s.get("embedUrl"):
            continue
        # "language" is often "English - Sky Sports Football": language, then channel
        lang, _, channel = (s.get("language") or "").partition(" - ")
        lang = lang.strip().title()
        lang_tag = f" · {lang}" if lang and lang != "English" else ""
        name = channel.strip() or s.get("source", "?")
        label = f"{name} #{s.get('streamNo', '?')} · {'HD' if s.get('hd') else 'SD'}{lang_tag}"
        out.append(StreamCandidate(label=label, embed_url=s["embedUrl"], site=NAME,
                                   viewers=s.get("viewers", 0), language=lang))
    return sorted(out, key=lambda c: c.viewers, reverse=True)
