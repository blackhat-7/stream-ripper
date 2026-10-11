import json
import logging
import os
import socket
import subprocess
import time
from typing import Any, Protocol

from _streamer.proxy import HLSProxy
from _streamer.settings import MPV_SOCKET

log = logging.getLogger(__name__)

# Tuned for live HLS: retry flaky segments/connections inside ffmpeg, stay open
# (idle) when a stream fails so the next one loads into the same window.
MPV_ARGS = [
    "--idle=yes", "--force-window=yes", "--keep-open=no", "--ytdl=no", "--terminal=no",
    "--cache=yes", "--demuxer-readahead-secs=20", "--network-timeout=10", "--hls-bitrate=max",
    "--demuxer-lavf-o=seg_max_retry=3,reconnect=1,reconnect_streamed=1,"
    "reconnect_on_network_error=1,reconnect_delay_max=2",
]


class Player(Protocol):
    """What Watcher drives: mpv here, or the web page (web.py's BrowserPlayer)."""
    def play(self, url: str, referrer: str = "", title: str = "Live Stream") -> None: ...
    def stop(self) -> None: ...
    def exit_code(self) -> int | None: ...
    def get(self, prop: str) -> Any: ...
    def osd(self, text: str, ms: int = 4000) -> None: ...


class MpvController:
    """One mpv window reused across streams (switches via IPC `loadfile`)."""

    def __init__(self, socket_path: str = MPV_SOCKET):
        self.socket_path = socket_path
        self.proc: subprocess.Popen | None = None
        self._proxy: HLSProxy | None = None

    def play(self, url: str, referrer: str = "", title: str = "Live Stream") -> None:
        """Play url, reusing the running mpv if there is one. Raises if mpv can't start."""
        if referrer:
            self._proxy = self._proxy or HLSProxy()
            url = self._proxy.wrap(url, referrer)
        log.info(f"mpv → {url[:80]}")
        if self.is_alive() and self.command("loadfile", url, "replace"):
            return
        self._launch(url, title)

    def stop(self) -> None:
        """Kill mpv and the proxy. Fast and idempotent."""
        if self._proxy:
            self._proxy.stop()
            self._proxy = None
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def exit_code(self) -> int | None:
        return self.proc.poll() if self.proc else None

    def get(self, prop: str) -> Any:
        """mpv property value, or None if unavailable."""
        reply = self._request(["get_property", prop])
        return reply.get("data") if reply and reply.get("error") == "success" else None

    def command(self, *args: Any) -> bool:
        reply = self._request(list(args))
        return bool(reply and reply.get("error") == "success")

    def osd(self, text: str, ms: int = 4000) -> None:
        self.command("show-text", text, ms)

    def _launch(self, url: str, title: str) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        self.proc = subprocess.Popen(
            ["mpv", *MPV_ARGS, f"--input-ipc-server={self.socket_path}", f"--title={title}", url],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._wait_for_socket()

    def _wait_for_socket(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.is_alive():
            if self._request(["get_property", "pid"]):
                return
            time.sleep(0.1)

    def _request(self, command: list) -> dict | None:
        """Send one IPC command and return mpv's reply (skipping async events)."""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect(self.socket_path)
                s.sendall((json.dumps({"command": command, "request_id": 1}) + "\n").encode())
                buf = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        return None
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = json.loads(line)
                        if msg.get("request_id") == 1:
                            return msg
        except (OSError, ValueError) as exc:
            log.debug(f"mpv IPC: {exc}")
            return None
