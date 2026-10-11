"""Last-resort resolver: load an embed page in Chrome and capture the stream URLs it requests.

Headless first; if that finds nothing, one retry in a visible Chrome placed
off-screen (some players detect headless). Callers skip this while a stream
is playing, so no window pops over the video.
"""
import logging
import re
import threading
import time

from _streamer.net import SEGMENT_RE
from _streamer.settings import HEADED_FALLBACK, HEADERS

log = logging.getLogger(__name__)

_slots = threading.Semaphore(3)  # each sniff launches its own Chrome
_SNIFF_SECS = 15                 # wait this long for a stream request after page load
_STREAM = re.compile(r"\.m3u8|\.mpd|/hls/|/live/", re.I)
_CLICK_PLAY = [
    "document.querySelector('video')?.play()",
    "document.querySelector('[class*=\"play\" i]')?.click()",
    "document.elementFromPoint(window.innerWidth*0.35, window.innerHeight*0.5)?.click()",
]


def sniff(url: str) -> list[str]:
    """Stream URLs the page (or its pop-ups) requested, m3u8s first; [] if none or no Chrome."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []
    with _slots:
        try:
            with sync_playwright() as p:
                found = _sniff(p, url, headless=True)
                if not found and HEADED_FALLBACK:
                    log.info("headless sniff found nothing — retrying in visible Chrome")
                    found = _sniff(p, url, headless=False)
        except Exception as exc:
            log.warning(f"browser sniff failed (is Google Chrome installed?): {exc}")
            return []
    m3u8s = [u for u in found if ".m3u8" in u]
    return list(dict.fromkeys(m3u8s + found))


def _sniff(p, url: str, headless: bool) -> list[str]:
    args = ["--autoplay-policy=no-user-gesture-required",
            "--disable-blink-features=AutomationControlled"]
    if not headless:
        args += ["--window-position=5000,5000", "--window-size=1280,720"]
    browser = p.chromium.launch(headless=headless, channel="chrome", args=args)
    found: list[str] = []

    def capture(r) -> None:
        if r.url.startswith("http") and _STREAM.search(r.url) and not SEGMENT_RE.search(r.url):
            found.append(r.url)

    def click_play(pg) -> None:
        for js in _CLICK_PLAY:
            try:
                pg.evaluate(js)
            except Exception:
                pass

    def watch(pg) -> None:
        pg.on("request", capture)
        pg.on("response", capture)

    def on_popup(pg) -> None:  # some players open the stream in a pop-up
        watch(pg)
        try:
            pg.wait_for_load_state("domcontentloaded", timeout=10_000)
            pg.wait_for_timeout(1_500)
            click_play(pg)
        except Exception:
            pass

    try:
        ctx = browser.new_context(viewport={"width": 1280, "height": 720},
                                  user_agent=HEADERS["User-Agent"])
        page = ctx.new_page()
        watch(page)
        ctx.on("page", on_popup)
        try:
            page.goto(url, timeout=20_000, wait_until="domcontentloaded")
        except Exception:
            pass
        page.wait_for_timeout(2_000)
        click_play(page)
        deadline = time.monotonic() + _SNIFF_SECS
        while not found and time.monotonic() < deadline:
            page.wait_for_timeout(500)
    finally:
        browser.close()
    return found
