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


def resolve(c: StreamCandidate) -> str | None:
    """Try all resolvers in order; return first working URL."""
    if ".m3u8" in c.embed_url:
        return c.embed_url
    log.info(f"  [{c.label}] resolving…")
    for fn in [resolve_with_playwright, resolve_with_ytdlp,
               resolve_direct_m3u8, resolve_with_streamlink]:
        if result := fn(c.embed_url):
            log.info(f"  [{c.label}] resolved via {fn.__name__.split('_with_')[-1]}")
            return result
    return None
