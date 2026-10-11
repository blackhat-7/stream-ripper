"""Web front end: a local site that lists live matches and plays them in the page.

The page is the player. Watcher (watch.py) still picks streams and fails over;
BrowserPlayer stands in for mpv by handing Watcher's choice to the page and
reporting back what the page says the video is doing.
"""
import http.server
import json
import logging
import sys
import threading
import time
import urllib.parse as urlparse
import webbrowser
from pathlib import Path
from typing import Any

from _streamer.models import LiveMatch
from _streamer.proxy import HLSProxy
from _streamer.settings import START_TIMEOUT
from _streamer.sources import discover_matches
from _streamer.watch import Watcher

log = logging.getLogger(__name__)

PORT = 8765
PAGE = Path(__file__).with_name("web.html")
MATCHES_TTL = 60  # seconds before the match list is fetched again
STALE_AFTER = 5   # seconds without a poll: the tab is closed or throttled in the background


class BrowserPlayer:
    """The web page as a Player: play() publishes a URL for the page to load; the page
    reports position/pause/failure on every poll, which Watcher reads via get().

    The player reads as paused, so Watcher waits instead of failing over blind, when
    polls stop (tab closed, or a background tab Chrome throttles) and, for up to
    START_TIMEOUT, while a reloaded page restarts the stream that was playing."""

    def __init__(self) -> None:
        self.proxy = HLSProxy()  # also adds CORS, which the page's player needs
        self.url = ""
        self.load_id = 0  # bumps on every play() so the page reloads even an unchanged URL
        self._seen = time.monotonic()
        self._resumed_at = 0.0
        self._report: dict = {}

    def play(self, url: str, referrer: str = "", title: str = "") -> None:
        self.url = self.proxy.wrap(url, referrer)
        self.load_id += 1
        self._report = {}

    def stop(self) -> None:
        self.url = ""
        self.load_id += 1

    def exit_code(self) -> int | None:
        return None  # the page never "exits"; a closed tab just reads as paused

    def get(self, prop: str) -> Any:
        now = time.monotonic()
        if prop == "pause" and (now - self._seen > STALE_AFTER or
                                (self._report.get("resuming") and now - self._resumed_at < START_TIMEOUT)):
            return True
        key = {"playback-time": "pos", "pause": "paused", "idle-active": "failed"}[prop]
        return self._report.get(key)

    def osd(self, text: str, ms: int = 4000) -> None:
        pass  # the page shows the current stream itself

    def report(self, load_id: int, pos: float | None, paused: bool, failed: bool, resuming: bool) -> None:
        self._seen = time.monotonic()
        if load_id == self.load_id:
            if resuming and not self._report.get("resuming"):
                self._resumed_at = self._seen
            self._report = {"pos": pos, "paused": paused, "failed": failed, "resuming": resuming}


class Site:
    def __init__(self) -> None:
        self.player = BrowserPlayer()
        self.watcher: Watcher | None = None
        self.matches: list[LiveMatch] = []
        self.prefs = {"language": "", "hd": False, "autoSwitch": True}
        self._matches_at = 0.0
        self._discover = threading.Lock()

    def get_matches(self, refresh: bool) -> list[dict]:
        with self._discover:
            if refresh or time.monotonic() - self._matches_at > MATCHES_TTL:
                self.matches = discover_matches("")
                self._matches_at = time.monotonic()
        return [{"key": _key(m), "title": m.title, "league": m.league, "sites": m.sites}
                for m in self.matches]

    def watch(self, key: str) -> None:
        match = next((m for m in self.matches if _key(m) == key), None)
        if match is None:
            raise KeyError("unknown match — refresh the list")
        self.stop()
        self.watcher = Watcher(match, self.player, on_change=lambda: None)
        self._apply_prefs()
        self.watcher.start()

    def set_prefs(self, prefs: dict) -> None:
        self.prefs.update({k: prefs[k] for k in self.prefs if k in prefs})
        self._apply_prefs()

    def _apply_prefs(self) -> None:
        if self.watcher:
            p = self.prefs
            self.watcher.set_prefs(p["language"], bool(p["hd"]), bool(p["autoSwitch"]))

    def stop(self) -> None:
        if self.watcher:
            watcher, self.watcher = self.watcher, None
            watcher.stop()

    def state(self, report: dict) -> dict:
        p = self.player
        p.report(report.get("loadId", -1), report.get("pos"), bool(report.get("paused")),
                 bool(report.get("failed")), bool(report.get("resuming")))
        w = self.watcher
        if w is None:
            return {"match": None, "prefs": self.prefs}
        if w.current and report.get("height"):
            w.current.height = int(report["height"])  # the page knows the real picture size
        return {
            "prefs": self.prefs,
            "match": {"key": _key(w.match), "title": w.match.title, "league": w.match.league},
            "phase": w.phase,
            "message": w.message,
            "url": p.url,
            "loadId": p.load_id,
            "current": w.candidates.index(w.current) if w.current in w.candidates else None,
            "streams": [{"label": c.label, "site": c.site, "status": c.status, "latency": c.latency_ms,
                         "viewers": c.viewers, "hd": c.hd, "height": c.height, "language": c.language,
                         "country": c.country, "stability": round(c.stability, 2), "checks": c.checks}
                        for c in w.candidates],
        }

    def play(self, index: int) -> None:
        if self.watcher:
            self.watcher.play(self.watcher.candidates[index])

    def next_stream(self) -> None:
        if self.watcher:
            self.watcher.next_stream()

    def reprobe(self) -> None:
        if self.watcher:
            self.watcher.reprobe()


def _key(m: LiveMatch) -> str:
    return f"{m.source_site}:{m.id}"


def serve(site: Site) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            url = urlparse.urlparse(self.path)
            if url.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/matches":
                self._json(lambda: site.get_matches("refresh" in urlparse.parse_qs(url.query)))
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            routes = {
                "/api/state": lambda: site.state(body),
                "/api/watch": lambda: site.watch(body["key"]),
                "/api/play": lambda: site.play(int(body["index"])),
                "/api/next": site.next_stream,
                "/api/reprobe": site.reprobe,
                "/api/prefs": lambda: site.set_prefs(body),
            }
            if self.path in routes:
                self._json(routes[self.path])
            else:
                self._send(404, b"not found", "text/plain")

        def _json(self, fn) -> None:
            try:
                status, out = 200, fn()
            except Exception as exc:
                log.warning(f"{self.path}: {exc}")
                status, out = 400, {"error": str(exc)}
            self._send(status, json.dumps(out).encode(), "application/json")

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")
    for lib in ("urllib3", "asyncio", "playwright", "httpx"):
        logging.getLogger(lib).setLevel(logging.WARNING)
    url = f"http://127.0.0.1:{PORT}"
    try:
        server = serve(site := Site())
    except OSError:
        log.info(f"Already running — opening {url}")
        webbrowser.open(url)
        sys.exit(0)
    log.info(f"stream-ripper web → {url}   (Ctrl+C to quit)")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    site.stop()


if __name__ == "__main__":
    main()
