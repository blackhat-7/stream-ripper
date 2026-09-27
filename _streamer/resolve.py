import base64
import json
import logging
import re
import threading
import time
import urllib.parse as urlparse

from _streamer.models import StreamCandidate
from _streamer.net import http_get, origin
from _streamer.settings import HEADERS

log = logging.getLogger(__name__)
_browsers = threading.Semaphore(3)  # each resolve launches its own headless Chrome
_M3U8 = re.compile(r"""https?://[^\s"'<>\\]+?\.m3u8[^\s"'<>\\]*""")


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
    ch, event = re.search(r"/embed/channel/(\d+)\?fx=(\d+)", url).groups()
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


def _html(url: str) -> str | None:
    """Embed pages that put the playlist URL straight in their HTML."""
    hit = _M3U8.search(_page(url))
    return hit.group(0) if hit else None


def _browser(url: str, wait_s: float = 15) -> str | None:
    """Load the embed in headless Chrome and capture the first .m3u8 request it makes."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    found: list[str] = []
    with _browsers:
        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(
                        headless=True, channel="chrome",
                        args=["--autoplay-policy=no-user-gesture-required"])
                except Exception:
                    log.warning("Chrome not found — install Google Chrome")
                    return None
                ctx = browser.new_context(user_agent=HEADERS["User-Agent"])
                ctx.on("request", lambda r: found.append(r.url) if ".m3u8" in r.url else None)
                page = ctx.new_page()
                ctx.on("page", lambda pg: pg.close())  # kill ad pop-ups
                try:
                    page.goto(url, timeout=20_000, wait_until="domcontentloaded")
                except Exception:
                    pass
                deadline = time.monotonic() + wait_s
                while not found and time.monotonic() < deadline:
                    page.wait_for_timeout(300)
                browser.close()
        except Exception as exc:
            log.debug(f"playwright: {exc}")
    return found[0] if found else None


def resolve(c: StreamCandidate, no_browser: bool = False) -> str | None:
    """Return a playable m3u8 URL for a candidate, or None.

    Known hosts are resolved over plain HTTP; anything else falls back to a
    scan of the page HTML and then headless Chrome (skipped if no_browser).
    """
    url = c.embed_url
    if ".m3u8" in url:
        return url
    host = urlparse.urlparse(url).netloc.removeprefix("www.")
    if host in _HTTP_RESOLVERS:
        fns = [_HTTP_RESOLVERS[host]]
    else:
        fns = [_html] + ([] if no_browser else [_browser])
    for fn in fns:
        try:
            result = fn(url)
        except Exception as exc:
            log.debug(f"  [{c.label}] {fn.__name__}: {exc}")
            continue
        if result:
            log.info(f"  [{c.label}] resolved via {fn.__name__.strip('_')}")
            return result
    return None
