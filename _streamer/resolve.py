import logging
import re
import subprocess
import threading
import time

from _streamer.models import StreamCandidate
from _streamer.net import http_get
from _streamer.settings import RESOLVE_TIMEOUT

log = logging.getLogger(__name__)
_playwright_lock = threading.Lock()


def resolve_with_playwright(url: str) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    found: list[str] = []
    with _playwright_lock:
        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(
                        headless=False, channel="chrome",
                        args=["--autoplay-policy=no-user-gesture-required",
                              "--window-position=5000,5000", "--window-size=1280,720"],
                    )
                except Exception:
                    log.warning("Chrome not found — install: brew install --cask google-chrome")
                    return None

                page = browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
                page.on("request",  lambda r: found.append(r.url) if ".m3u8" in r.url else None)
                page.on("response", lambda r: found.append(r.url) if ".m3u8" in r.url else None)
                try:
                    page.goto(url, timeout=20_000, wait_until="domcontentloaded")
                except Exception:
                    pass
                deadline = time.monotonic() + 15
                while not found and time.monotonic() < deadline:
                    try:
                        page.wait_for_timeout(500)
                    except Exception:
                        break
                browser.close()
        except Exception as exc:
            log.debug(f"playwright: {exc}")

    if found:
        for u in found:
            if u.startswith("http") and not re.search(r"\d+\.ts|seg-\d|\.aac|\.mp4", u):
                return u
        return found[0]
    return None


def resolve_with_ytdlp(url: str) -> str | None:
    try:
        r = subprocess.run(
            ["yt-dlp", "--get-url", "--no-warnings", "--no-playlist", "-f", "best", url],
            capture_output=True, text=True, timeout=RESOLVE_TIMEOUT,
        )
        for line in r.stdout.strip().splitlines():
            if line.strip().startswith("http"):
                return line.strip()
    except FileNotFoundError:
        log.debug("yt-dlp not found")
    except subprocess.TimeoutExpired:
        log.debug(f"yt-dlp timed out: {url}")
    except Exception as exc:
        log.debug(f"yt-dlp: {exc}")
    return None


def resolve_direct_m3u8(url: str) -> str | None:
    try:
        hits = re.findall(r"""["'](https?://[^"']*\.m3u8[^"']*?)["']""", http_get(url).text)
        if hits:
            for h in hits:
                if any(kw in h for kw in ("live", "stream", "hls", "playlist")):
                    return h
            return hits[0]
    except Exception as exc:
        log.debug(f"direct m3u8 scan: {exc}")
    return None


def resolve_with_streamlink(url: str) -> str | None:
    try:
        r = subprocess.run(
            ["streamlink", "--stream-url", url, "best"],
            capture_output=True, text=True, timeout=RESOLVE_TIMEOUT,
        )
        out = r.stdout.strip()
        if out.startswith("http"):
            return out
    except Exception as exc:
        log.debug(f"streamlink: {exc}")
    return None


def resolve_sportsurge_page(url: str) -> str | None:
    """Open a sportsurge watch page in Chrome, click a stream, and capture the m3u8."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    found: list[str] = []
    with _playwright_lock:
        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch(
                        headless=False, channel="chrome",
                        args=["--autoplay-policy=no-user-gesture-required",
                              "--window-position=5000,5000", "--window-size=1280,720"],
                    )
                except Exception:
                    log.warning("Chrome not found — install: brew install --cask google-chrome")
                    return None

                ctx = browser.new_context(viewport={"width": 1280, "height": 720})
                page = ctx.new_page()

                def capture(r):
                    u = r.url
                    if any(x in u for x in (".m3u8", ".mpd", "playlist", "/live/", "/hls/")):
                        if not re.search(r"\.(js|css|png|jpg|gif|svg|ico|woff)", u):
                            found.append(u)

                page.on("request", capture)
                page.on("response", capture)

                try:
                    page.goto(url, timeout=30_000, wait_until="domcontentloaded")
                except Exception:
                    pass

                # Wait for React to render
                page.wait_for_timeout(3_000)

                def _click_play(pg) -> None:
                    """Click a play button on any streaming embed page."""
                    _plays = [
                        "document.querySelector('video')?.play()",
                        "document.querySelector('[class*=\"play\" i]')?.click()",
                        "document.querySelector('[class*=\"Play\"]')?.click()",
                        "document.elementFromPoint(window.innerWidth*0.35, window.innerHeight*0.5)?.click()",
                    ]
                    for js in _plays:
                        try:
                            pg.evaluate(js)
                        except Exception:
                            pass

                # When sportsurge's "Action" link opens a new tab, click play on that page too
                def _on_new_page(pg) -> None:
                    pg.on("request", capture)
                    pg.on("response", capture)
                    try:
                        pg.wait_for_load_state("domcontentloaded", timeout=15_000)
                        pg.wait_for_timeout(2_000)
                        _click_play(pg)
                    except Exception:
                        pass

                ctx.on("page", _on_new_page)

                # Click the FIRST stream link in the Action column (last td of first data row)
                _STREAM_CLICK = (
                    "document.querySelector('table tbody tr td:last-child a, "
                    "table tr:nth-child(2) td:last-child a, "
                    "table tr:nth-child(2) td:last-child button')?.click()"
                )
                try:
                    page.evaluate(_STREAM_CLICK)
                    log.debug("sportsurge: clicked first stream Action link")
                except Exception:
                    # Fallback: click the play button directly on this page
                    _click_play(page)

                # Give everything time to load
                deadline = time.monotonic() + 25
                while not found and time.monotonic() < deadline:
                    try:
                        page.wait_for_timeout(500)
                    except Exception:
                        break

                if not found:
                    log.debug(f"sportsurge: no stream found, page title: {page.title()!r}")
                browser.close()
        except Exception as exc:
            log.debug(f"sportsurge playwright: {exc}")

    if found:
        for u in found:
            if u.startswith("http") and not re.search(r"\d+\.ts|seg-\d|\.aac|\.mp4", u):
                return u
        return found[0]
    return None


def resolve(c: StreamCandidate, no_browser: bool = False) -> str | None:
    """Try all resolvers in order; return first working URL.

    no_browser=True skips playwright/Chrome — use for background probes while
    mpv is already playing to avoid popping windows over the stream.
    """
    if ".m3u8" in c.embed_url:
        return c.embed_url
    log.info(f"  [{c.label}] resolving…")
    if "sportsurge" in c.embed_url:
        if no_browser:
            return None
        if result := resolve_sportsurge_page(c.embed_url):
            log.info(f"  [{c.label}] resolved via sportsurge_page")
            return result
        return None
    fns = [resolve_with_ytdlp, resolve_direct_m3u8, resolve_with_streamlink]
    if not no_browser:
        fns = [resolve_with_playwright] + fns
    for fn in fns:
        if result := fn(c.embed_url):
            log.info(f"  [{c.label}] resolved via {fn.__name__.split('_with_')[-1]}")
            return result
    return None
