"""roxiestreams.su — HTML schedule; stream pages link straight to m3u8 playlists."""
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.net import get_first

NAME = "roxie"
_MIRRORS = ["https://roxiestreams.su", "https://roxiestreams.biz"]
_TZ = ZoneInfo("America/Los_Angeles")  # schedule times are US Pacific
_WINDOW = (-30 * 60, 3 * 3600)         # "live" = from 30 min before kickoff to 3 h after
# showPlayer('clappr', getRandomStream('fs2.m3u8', 'tedesco'))">Stream 1
_BUTTON = re.compile(r"""showPlayer\('clappr',\s*getRandomStream\('([^']+)'(?:,\s*'([^']+)')?\)\)">([^<]*)""")


def _get(path: str) -> str:
    return get_first([m + path for m in _MIRRORS]).text


def fetch_matches() -> list[LiveMatch]:
    now = time.time()
    out = []
    for row in BeautifulSoup(_get("/soccer"), "lxml").select("table#eventsTable tbody tr"):
        link, start = row.select_one("td a[href]"), row.select_one("td.event-start-time")
        if not link or not start:
            continue
        kickoff = datetime.strptime(start.get_text(strip=True), "%B %d, %Y %I:%M %p")
        if not _WINDOW[0] <= now - kickoff.replace(tzinfo=_TZ).timestamp() <= _WINDOW[1]:
            continue
        title = link.get_text(" ", strip=True)
        out.append(LiveMatch(id=f"{link['href']}#{title}", title=title, source_site=NAME,
                             sport="football", raw={"path": link["href"]}))
    return out


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    page = _get(match.raw["path"])
    hosts_file = re.search(r"domainsz?\w*\.txt", page)
    domains = dict.fromkeys(_get("/" + hosts_file.group(0)).split()) if hosts_file else {}
    return [StreamCandidate(label=f"{label.strip()} · {domain.split('.')[0]}",
                            embed_url=f"https://{sub or 'tedesco'}.{domain}/{path}", site=NAME)
            for path, sub, label in _BUTTON.findall(page) for domain in domains]
