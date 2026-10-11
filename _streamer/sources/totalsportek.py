"""totalsportek — Next.js listing; stream links sit in each event page's RSC payload."""
import re
import urllib.parse as urlparse

from bs4 import BeautifulSoup

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.net import get_first, http_get
from _streamer.sources import fmhy

NAME = "totalsportek"
_MIRRORS = ["https://total-sportekk.st"]
_PREROLL = 30 * 60  # upcoming events show "Starts in: HH:MM:SS"; keep the last 30 min
_COUNTDOWN = re.compile(r"Starts in:\s*(\d+):(\d+):(\d+)")


def fetch_matches() -> list[LiveMatch]:
    resp = get_first([m + "/football" for m in fmhy.mirrors(NAME, _MIRRORS)])
    out, seen = [], set()
    for a in BeautifulSoup(resp.text, "lxml").select('a[href^="/events/"]'):
        url = urlparse.urljoin(resp.url, a["href"])
        if url in seen:
            continue
        seen.add(url)
        if (m := _COUNTDOWN.search(a.get_text(" ", strip=True))) and \
                int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) > _PREROLL:
            continue
        slug = url.rstrip("/").split("/")[-1]
        if "-vs-" not in slug:  # anchor text carries countdowns etc; the slug is clean
            continue
        home, _, away = slug.partition("-vs-")
        title = f"{home.replace('-', ' ').title()} vs {away.replace('-', ' ').title()}"
        out.append(LiveMatch(id=url, title=title, source_site=NAME, sport="football", raw={"url": url}))
    return out


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    """Stream links appear in the page's RSC payload ~60 min before kickoff;
    until then the watch page itself is the candidate (resolved generically)."""
    page = http_get(match.raw["url"]).text
    out = []
    # RSC payload double-escapes JSON: \"iframe_link\":\"https://...\"
    for block in re.findall(r"\{[^{}]{0,800}iframe_link[^{}]{0,800}\}", page):
        iframe = re.search(r'iframe_link\\":\\"(https?://[^\\"]+)', block)
        if not iframe:
            continue
        name = re.search(r'"name\\":\\"([^\\"]+)', block)
        qual = re.search(r'quality\\":\\"([^\\"]+)', block)
        label = (name[1] if name else "stream") + (f" · {qual[1]}" if qual else "")
        out.append(StreamCandidate(label=label, embed_url=iframe[1], site=NAME))
    return out or [StreamCandidate(label="totalsportek", embed_url=match.raw["url"], site=NAME)]
