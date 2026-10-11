import base64
import json
import logging
import re
import time
import urllib.parse as urlparse
from typing import Callable

from _streamer import browser, cache
from _streamer.models import StreamCandidate
from _streamer.net import check_stream, http_get, origin

log = logging.getLogger(__name__)
_M3U8 = re.compile(r"""https?://[^\s"'<>\\]+?\.m3u8[^\s"'<>\\]*""")
_IFRAME = re.compile(r"""<iframe[^>]+src=["']([^"']+)["']""", re.I)
_MAX_TRY = 5  # validate at most this many found URLs per step


def _page(url: str) -> str:
    return http_get(url, timeout=6, headers={"Referer": url}).text


def _streamfree(url: str) -> str | None:
    """strmfree.st: the page embeds the playlist path pieces and a signed token."""
    t = _page(url)
    quality = re.search(r"const QUALITY = '([^']*)'", t).group(1)
    suffix = re.search(r"const SOURCE_SUFFIX = '([^']*)'", t).group(1)
    key = re.search(r"const VARIANT = '([^']*)'", t).group(1)
    tok = json.loads(re.search(r"const _0x = (\{.*?\});", t, re.S).group(1))[quality]
    return (f"{origin(url)}/live/{key}{quality}{suffix}/index.m3u8"
            f"?_t={tok['_t']}&_e={tok['_e']}&_n={tok['_n']}")


def _exmxbxe(url: str) -> str | None:
    """exmxbxe.cfd (TimStreams): player JS is an XOR/shift-encoded byte array."""
    m = re.search(r"var \w+=\[([\d,]+)\],\w+=(\d+),\w+=(\d+)", _page(url))
    js = "".join(chr(((int(v) ^ int(m[2])) - int(m[3])) % 256) for v in m[1].split(","))
    hit = _M3U8.search(js)
    return hit.group(0) if hit else None


def _cdnlive(url: str) -> str | None:
    """cdnlivetv.tv player: the playlist URL is split into base64 vars joined by a decoder call."""
    t = _page(url)
    parts = dict(re.findall(r"var (\w+)='([\w-]+)';", t))
    call = re.search(r"=((\w+)\(\w+\)(?:\+\2\(\w+\))+);", t)
    return "".join(base64.urlsafe_b64decode(parts[v] + "=" * (-len(parts[v]) % 4)).decode()
                   for v in re.findall(r"\((\w+)\)", call[1]))


def _matchora(url: str) -> str | None:
    """matchora.to: its API restreams a channel on demand; poll until it's warm."""
    ch, event = re.search(r"/embed/channel/(\d+)\?fx=(\w+)", url).groups()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        r = http_get(f"{origin(url)}/api/play/{ch}?fx={event}", timeout=6).json()
        if r.get("reason") not in (None, "warming"):
            return None  # dead (black/no picture), capacity or unknown
        if r.get("ready") and r.get("url"):
            return r["url"]
        time.sleep(2)
    return None


# Hosts whose playlist URL can be extracted without a browser.
_HTTP_RESOLVERS = {"strmfree.st": _streamfree, "exmxbxe.cfd": _exmxbxe,
                   "cdnlivetv.tv": _cdnlive, "matchora.to": _matchora}


def _scan(url: str, depth: int = 2) -> list[tuple[str, str]]:
    """m3u8 URLs in a page's HTML, else in its iframes, each paired with the page it was
    found on (the Referer the CDN usually expects)."""
    html = _page(url)
    if hits := _M3U8.findall(html):
        return [(u, url) for u in dict.fromkeys(hits)]
    for src in _IFRAME.findall(html)[:5] if depth else []:
        src = urlparse.urljoin(url, src)
        try:
            if src.startswith("http") and (found := _scan(src, depth - 1)):
                return found
        except Exception as exc:
            log.debug(f"iframe {src[:60]}: {exc}")
    return []


def _steps(url: str, no_browser: bool) -> list[tuple[str, Callable[[], list[tuple[str, str]]]]]:
    """(name, finder) pairs to try in order; a finder returns (m3u8 URL, found-on page) pairs.

    Known hosts are resolved over plain HTTP; anything else falls back to a
    scan of the page HTML and then a browser (skipped if no_browser).
    """
    if ".m3u8" in url:
        return [("direct", lambda: [(url, url)])]
    fn = _HTTP_RESOLVERS.get(urlparse.urlparse(url).netloc.removeprefix("www."))
    if fn:
        return [(fn.__name__.strip("_"), lambda: [(m, url)] if (m := fn(url)) else [])]
    steps = [("html", lambda: _scan(url))]
    if not no_browser:
        steps.append(("browser", lambda: [(m, url) for m in browser.sniff(url)]))
    return steps


def _first_playable(found: list[tuple[str, str]], embed_url: str) -> tuple[str, str, int] | None:
    """First (url, referer, latency ms) that passes a stream check. Tries the page the URL
    was found on, the embed page and the stream's own origin as Referer."""
    for url, page in found[:_MAX_TRY]:
        for ref in dict.fromkeys(r for r in (page, embed_url, origin(url)) if r):
            try:
                return url, ref, check_stream(url, ref)
            except Exception as exc:
                log.debug(f"check {url[:60]} (ref {ref[:40]}): {exc}")
    return None


def resolve(c: StreamCandidate, no_browser: bool = False) -> tuple[str, str, int] | None:
    """Return a validated (m3u8 URL, referer, latency ms) for a candidate, or None.

    A disk-cached URL from an earlier run is tried first; new results are cached.
    """
    url = c.embed_url
    if (hit := cache.get(url)) and (ok := _first_playable([hit], url)):
        log.info(f"  [{c.label}] resolved via cache")
        return ok
    for name, find in _steps(url, no_browser):
        try:
            found = find()
        except Exception as exc:
            log.debug(f"  [{c.label}] {name}: {exc}")
            continue
        if ok := _first_playable(found, url):
            cache.put(url, ok[0], ok[1])
            log.info(f"  [{c.label}] resolved via {name}")
            return ok
        if found:
            log.debug(f"  [{c.label}] {name}: found URL(s) failed validation")
    return None
