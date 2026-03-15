import logging
import re
import threading

from bs4 import BeautifulSoup

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.net import http_get

log = logging.getLogger(__name__)
SPORT = "football"


def _matches_query(query: str, title: str) -> bool:
    return not query or any(w in title.lower() for w in query.lower().split())


def fetch_matches(query: str) -> list[LiveMatch]:
    """Aggregate live football matches from all sources."""
    results: list[LiveMatch] = []
    lock = threading.Lock()

    def run(fn):
        found = fn(query)
        with lock:
            results.extend(found)

    threads = [threading.Thread(target=run, args=(fn,), daemon=True)
               for fn in [_fetch_streamed_pk, _fetch_streameast, _fetch_sportsurge]]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    if match.source_site == "streamed.pk":
        return _load_streamed_pk(match)
    url = match.raw.get("url", match.id)
    return [StreamCandidate(label=match.source_site, embed_url=str(url))]


# ── Private fetchers ──────────────────────────────────────────────────────────

def _fetch_streamed_pk(query: str) -> list[LiveMatch]:
    try:
        resp = http_get("https://streamed.pk/api/matches/live")
        resp.raise_for_status()
        football = [m for m in resp.json() if m.get("category") == "football"]
        return [
            LiveMatch(id=m["id"], title=m.get("title", m["id"]),
                      source_site="streamed.pk", sport=SPORT, raw=m)
            for m in football if _matches_query(query, m.get("title", ""))
        ]
    except Exception as exc:
        log.warning(f"streamed.pk: {exc}")
        return []


def _fetch_streameast(query: str) -> list[LiveMatch]:
    for base in ["https://gostreameast.is", "https://thestreameast.fun"]:
        try:
            resp = http_get(base)
            if resp.status_code != 200:
                continue
            matches = []
            for row in BeautifulSoup(resp.text, "lxml").select(".match-row"):
                link = row.select_one(".match-name, a[href]")
                if not link:
                    continue
                title = link.get_text(strip=True)
                href = str(link.get("href", ""))
                cat = str(row.get("data-category", ""))
                if cat not in ("football", "soccer", "") or not _matches_query(query, title):
                    continue
                matches.append(LiveMatch(id=href, title=title,
                                         source_site=f"streameast ({base})",
                                         sport=SPORT, raw={"url": href, "base": base}))
            if matches:
                return matches
        except Exception as exc:
            log.debug(f"streameast {base}: {exc}")
    return []


def _fetch_sportsurge(query: str) -> list[LiveMatch]:
    for base in ["https://sportsurge.ws", "https://sportsurge.net", "https://sportsurge.uno"]:
        try:
            resp = http_get(base)
            if resp.status_code != 200:
                continue
            matches = []
            for a in BeautifulSoup(resp.text, "lxml").find_all("a", href=re.compile(r"/watch/")):
                title = a.get_text(" ", strip=True)
                href = str(a["href"])
                if not title or not _matches_query(query, title):
                    continue
                full_url = href if href.startswith("http") else base + href
                matches.append(LiveMatch(id=href, title=title,
                                         source_site=f"sportsurge ({base})",
                                         sport=SPORT, raw={"url": full_url}))
            if matches:
                return matches
        except Exception as exc:
            log.debug(f"sportsurge {base}: {exc}")
    return []


def _load_streamed_pk(match: LiveMatch) -> list[StreamCandidate]:
    candidates: list[StreamCandidate] = []
    lock = threading.Lock()

    def fetch_source(src):
        name = str(src.get("source", "?"))
        sid  = str(src.get("id", ""))
        try:
            resp = http_get(f"https://streamed.pk/api/stream/{name}/{sid}")
            if resp.status_code != 200:
                return
            streams = resp.json()
            for s in (streams if isinstance(streams, list) else []):
                embed = s.get("embedUrl", "")
                if not embed:
                    continue
                hd_tag   = "HD" if s.get("hd") else "SD"
                lang     = s.get("language", "")
                lang_tag = f" · {lang}" if lang and lang.lower() != "english" else ""
                with lock:
                    label = f"{name} #{s.get('streamNo', len(candidates)+1)} · {hd_tag}{lang_tag}"
                    candidates.append(StreamCandidate(
                        label=label, embed_url=embed, viewers=s.get("viewers", 0)))
        except Exception as exc:
            log.debug(f"load_candidates {name}: {exc}")

    threads = [threading.Thread(target=fetch_source, args=(s,), daemon=True)
               for s in match.raw.get("sources", [])]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    candidates.sort(key=lambda c: c.viewers, reverse=True)
    return candidates
