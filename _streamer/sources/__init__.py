"""Live football discovery across independent aggregator sites.

Each site module exposes NAME, fetch_matches() and load_candidates(match).
The same fixture listed on several sites is merged into one LiveMatch
(primary + alts) so its streams from every site can back each other up.
"""
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, wait

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.sources import cdnlive, futbolx, matchora, ppv, roxie, streamed, streamfree, timstreams

log = logging.getLogger(__name__)

# Priority order: earlier sites become the primary listing of a merged match.
_SITES = [streamed, ppv, matchora, cdnlive, timstreams, streamfree, roxie, futbolx]
_BY_NAME = {s.NAME: s for s in _SITES}
_DISCOVER_TIMEOUT = 10
_CANDIDATES_TIMEOUT = 15

# Words that don't identify a club.
_NOISE = {"fc", "cf", "afc", "sc", "ac", "cd", "club", "the", "de", "women"}


def discover_matches(query: str) -> list[LiveMatch]:
    """Fetch live football from every site in parallel and merge duplicates."""
    per_site = _run_all(lambda s: s.fetch_matches(), _SITES, _DISCOVER_TIMEOUT)
    groups: list[LiveMatch] = []
    for site in _SITES:
        for m in per_site.get(site.NAME, []):
            primary = next((g for g in groups if _same_fixture(g, m)), None)
            if primary is None:
                groups.append(m)
            else:
                primary.alts.append(m)
                primary.league = primary.league or m.league
    return [g for g in groups if _matches_query(query, g)]


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    """Gather streams from the primary listing and every alt in parallel, deduped by embed URL."""
    listings = [match] + match.alts
    results = _run_all(lambda m: _BY_NAME[m.source_site].load_candidates(m), listings,
                       _CANDIDATES_TIMEOUT, name=lambda m: f"{m.source_site}:{m.id}")
    seen: set[str] = set()
    out: list[StreamCandidate] = []
    for m in listings:
        for c in results.get(f"{m.source_site}:{m.id}", []):
            if c.embed_url not in seen:
                seen.add(c.embed_url)
                out.append(c)
    return out


def _run_all(fn, items, timeout: float, name=lambda s: s.NAME) -> dict:
    """Run fn over items in parallel; collect results by name, logging and skipping failures."""
    ex = ThreadPoolExecutor(max(len(items), 1))
    futures = {ex.submit(fn, it): name(it) for it in items}
    done, pending = wait(futures, timeout=timeout)
    ex.shutdown(wait=False, cancel_futures=True)
    out = {}
    for f in done:
        try:
            out[futures[f]] = f.result()
        except Exception as exc:
            log.warning(f"{futures[f]}: {str(exc)[:150]}")
    for f in pending:
        log.warning(f"{futures[f]}: timed out after {timeout}s")
    return out


def _matches_query(query: str, m: LiveMatch) -> bool:
    text = " ".join([m.title, m.league] + [a.title for a in m.alts]).lower()
    return not query or any(w in text for w in query.lower().split())


def _teams(title: str) -> list[set[str]]:
    """'Man Utd vs. Chelsea FC' -> [{'man', 'united'}, {'chelsea'}]; [] if not a two-team title."""
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    sides = re.split(r"\s+(?:vs?\.?|-|–|@)\s+", title.replace("utd", "united"))
    if len(sides) != 2:
        return []
    out = []
    for side in sides:
        words = set(re.findall(r"[a-z0-9]+", side))
        out.append({w for w in words - _NOISE if len(w) >= 3} or words)
    return out


def _same_side(a: set[str], b: set[str]) -> bool:
    """Every word of the shorter name shares a 3-letter prefix with a word of the other
    ('man' ~ 'manchester', 'munchen' ~ 'munich')."""
    small, big = sorted((a, b), key=len)
    return all(any(x[:3] == y[:3] for y in big) for x in small)


def _same_fixture(a: LiveMatch, b: LiveMatch) -> bool:
    ta, tb = _teams(a.title), _teams(b.title)
    if not ta or not tb:
        return a.title.lower().strip() == b.title.lower().strip()
    return ((_same_side(ta[0], tb[0]) and _same_side(ta[1], tb[1]))
            or (_same_side(ta[0], tb[1]) and _same_side(ta[1], tb[0])))
