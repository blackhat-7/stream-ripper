import http.server
import logging
import re
import socket
import threading
import urllib.parse as urlparse

import requests

from _streamer.net import origin
from _streamer.settings import HEADERS, REQ_TIMEOUT

log = logging.getLogger(__name__)


class HLSProxy:
    """Local HTTP proxy that injects Referer/Origin on every CDN request
    and rewrites m3u8 URLs to route through itself, so mpv never gets a 403."""

    def __init__(self, referrer: str):
        self.referrer = referrer
        self.origin   = origin(referrer)
        self.port     = self._free_port()
        self._server: http.server.HTTPServer | None = None
        self._session = requests.Session()
        self._start()

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def _start(self) -> None:
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None: pass

            def do_GET(self):
                real_url = urlparse.parse_qs(urlparse.urlparse(self.path).query).get("u", [None])[0]
                if not real_url:
                    self.send_error(400, "Missing ?u=")
                    return
                h = {**HEADERS, "Referer": proxy.referrer, "Origin": proxy.origin}
                try:
                    is_m3u8 = real_url.split("?")[0].endswith(".m3u8")
                    resp = proxy._session.get(real_url, headers=h, timeout=REQ_TIMEOUT,
                                              stream=not is_m3u8)
                    ct = resp.headers.get("content-type", "application/octet-stream")
                    if "mpegurl" in ct or is_m3u8:
                        body = proxy._rewrite(resp.text, real_url).encode()
                        self.send_response(resp.status_code)
                        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    else:
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

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None

    def _rewrite(self, text: str, base_url: str) -> str:
        out = []
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                abs_url = s if s.startswith("http") else urlparse.urljoin(base_url, s)
                line = self._wrap(abs_url)
            else:
                line = re.sub(
                    r'URI="([^"]*)"',
                    lambda m: f'URI="{self._wrap(urlparse.urljoin(base_url, m.group(1)))}"',
                    line,
                )
            out.append(line)
        return "\n".join(out)

    def _wrap(self, cdn_url: str) -> str:
        return f"http://localhost:{self.port}/?u={urlparse.quote(cdn_url, safe='')}"
