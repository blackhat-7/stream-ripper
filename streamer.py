#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "requests>=2.31.0",
#   "m3u8>=3.0.0",
#   "beautifulsoup4>=4.12.0",
#   "lxml>=5.0.0",
#   "yt-dlp>=2024.1.0",
#   "pyyaml>=6.0",
#   "curl-cffi>=0.7.0",
#   "playwright>=1.40.0",
# ]
# ///
"""
Football Stream Aggregator
──────────────────────────
Automatically finds live football matches and plays the best available stream.
Monitors all sources in the background and switches if the current one dies.

Usage:
  uv run streamer.py                  # show all live football matches
  uv run streamer.py "liverpool"      # filter by team/competition name
  uv run streamer.py --list           # list matches and exit

Requirements:
  brew install mpv
"""

import http.server
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse as _urlparse
from dataclasses import dataclass, field
from typing import Optional

import m3u8
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cf  # browser-like TLS fingerprint (bypasses Cloudflare)

# ── Settings ──────────────────────────────────────────────────────────────────
CHECK_EVERY   = 20     # seconds between background health checks
FAIL_LIMIT    = 2      # consecutive failures → force re-resolve
MAX_RESOLVES  = 3      # give up re-resolving a stream after this many attempts
REQ_TIMEOUT   = 8      # HTTP timeout
RESOLVE_TIMEOUT = 30   # yt-dlp / streamlink timeout
MPV_SOCKET    = "/tmp/mpv-football.sock"
# Switch only if candidate is significantly better (avoids flip-flopping)
SWITCH_MARGIN = 0.70
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Playwright (Chrome) can only run one instance at a time reliably
_playwright_lock = threading.Lock()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Shared curl_cffi session — maintains cookies across requests (helps with Cloudflare)
_cf_session = cf.Session(impersonate="chrome")


