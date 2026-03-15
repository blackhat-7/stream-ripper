import re
import threading

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.sources import football

_SOURCES = [football]  # add f1, etc. here


def discover_matches(query: str, sport: str | None = None) -> list[LiveMatch]:
    """Run all matching source modules in parallel, deduplicate by title."""
    sources = [s for s in _SOURCES if sport is None or s.SPORT == sport]
    results: list[LiveMatch] = []
    lock = threading.Lock()

    def run(src):
        found = src.fetch_matches(query)
        with lock:
            results.extend(found)

    threads = [threading.Thread(target=run, args=(s,), daemon=True) for s in sources]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    seen: set[str] = set()
    unique: list[LiveMatch] = []
    for m in results:
        key = re.sub(r"\s+", " ", m.title.lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(m)
    return unique


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    src = next((s for s in _SOURCES if s.SPORT == match.sport), None)
    if src is None:
        raise ValueError(f"No source module for sport '{match.sport}'")
    return src.load_candidates(match)
