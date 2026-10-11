import http.server
import logging
import queue
import re
import socket
import threading
import urllib.parse as urlparse
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor

from _streamer.net import headers_for, http_get, new_session

log = logging.getLogger(__name__)

PREFETCH = 3   # newest segments of each media playlist fetched ahead, in parallel
KEEP = 12      # prefetched segments held at most (a few MB each)


class HLSProxy:
    """Local HTTP proxy that injects Referer/Origin on every CDN request
    and rewrites m3u8 URLs to route through itself, so mpv never gets a 403.

    The referrer travels in each proxied URL (?u=…&r=…), so one proxy serves
    every stream and mpv can switch streams without a restart.

    The server runs a thread per player connection, so CDN sessions live in a
    pool rather than per thread: each segment reuses a warm TLS connection.

    Players fetch one segment at a time, and some CDNs here barely manage an HD
    segment per segment-length on one connection. So whenever a media playlist
    goes out, its newest segments start downloading in parallel, ready (or
    nearly) by the time the player asks."""

    def __init__(self):
        self._sessions: queue.LifoQueue = queue.LifoQueue()
        self._ahead: OrderedDict[str, Future] = OrderedDict()  # segment URL -> its download
        self._ahead_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(PREFETCH * 2)
        self.port    = self._free_port()
        self._server = self._start()

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _start(self) -> http.server.ThreadingHTTPServer:
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None: pass

            def do_GET(self):
                qs = urlparse.parse_qs(urlparse.urlparse(self.path).query)
                real_url = qs.get("u", [None])[0]
                referrer = qs.get("r", [""])[0]
                if not real_url:
                    self.send_error(400, "Missing ?u=")
                    return
                try:
                    status, ct, body = proxy._get(real_url, referrer)
                    if "mpegurl" in ct or real_url.split("?")[0].endswith(".m3u8"):
                        ct = "application/vnd.apple.mpegurl"
                        body = proxy._rewrite(body.decode("utf-8", "replace"), real_url, referrer).encode()
                except Exception as exc:  # the CDN failed (curl errors are OSErrors too)
                    log.warning(f"proxy: {exc}")
                    status, ct, body = 502, "text/plain", b""
                try:
                    self.send_response(status)
                    self.send_header("Access-Control-Allow-Origin", "*")  # web player; also on errors
                    self.send_header("Content-Type", ct)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass  # the player hung up (e.g. switched streams)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        server.handle_error = lambda *_: None  # never print tracebacks over the TUI
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def stop(self) -> None:
        """Non-blocking: shutdown() waits for the serve loop, so do it off-thread."""
        server = self._server
        threading.Thread(target=lambda: (server.shutdown(), server.server_close()),
                         daemon=True).start()

    def _fetch(self, url: str, referrer: str) -> tuple[int, str, bytes]:
        """(status, content type, body). Whole bodies only: see net.py on why never
        stream=True. CDNs here reset connections often; retries beat a player error."""
        try:
            session = self._sessions.get_nowait()
        except queue.Empty:
            session = new_session()
        try:
            resp = http_get(url, headers=headers_for(referrer), session=session, retries=4, timeout=20)
            return resp.status_code, resp.headers.get("content-type", "application/octet-stream"), resp.content
        finally:
            self._sessions.put(session)

    def _get(self, url: str, referrer: str) -> tuple[int, str, bytes]:
        with self._ahead_lock:
            ahead = self._ahead.pop(url, None)
        if ahead:
            try:
                return ahead.result()
            except Exception:
                pass  # the early download failed; try once more now
        return self._fetch(url, referrer)

    def _prefetch(self, segments: list[str], referrer: str) -> None:
        with self._ahead_lock:
            for url in segments[-PREFETCH:]:
                if url not in self._ahead:
                    self._ahead[url] = self._pool.submit(self._fetch, url, referrer)
            while len(self._ahead) > KEEP:
                self._ahead.popitem(last=False)[1].cancel()

    def _rewrite(self, text: str, base_url: str, referrer: str) -> str:
        out, segments = [], []
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                abs_url = s if s.startswith("http") else urlparse.urljoin(base_url, s)
                if "#EXTINF" in text:  # a media playlist: these lines are segments
                    segments.append(abs_url)
                line = self.wrap(abs_url, referrer)
            else:
                line = re.sub(
                    r'URI="([^"]*)"',
                    lambda m: f'URI="{self.wrap(urlparse.urljoin(base_url, m.group(1)), referrer)}"',
                    line,
                )
            out.append(line)
        self._prefetch(segments, referrer)
        return "\n".join(out)

    def wrap(self, cdn_url: str, referrer: str) -> str:
        q = urlparse.urlencode({"u": cdn_url, "r": referrer})
        return f"http://127.0.0.1:{self.port}/?{q}"