def http_get(url: str, timeout: int = REQ_TIMEOUT, retries: int = 3, **kwargs):
    """GET with retries using browser TLS fingerprint."""
    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(retries):
        try:
            return _cf_session.get(url, timeout=timeout, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(1.5 ** attempt)
    raise last_exc


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class StreamCandidate:
    """One potential stream URL for a match, from one source site."""
    label: str          # e.g. "streamed.pk · admin · HD"
    embed_url: str      # the embed page URL (needs resolution)
    resolved: str = ""  # raw .m3u8 or playable URL after resolution
    alive: bool = False
    latency_ms: int = 9999
    failures: int = 0
    resolve_attempts: int = 0
    viewers: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def score(self) -> float:
        if not self.alive:
            return -1.0
        # Prefer HD; within same quality tier prefer low latency and high viewers
        hd_bonus = 2.0 if "· HD" in self.label else 1.0
        return hd_bonus * (1_000_000.0 / (self.latency_ms + 1)) + self.viewers * 0.001


@dataclass
class LiveMatch:
    id: str
    title: str
    source_site: str
    raw: dict  # full API/scrape payload for lazy stream loading


# ── Match discovery ───────────────────────────────────────────────────────────

def fetch_streamed_pk(query: str) -> list[LiveMatch]:
    """
    Primary source: streamed.pk public JSON API.
    Returns live football matches, optionally filtered by query.
    """
    try:
        resp = http_get("https://streamed.pk/api/matches/live")
        resp.raise_for_status()
        all_matches = resp.json()
    except Exception as exc:
        log.warning(f"streamed.pk: {exc}")
        return []

    football = [m for m in all_matches if m.get("category") == "football"]

    if query:
        words = query.lower().split()
        football = [
            m for m in football
            if any(w in m.get("title", "").lower() for w in words)
        ]

    return [
        LiveMatch(
            id=m["id"],
            title=m.get("title", m["id"]),
            source_site="streamed.pk",
            raw=m,
        )
        for m in football
    ]


def fetch_streameast(query: str) -> list[LiveMatch]:
    """
    Fallback scraper: gostreameast.is
    Scrapes .match-row elements from the HTML listing.
    """
    domains = ["https://gostreameast.is", "https://thestreameast.fun"]
    for base in domains:
        try:
            resp = http_get(base)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            matches = []
            for row in soup.select(".match-row"):
                link = row.select_one(".match-name, a[href]")
                if not link:
                    continue
                title = link.get_text(strip=True)
                href = link.get("href", "")
                cat = row.get("data-category", "")
                if cat not in ("football", "soccer", ""):
                    continue
                if query:
                    words = query.lower().split()
                    if not any(w in title.lower() for w in words):
                        continue
                matches.append(LiveMatch(
                    id=href,
                    title=title,
                    source_site=f"streameast ({base})",
                    raw={"url": href, "base": base},
                ))
            if matches:
                return matches
        except Exception as exc:
            log.debug(f"streameast {base}: {exc}")
    return []


def fetch_sportsurge(query: str) -> list[LiveMatch]:
    """
    Fallback scraper: sportsurge.ws
    """
    domains = ["https://sportsurge.ws", "https://sportsurge.net", "https://sportsurge.uno"]
    for base in domains:
        try:
            resp = http_get(base)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            matches = []
            # Links matching /watch/[league]/[teams]/[id] pattern
            for a in soup.find_all("a", href=re.compile(r"/watch/")):
                title = a.get_text(" ", strip=True)
                href = a["href"]
                if not title:
                    continue
                if query:
                    words = query.lower().split()
                    if not any(w in title.lower() for w in words):
                        continue
                full_url = href if href.startswith("http") else base + href
                matches.append(LiveMatch(
                    id=href,
                    title=title,
                    source_site=f"sportsurge ({base})",
                    raw={"url": full_url},
                ))
            if matches:
                return matches
        except Exception as exc:
            log.debug(f"sportsurge {base}: {exc}")
    return []


def discover_matches(query: str) -> list[LiveMatch]:
    """Run all discovery sources in parallel, deduplicate by title."""
    results: list[LiveMatch] = []
    lock = threading.Lock()

    def run(fn):
        found = fn(query)
        with lock:
            results.extend(found)

    threads = [
        threading.Thread(target=run, args=(fn,), daemon=True)
        for fn in [fetch_streamed_pk, fetch_streameast, fetch_sportsurge]
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Deduplicate: keep first occurrence of each title (normalized)
    seen: set[str] = set()
    unique: list[LiveMatch] = []
    for m in results:
        key = re.sub(r"\s+", " ", m.title.lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(m)

    return unique


# ── Stream candidate loading ──────────────────────────────────────────────────

def load_candidates_streamed_pk(match: LiveMatch) -> list[StreamCandidate]:
    """Fetch all stream sources for a match from the streamed.pk API."""
    candidates = []
    sources = match.raw.get("sources", [])

    def fetch_source(src):
        source_name = src.get("source", "?")
        source_id   = src.get("id", "")
        try:
            resp = http_get(f"https://streamed.pk/api/stream/{source_name}/{source_id}")
            if resp.status_code != 200:
                return
            streams = resp.json()
            for s in streams if isinstance(streams, list) else []:
                embed = s.get("embedUrl", "")
                if not embed:
                    continue
                hd      = s.get("hd", False)
                viewers = s.get("viewers", 0)
                lang    = s.get("language", "")
                hd_tag  = "HD" if hd else "SD"
                lang_tag = f" · {lang}" if lang and lang.lower() != "english" else ""
                stream_no = s.get("streamNo", len(candidates) + 1)
                label   = f"{source_name} #{stream_no} · {hd_tag}{lang_tag}"
                candidates.append(StreamCandidate(
                    label=label,
                    embed_url=embed,
                    viewers=viewers,
                ))
        except Exception as exc:
            log.debug(f"load_candidates {source_name}: {exc}")

    threads = [threading.Thread(target=fetch_source, args=(s,), daemon=True) for s in sources]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Sort by viewer count descending (more viewers = more likely to work)
    candidates.sort(key=lambda c: c.viewers, reverse=True)
    return candidates


def load_candidates_page(match: LiveMatch) -> list[StreamCandidate]:
    """For HTML-scraped matches, return a single candidate from the match page URL."""
    url = match.raw.get("url", match.id)
    return [StreamCandidate(label=match.source_site, embed_url=url)]


def load_candidates(match: LiveMatch) -> list[StreamCandidate]:
    if match.source_site == "streamed.pk":
        return load_candidates_streamed_pk(match)
    return load_candidates_page(match)


# ── Stream URL resolution ─────────────────────────────────────────────────────

def resolve_with_playwright(url: str) -> Optional[str]:
    """
    Load the embed page in a real Chrome window (off-screen) and intercept
    the first .m3u8 network request. Requires Chrome to be installed:
      brew install --cask google-chrome
    The browser window appears briefly (~3-8s) off-screen then closes.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    found: list[str] = []

    # Serialize Chrome windows: opening multiple simultaneously causes failures
    with _playwright_lock:
        try:
            with sync_playwright() as p:
                try:
                    # Real Chrome required — streaming sites use WASM bot detection
                    # that blocks headless/Chromium. Chrome passes these checks.
                    browser = p.chromium.launch(
                        headless=False,
                        channel="chrome",
                        args=[
                            "--autoplay-policy=no-user-gesture-required",
                            "--window-position=5000,5000",  # off-screen
                            "--window-size=1280,720",
                        ],
                    )
                except Exception:
                    log.warning(
                        "Chrome not found — install with: brew install --cask google-chrome"
                    )
                    return None

                ctx  = browser.new_context(viewport={"width": 1280, "height": 720})
                page = ctx.new_page()

                def on_url(u: str):
                    if ".m3u8" in u and u.startswith("http"):
                        found.append(u)

                page.on("request",  lambda r: on_url(r.url))
                page.on("response", lambda r: on_url(r.url))

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
            if not re.search(r"\d+\.ts|seg-\d|\.aac|\.mp4", u):
                return u
        return found[0]

    return None


def resolve_with_ytdlp(url: str) -> Optional[str]:
    """Use yt-dlp to extract the best stream URL from an embed page."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--get-url", "--no-warnings", "--no-playlist", "-f", "best", url],
            capture_output=True,
            text=True,
            timeout=RESOLVE_TIMEOUT,
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("http"):
                return line
    except FileNotFoundError:
        log.debug("yt-dlp not found")
    except subprocess.TimeoutExpired:
        log.debug(f"yt-dlp timed out: {url}")
    except Exception as exc:
        log.debug(f"yt-dlp: {exc}")
    return None


def resolve_direct_m3u8(url: str) -> Optional[str]:
    """Fetch page HTML and scan for .m3u8 URLs in JS variables."""
    try:
        resp = http_get(url)
        html = resp.text
        hits = re.findall(r"""["'](https?://[^"']*\.m3u8[^"']*?)["']""", html)
        if hits:
            for h in hits:
                if any(kw in h for kw in ("live", "stream", "hls", "playlist")):
                    return h
            return hits[0]
    except Exception as exc:
        log.debug(f"direct m3u8 scan: {exc}")
    return None


def resolve_with_streamlink(url: str) -> Optional[str]:
    """Use streamlink to extract the stream URL."""
    try:
        result = subprocess.run(
            ["streamlink", "--stream-url", url, "best"],
            capture_output=True, text=True, timeout=RESOLVE_TIMEOUT,
        )
        out = result.stdout.strip()
        if out.startswith("http"):
            return out
    except Exception as exc:
        log.debug(f"streamlink: {exc}")
    return None


def resolve(candidate: StreamCandidate) -> Optional[str]:
    """Try all resolvers in order; return first working URL."""
    url = candidate.embed_url

    if ".m3u8" in url:
        return url

    log.info(f"  [{candidate.label}] resolving…")

    # Playwright first: most reliable for JS-rendered embed pages.
    # Falls back to yt-dlp, regex scan, and streamlink.
    for resolver in [
        resolve_with_playwright,
        resolve_with_ytdlp,
        resolve_direct_m3u8,
        resolve_with_streamlink,
    ]:
        result = resolver(url)
        if result:
            log.info(f"  [{candidate.label}] resolved via {resolver.__name__.split('_with_')[-1]}")
            return result

    return None


# ── Health probe ──────────────────────────────────────────────────────────────

def probe(c: StreamCandidate, resolve_if_dead: bool = True) -> bool:
    """Health-check one stream candidate. Updates c in-place.

    resolve_if_dead=False: skip Playwright/yt-dlp re-resolution — only verify
    streams that already have a URL. Use this during background health checks
    so Chrome windows never interfere with active playback.
    """
    if not c.resolved:
        if not resolve_if_dead:
            return False  # don't launch Playwright in background
        with c._lock:
            if c.resolve_attempts >= MAX_RESOLVES:
                return False  # gave up on this stream
            c.resolve_attempts += 1
        url = resolve(c)
        if not url:
            with c._lock:
                c.alive = False
                c.failures += 1
            return False
        with c._lock:
            c.resolved = url
    else:
        url = c.resolved

    # Add Referer from embed URL so CDN doesn't block
    h = {**HEADERS, "Referer": c.embed_url, "Origin": _origin(c.embed_url)}

    try:
        t0 = time.monotonic()
        resp = requests.get(url, headers=h, timeout=REQ_TIMEOUT)
        manifest_ms = int((time.monotonic() - t0) * 1000)

        if resp.status_code != 200:
            raise ValueError(f"manifest HTTP {resp.status_code}")

        pl = m3u8.loads(resp.text, uri=url)

        if pl.is_variant and pl.playlists:
            best_variant = pl.playlists[-1].absolute_uri
            r2 = requests.get(best_variant, headers=h, timeout=REQ_TIMEOUT)
            if r2.status_code != 200:
                raise ValueError(f"variant HTTP {r2.status_code}")
            pl = m3u8.loads(r2.text, uri=best_variant)

        if not pl.segments:
            raise ValueError("no segments")

        seg_url = pl.segments[-1].absolute_uri
        t1 = time.monotonic()
        sr = requests.get(seg_url, headers=h, timeout=REQ_TIMEOUT, stream=True)
        next(sr.iter_content(8192), None)
        seg_ms = int((time.monotonic() - t1) * 1000)

        if sr.status_code != 200:
            raise ValueError(f"segment HTTP {sr.status_code}")

        total_ms = manifest_ms + seg_ms
        with c._lock:
            c.alive = True
            c.latency_ms = total_ms
            c.failures = 0
            c.resolve_attempts = 0
        log.info(f"  ✓ [{c.label}]  {total_ms}ms")
        return True

    except Exception as exc:
        with c._lock:
            c.alive = False
            c.failures += 1
            if c.failures >= FAIL_LIMIT:
                c.resolved = ""  # force re-resolve next round
        log.warning(f"  ✗ [{c.label}]  {exc}")
        return False


def _origin(url: str) -> str:
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


class HLSProxy:
    """
    Tiny local HTTP proxy on localhost that injects Referer/Origin headers
    into every CDN request (manifest, variant playlists, segments).
    All URLs inside m3u8 files are rewritten to route through this proxy,
    so mpv never touches the CDN directly and never gets a 403.
    """

    def __init__(self, referrer: str):
        self.referrer = referrer
        self.origin   = _origin(referrer)
        self.port     = self._free_port()
        self._server: Optional[http.server.HTTPServer] = None
        self._session = requests.Session()  # reuse TCP connections to CDN
        self._start()

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def _start(self):
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass  # silence access logs

            def do_GET(self):
                qs       = _urlparse.urlparse(self.path).query
                real_url = _urlparse.parse_qs(qs).get("u", [None])[0]
                if not real_url:
                    self.send_error(400, "Missing ?u=")
                    return

                req_h = {
                    **HEADERS,
                    "Referer": proxy.referrer,
                    "Origin":  proxy.origin,
                }
                try:
                    is_m3u8 = real_url.split("?")[0].endswith(".m3u8")
                    resp = proxy._session.get(
                        real_url, headers=req_h, timeout=REQ_TIMEOUT,
                        stream=not is_m3u8,
                    )
                    ct = resp.headers.get("content-type", "application/octet-stream")
                    if "mpegurl" in ct or is_m3u8:
                        # Small text file — buffer for URL rewriting
                        body = proxy._rewrite(resp.text, real_url).encode()
                        self.send_response(resp.status_code)
                        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    else:
                        # Binary segment — stream directly so mpv doesn't wait
                        self.send_response(resp.status_code)
                        self.send_header("Content-Type", ct)
                        if "content-length" in resp.headers:
                            self.send_header("Content-Length", resp.headers["content-length"])
                        self.end_headers()
                        for chunk in resp.iter_content(65536):
                            self.wfile.write(chunk)
                except Exception as exc:
                    log.warning(f"proxy: {exc}")
                    self.send_error(502)

        self._server = http.server.ThreadingHTTPServer(("localhost", self.port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None

    def _rewrite(self, text: str, base_url: str) -> str:
        """Rewrite all URLs in an m3u8 to route through this proxy."""
        out = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                abs_url = stripped if stripped.startswith("http") else _urlparse.urljoin(base_url, stripped)
                line = self._wrap(abs_url)
            else:
                line = re.sub(
                    r'URI="([^"]*)"',
                    lambda m: f'URI="{self._wrap(_urlparse.urljoin(base_url, m.group(1)))}"',
                    line,
                )
            out.append(line)
        return "\n".join(out)

    def _wrap(self, cdn_url: str) -> str:
        enc = _urlparse.quote(cdn_url, safe="")
        return f"http://localhost:{self.port}/?u={enc}"

    def mpv_url(self, cdn_url: str) -> str:
        return self._wrap(cdn_url)


def probe_all(candidates: list[StreamCandidate], resolve_if_dead: bool = True):
    threads = [
        threading.Thread(target=probe, args=(c, resolve_if_dead), daemon=True)
        for c in candidates
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# ── mpv controller ────────────────────────────────────────────────────────────

class MpvController:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self.proc: Optional[subprocess.Popen] = None
        self._proxy: Optional[HLSProxy] = None

    def _kill_existing(self):
        if self._proxy:
            self._proxy.stop()
            self._proxy = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

    def launch(self, url: str, title: str = "Football Streamer", referrer: str = ""):
        self._kill_existing()
        mpv_base = [
            "mpv",
            "--cache=yes",
            "--demuxer-readahead-secs=20",  # buffer 20s to absorb proxy jitter
            f"--input-ipc-server={self.socket_path}",
            f"--title=⚽ {title}",
            "--force-window=yes",
            "--ytdl=no",
        ]
        if referrer:
            # Local HTTP proxy rewrites all m3u8 URLs and injects Referer/Origin
            # on every request — mpv never touches the CDN directly.
            self._proxy = HLSProxy(referrer)
            play_url = self._proxy.mpv_url(url)
            log.info(f"proxy|mpv → {url[:72]}")
            log.info(f"  referrer: {referrer[:80]}")
        else:
            play_url = url
            log.info(f"mpv → {url[:80]}")
        self.proc = subprocess.Popen(mpv_base + [play_url])
        self._wait_for_socket()

    def _wait_for_socket(self, timeout: float = 12.0):
        """Wait until the IPC socket is actually connectable, not just present."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(self.socket_path):
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                        s.settimeout(1)
                        s.connect(self.socket_path)
                    return  # connection succeeded — socket is ready
                except OSError:
                    pass
            time.sleep(0.1)

    def _send(self, command: list) -> bool:
        try:
            msg = (json.dumps({"command": command}) + "\n").encode()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(3)
                s.connect(self.socket_path)
                s.sendall(msg)
            return True
        except Exception as exc:
            log.debug(f"mpv IPC: {exc}")
            return False

    def switch(self, url: str, title: str = "", referrer: str = ""):
        """Switch to a new stream. Restarts mpv so headers are applied to all HLS requests."""
        # IPC loadfile cannot pass --stream-lavf-o headers, so we always restart.
        self.launch(url, title, referrer)

    def osd(self, text: str, ms: int = 4000):
        self._send(["show-text", text, ms])

    def is_alive(self) -> bool:
        if self.proc is None or self.proc.poll() is not None:
            return False
        return True


# ── UI helpers ────────────────────────────────────────────────────────────────

def print_status(candidates: list[StreamCandidate], current: Optional[StreamCandidate]):
    print("\n┌─ Streams " + "─" * 50)
    for c in candidates:
        playing = "▶" if (current and c.label == current.label) else " "
        ok      = "✓" if c.alive else "✗"
        latency = f"{c.latency_ms}ms" if c.alive else "dead"
        viewers = f"  ({c.viewers} viewers)" if c.viewers else ""
        print(f"│ {playing} {ok}  {c.label:<40} {latency}{viewers}")
    print("└" + "─" * 60 + "\n")


def pick_match(matches: list[LiveMatch]) -> LiveMatch:
    if len(matches) == 1:
        log.info(f"One match found: {matches[0].title}")
        return matches[0]

    print("\n  Live football matches:\n")
    for i, m in enumerate(matches, 1):
        print(f"  [{i:2}] {m.title}  — {m.source_site}")
    print()

    while True:
        try:
            raw = input("  Select match (number): ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(matches):
                return matches[idx]
        except (ValueError, KeyboardInterrupt):
            sys.exit(0)
        print("  Invalid selection.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    query = " ".join(a for a in args if not a.startswith("--"))

    # ── Discover live matches ────────────────────────────────────────────────
    log.info("Finding live football matches…")
    matches = discover_matches(query)

    if not matches:
        msg = f"No live football matches found"
        if query:
            msg += f" for '{query}'"
        sys.exit(msg + ". Try again closer to kick-off.")

    if "--list" in args:
        for m in matches:
            print(f"  {m.title}  [{m.source_site}]")
        return

    match = pick_match(matches)
    print(f"\n  ⚽  {match.title}\n")

    # ── Load stream candidates ───────────────────────────────────────────────
    log.info("Loading stream sources…")
    candidates = load_candidates(match)

    if not candidates:
        sys.exit("No stream sources found for this match.")

    log.info(f"Found {len(candidates)} source(s). Resolving streams…")

    # ── Eager startup: probe candidates one by one, launch as soon as one works ──
    # Remaining candidates are probed in the background after playback starts.
    mpv     = MpvController(MPV_SOCKET)
    current: Optional[StreamCandidate] = None

    for c in candidates:
        probe(c)
        if c.alive:
            current = c
            mpv.launch(c.resolved, title=match.title, referrer=c.embed_url)
            mpv.osd(f"▶ {c.label}  ({c.latency_ms}ms)")
            log.info(f"Playing: {c.label}")
            break

    if current is None:
        sys.exit("No working streams found. Try again or pick a different match.")

    print_status(candidates, current)

    # ── Background monitor ───────────────────────────────────────────────────
    def monitor():
        nonlocal current

        while True:
            time.sleep(CHECK_EVERY)

            if not mpv.is_alive():
                log.warning("mpv closed — re-launching")
                if current and current.resolved:
                    mpv.launch(current.resolved, title=match.title, referrer=current.embed_url)
                continue

            log.info("Health check…")
            # Never probe the current stream independently — the CDN rejects a
            # second concurrent connection while the proxy is already fetching,
            # causing false "stream died" detections even when mpv is playing fine.
            # Trust mpv.is_alive() for the current stream's health.
            others = [c for c in candidates if c is not current]
            probe_all(others, resolve_if_dead=False)
            if current:
                current.alive = True  # mpv is alive (checked above), so stream is fine
            alive_now = [c for c in candidates if c.alive]
            print_status(candidates, current)

            if current is None:
                continue

            candidate_alive = max(alive_now, key=lambda c: c.score()) if alive_now else None
            current_dead = not current.alive
            much_better = (
                candidate_alive is not None
                and candidate_alive is not current
                and candidate_alive.latency_ms < current.latency_ms * SWITCH_MARGIN
            )

            if current_dead:
                log.warning("Current stream died — finding replacement…")
                # Now we may use Playwright to resolve a replacement
                unresolved = [c for c in candidates if c is not current and not c.resolved]
                if unresolved:
                    probe_all(unresolved, resolve_if_dead=True)
                alive_now = [c for c in candidates if c.alive]
                candidate_alive = max(alive_now, key=lambda c: c.score()) if alive_now else None

            if (current_dead or much_better) and candidate_alive:
                candidate = candidate_alive
                reason = (
                    f"{current.label} died"
                    if current_dead
                    else f"{candidate.latency_ms}ms vs {current.latency_ms}ms"
                )
                log.info(f"Switch: {current.label} → {candidate.label}  ({reason})")
                mpv.switch(candidate.resolved, title=match.title, referrer=candidate.embed_url)
                mpv.osd(f"↷ {candidate.label}  ({candidate.latency_ms}ms)")
                current = candidate
            elif current_dead and not candidate_alive:
                log.warning("All streams dead — waiting…")

    threading.Thread(target=monitor, daemon=True).start()

    log.info(
        f"Monitoring {len(candidates)} stream(s) every {CHECK_EVERY}s. "
        "Close mpv or press Ctrl+C to quit."
    )

    def shutdown(*_):
        log.info("Shutting down.")
        if mpv.proc:
            mpv.proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while mpv.is_alive():
        time.sleep(1)
    log.info("mpv closed — done.")


if __name__ == "__main__":
    main()
