import json
import logging
import os
import socket
import subprocess
import time

from _streamer.proxy import HLSProxy
from _streamer.settings import MPV_SOCKET

log = logging.getLogger(__name__)


class MpvController:
    def __init__(self, socket_path: str = MPV_SOCKET):
        self.socket_path = socket_path
        self.proc: subprocess.Popen | None = None
        self._proxy: HLSProxy | None = None

    def launch(self, url: str, title: str = "Live Stream", referrer: str = "") -> None:
        self._kill_existing()
        if referrer:
            proxy = HLSProxy(referrer)
            self._proxy = proxy
            play_url = proxy._wrap(url)
            log.info(f"proxy → {url[:72]}  (ref: {referrer[:60]})")
        else:
            play_url = url
            log.info(f"mpv → {url[:80]}")
        self.proc = subprocess.Popen([
            "mpv", "--cache=yes", "--demuxer-readahead-secs=20",
            f"--input-ipc-server={self.socket_path}",
            f"--title={title}", "--force-window=yes", "--ytdl=no",
            play_url,
        ])
        self._wait_for_socket()

    def _kill_existing(self) -> None:
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

    def _wait_for_socket(self, timeout: float = 12.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(self.socket_path):
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                        s.settimeout(1)
                        s.connect(self.socket_path)
                    return
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

    def osd(self, text: str, ms: int = 4000) -> None:
        self._send(["show-text", text, ms])

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None
