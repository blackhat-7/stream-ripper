"""Keeps one match playing: probes streams, plays the best, fails over fast.

Threads: one loop thread owns the player and all switching decisions; PROBE_WORKERS
worker threads resolve/probe candidates. Public methods only set a request and
wake the loop, so they never block the caller.
"""
import logging
import queue
import threading
import time
from typing import Callable

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.player import Player
from _streamer.probe import mark_failed, probe
from _streamer.settings import (DEAD_RETRY_MAX, MIN_DWELL, POLL_EVERY, PROBE_WORKERS,
                                RECHECK_EVERY, START_TIMEOUT, STALL_SECS, SWITCH_MARGIN)
from _streamer.sources import load_candidates

log = logging.getLogger(__name__)

FIRST_RETRY = 5.0                               # first backoff step when everything is dead
NO_BROWSER_PHASES = ("playing", "switching", "stopped")  # never pop Chrome over the stream


class Watcher:
    def __init__(self, match: LiveMatch, player: Player, on_change: Callable[[], None]) -> None:
        self.match = match
        self.player = player
        self.candidates: list[StreamCandidate] = []
        self.current: StreamCandidate | None = None
        self.phase = "loading"
        self.message = "Loading streams…"
        self._on_change = on_change
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._jobs: queue.Queue[StreamCandidate] = queue.Queue()
        self._queued: set[int] = set()  # id() of candidates waiting for or in a probe
        self._request: StreamCandidate | str | None = None  # manual target or "next"
        self._reprobe = False
        self._user_closed = False
        # Preferences (the web app sets these): they rank streams ahead of raw score.
        self.language = ""        # preferred language; other streams are fallbacks
        self.prefer_hd = False
        self.auto_upgrade = True  # move to a much better stream after MIN_DWELL
        # Health of the current stream (loop thread only)
        self._since = 0.0
        self._progress_at = 0.0
        self._last_pos: float | None = None
        # Background sweeps
        self._next_sweep = 0.0
        self._retry_delay = FIRST_RETRY

    # ── Public API (any thread, non-blocking) ─────────────────────────────────

    def start(self) -> None:
        for _ in range(PROBE_WORKERS):
            threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            if self._stopping.is_set():
                return
            self.current, self.phase, self.message = None, "stopped", "Stopped"
            self._stopping.set()
        self._wake.set()
        self.player.stop()

    def play(self, c: StreamCandidate) -> None:
        if c.alive and c is not self.current:
            self._ask(c)

    def next_stream(self) -> None:
        self._ask("next")

    def set_prefs(self, language: str, prefer_hd: bool, auto_upgrade: bool) -> None:
        self.language, self.prefer_hd, self.auto_upgrade = language, prefer_hd, auto_upgrade
        best, cur = self._best(), self.current
        if best and cur and self._fit(best) > self._fit(cur):
            self._ask(best)

    def reprobe(self) -> None:
        with self._lock:
            self._reprobe = True
        self._wake.set()

    # ── Loop thread ───────────────────────────────────────────────────────────

    def _ask(self, request: StreamCandidate | str) -> None:
        with self._lock:
            self._request = request
        self._wake.set()

    def _run(self) -> None:
        self._load()
        self._next_sweep = time.monotonic() + RECHECK_EVERY
        while not self._stopping.is_set():
            self._wake.clear()
            try:
                self._tick()
            except Exception:
                log.exception("watcher tick failed")
            self._wake.wait(POLL_EVERY)

    def _tick(self) -> None:
        with self._lock:
            request, self._request = self._request, None
            reprobe, self._reprobe = self._reprobe, False

        if reprobe:
            self._sweep(reset=True)
        if request == "next":
            request = self._best(exclude=self.current)
            if request is None:
                self._set(message="No other live stream")

        if isinstance(request, StreamCandidate):
            self._user_closed = False
            self._switch(request)
        elif self.current:
            self._check_current()
        elif not self._user_closed:
            best = self._best()
            if best:
                self._switch(best)
            else:
                self._show_waiting()

        if time.monotonic() >= self._next_sweep:
            self._sweep()

    def _check_current(self) -> None:
        c = self.current
        assert c is not None
        if self.player.exit_code() == 0:
            # User closed the window: don't fight them by respawning mpv.
            log.info("player closed by user")
            self._user_closed = True
            self._set(current=None, phase="stopped", message="Player closed — pick a stream to resume")
            return

        problem = self._health()
        if problem is None:
            c.alive = True  # mpv is the source of truth for the playing stream
            self._maybe_upgrade(c)
            return

        log.warning(f"[{c.label}] {problem} — switching")
        mark_failed(c)
        nxt = self._best(exclude=c)
        if nxt:
            self._switch(nxt)
            return
        # Nothing else alive: leave mpv on it (it may recover) and re-resolve everything now.
        self._set(current=None)
        self._retry_delay = FIRST_RETRY
        self._next_sweep = 0.0
        self._show_waiting()

    def _health(self) -> str | None:
        """Problem with the playing stream, or None if it's fine."""
        code = self.player.exit_code()
        if code is not None:
            return f"player exited ({code})"
        now = time.monotonic()
        if now - self._since > POLL_EVERY and self.player.get("idle-active"):
            return "stream ended"  # load error or EOF; mpv went idle
        pos = self.player.get("playback-time")
        if self.player.get("pause") or (pos is not None and pos != self._last_pos):
            self._last_pos, self._progress_at = pos, now
            return None
        if self._last_pos is None:
            return "didn't start" if now - self._progress_at > START_TIMEOUT else None
        return "stalled" if now - self._progress_at > STALL_SECS else None

    def _maybe_upgrade(self, c: StreamCandidate) -> None:
        if not self.auto_upgrade or time.monotonic() - self._since < MIN_DWELL:
            return
        best = self._best(exclude=c)
        if best and best.checks >= 2 and best.stability >= 0.75 and (self._fit(best) > self._fit(c) or
                     (self._fit(best) == self._fit(c) and c.score() < best.score() * SWITCH_MARGIN)):
            log.info(f"Upgrade: {c.label} → {best.label}")
            self._switch(best)

    def _switch(self, c: StreamCandidate) -> None:
        url = c.resolved
        if self._stopping.is_set() or not url:
            return
        self._set(phase="switching", message=f"Switching to {c.label}…")
        try:
            self.player.play(url, referrer=c.play_referer, title=self.match.title)
        except Exception as exc:
            log.error(f"player failed: {exc}")
            mark_failed(c)
            self._set(current=None, message=f"Player failed: {exc}")
            return
        if self._stopping.is_set():
            self.player.stop()  # stop() raced with the launch
            return
        now = time.monotonic()
        self._since, self._progress_at, self._last_pos = now, now, None
        self._retry_delay = FIRST_RETRY
        log.info(f"Playing: {c.label}")
        self._set(current=c, phase="playing", message=f"Playing {c.label} · {c.latency_ms}ms")
        self.player.osd(f"▶ {c.label}  ({c.latency_ms}ms)")

    def _show_waiting(self) -> None:
        """Phase/message while nothing is playing (and the user didn't close mpv)."""
        with self._lock:
            busy = len(self._queued)
        if not self.candidates:
            self._set(phase="no-streams", message="No streams found — retrying")
        elif busy:
            self._set(phase="probing", message=f"Probing {busy} stream(s)…")
        else:
            wait = max(0, int(self._next_sweep - time.monotonic()))
            self._set(phase="dead", message=f"All streams down — retrying in {wait}s")

    def _sweep(self, reset: bool = False) -> None:
        """Re-probe all non-playing candidates. When nothing plays, retry with backoff,
        re-resolving dead ones and reloading the source list for new streams."""
        now = time.monotonic()
        with self._lock:
            idle = self.current is None and not self._user_closed and not self._queued
        if idle:
            reset = True
            self._next_sweep = now + self._retry_delay
            self._retry_delay = min(self._retry_delay * 2, DEAD_RETRY_MAX)
        else:
            self._next_sweep = now + RECHECK_EVERY
        for c in self.candidates:
            if reset:
                c.resolve_attempts = 0
            self._enqueue(c)
        if idle:
            self._load()

    def _load(self) -> None:
        try:
            found = load_candidates(self.match)
        except Exception as exc:
            log.warning(f"load_candidates: {exc}")
            found = []
        known = {c.embed_url for c in self.candidates}
        new = [c for c in found if c.embed_url not in known]
        if new:
            log.info(f"Found {len(new)} new stream(s)")
            with self._lock:
                self.candidates = self.candidates + new
            for c in new:
                self._enqueue(c)
        if not self.current:
            self._show_waiting()
        self._notify()

    def _best(self, exclude: StreamCandidate | None = None) -> StreamCandidate | None:
        alive = [c for c in self.candidates if c.alive and c.resolved and c is not exclude]
        return max(alive, key=lambda c: (self._fit(c), c.score()), default=None)

    def _fit(self, c: StreamCandidate) -> tuple[bool, bool]:
        """How well c matches the preferences, most important first."""
        return (not self.language or c.language == self.language, not self.prefer_hd or c.hd)

    # ── Probe workers ─────────────────────────────────────────────────────────

    def _enqueue(self, c: StreamCandidate) -> None:
        with self._lock:
            if c is self.current or id(c) in self._queued:
                return
            self._queued.add(id(c))
        self._jobs.put(c)

    def _worker(self) -> None:
        while not self._stopping.is_set():
            try:
                c = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if c is not self.current:  # CDNs reject a 2nd connection to the playing stream
                    self._notify()
                    probe(c, no_browser=self.phase in NO_BROWSER_PHASES)
            except Exception:
                log.exception(f"probe {c.label}")
            finally:
                with self._lock:
                    self._queued.discard(id(c))
                self._notify()
                self._wake.set()

    # ── State updates ─────────────────────────────────────────────────────────

    def _set(self, **fields) -> None:
        with self._lock:
            if self._stopping.is_set():
                return
            changed = any(getattr(self, k) != v for k, v in fields.items())
            for k, v in fields.items():
                setattr(self, k, v)
        if changed:
            self._notify()

    def _notify(self) -> None:
        if self._stopping.is_set():
            return
        try:
            self._on_change()
        except Exception:
            log.exception("on_change failed")
