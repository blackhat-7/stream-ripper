import http.server
import logging
import re
import socket
import threading
import urllib.parse as urlparse

from _streamer.net import headers_for, http_get

log = logging.getLogger(__name__)


class HLSProxy:
    """Local HTTP proxy that injects Referer/Origin on every CDN request
    and rewrites m3u8 URLs to route through itself, so mpv never gets a 403.

    The referrer travels in each proxied URL (?u=…&r=…), so one proxy serves
    every stream and mpv can switch streams without a restart."""

    def __init__(self):
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
                    is_m3u8 = real_url.split("?")[0].endswith(".m3u8")
                    resp = http_get(real_url, headers=headers_for(referrer), stream=not is_m3u8)
                    try:
                        ct = resp.headers.get("content-type", "application/octet-stream")
                        if "mpegurl" in ct or is_m3u8:
                            body = proxy._rewrite(resp.text, real_url, referrer).encode()
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
                    finally:
                        resp.close()
                except OSError:
                    pass  # mpv hung up (e.g. switched streams)
                except Exception as exc:
                    log.warning(f"proxy: {exc}")
                    try:
                        self.send_error(502)
                    except OSError:
                        pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        server.handle_error = lambda *_: None  # never print tracebacks over the TUI
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def stop(self) -> None:
        """Non-blocking: shutdown() waits for the serve loop, so do it off-thread."""
        server = self._server
        threading.Thread(target=lambda: (server.shutdown(), server.server_close()),
                         daemon=True).start()

    def _rewrite(self, text: str, base_url: str, referrer: str) -> str:
        out = []
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                abs_url = s if s.startswith("http") else urlparse.urljoin(base_url, s)
                line = self.wrap(abs_url, referrer)
            else:
                line = re.sub(
                    r'URI="([^"]*)"',
                    lambda m: f'URI="{self.wrap(urlparse.urljoin(base_url, m.group(1)), referrer)}"',
                    line,
                )
            out.append(line)
        return "\n".join(out)

    def wrap(self, cdn_url: str, referrer: str) -> str:
        q = urlparse.urlencode({"u": cdn_url, "r": referrer})
        return f"http://127.0.0.1:{self.port}/?{q}"
