#!/usr/bin/env python3
"""Modern Textual TUI for our-streamer."""
from __future__ import annotations

import collections
import logging
import sys
import threading
import time
from datetime import datetime

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.message import Message
from textual.screen import Screen
from textual.widgets import DataTable, Footer, RichLog, Static

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.player import MpvController
from _streamer.probe import probe_all
from _streamer.settings import CHECK_EVERY, PROBE_BATCH, SWITCH_MARGIN
from _streamer.sources import discover_matches, load_candidates


# ─── Log capture ───────────────────────────────────────────────────────────────

class TuiLogHandler(logging.Handler):
    _COLORS = {
        "DEBUG":    "#2e3440",
        "INFO":     "#7c6af6",
        "WARNING":  "#f4b942",
        "ERROR":    "#f06f6f",
        "CRITICAL": "#f06f6f",
    }

    def __init__(self) -> None:
        super().__init__()
        self._q: collections.deque[Text] = collections.deque(maxlen=2000)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ts    = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
            color = self._COLORS.get(record.levelname, "#888")
            line  = Text(no_wrap=True)
            line.append(ts,                   style="#2e3440")
            line.append("  ")
            line.append(f"{record.levelname[:4]:<4}", style=color)
            line.append("  ")
            line.append(record.getMessage(),  style="#8899aa")
            self._q.append(line)
        except Exception:
            pass

    def drain(self) -> list[Text]:
        out: list[Text] = []
        try:
            while True:
                out.append(self._q.popleft())
        except IndexError:
            pass
        return out


_log_handler = TuiLogHandler()


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(_log_handler)
    for lib in ("urllib3", "asyncio", "playwright", "httpx"):
        logging.getLogger(lib).setLevel(logging.WARNING)


# ─── Home Screen ───────────────────────────────────────────────────────────────

class HomeScreen(Screen[None]):
    """Match browser. Always lives at the bottom of the screen stack."""

    BINDINGS = [
        Binding("r", "refresh", "refresh"),
        Binding("q", "app.quit", "quit"),
    ]

    CSS = """
    HomeScreen {
        background: #0c0c10;
        layout: vertical;
        padding: 2 4;
    }
    #home-logo {
        color: #6e5fed;
        text-style: bold;
        height: 2;
    }
    #home-hdr {
        height: 1;
        layout: horizontal;
        margin-bottom: 1;
    }
    #home-lbl {
        width: 1fr;
        color: #3a3a4a;
        text-style: bold;
    }
    #home-status {
        width: auto;
        color: #3a3a4a;
    }
    #home-table {
        height: 1fr;
        background: transparent;
    }
    #home-table > .datatable--header {
        display: none;
    }
    #home-table > .datatable--cursor {
        background: #1a1a2e;
    }
    #home-table > .datatable--hover {
        background: #111118;
    }
    #home-hint {
        height: 1;
        color: #2e3440;
        margin-top: 1;
    }
    """

    class MatchChosen(Message):
        def __init__(self, match: LiveMatch) -> None:
            super().__init__()
            self.match = match

    def __init__(self, query: str) -> None:
        super().__init__()
        self._query   = query
        self._matches: list[LiveMatch] = []

    def compose(self) -> ComposeResult:
        yield Static("◉  stream-ripper", id="home-logo")
        with Container(id="home-hdr"):
            yield Static("LIVE MATCHES", id="home-lbl")
            yield Static("",             id="home-status")
        yield DataTable(id="home-table", cursor_type="row", show_cursor=True, show_header=False)
        yield Static(
            "↑ ↓  navigate   ·   enter  watch   ·   r  refresh   ·   q  quit",
            id="home-hint",
        )

    def on_mount(self) -> None:
        t = self.query_one("#home-table", DataTable)
        t.add_column("title",  width=54, key="title")
        t.add_column("source", width=26, key="source")
        self._do_discover()

    def _set_status(self, text: str) -> None:
        self.query_one("#home-status", Static).update(text)

    def _populate(self, matches: list[LiveMatch]) -> None:
        self._matches = matches
        t = self.query_one("#home-table", DataTable)
        t.clear()
        if matches:
            for m in matches:
                t.add_row(
                    Text(m.title,       style="#c8c8d8"),
                    Text(m.source_site, style="#4a4a5a"),
                )
            self._set_status(f"{len(matches)} found")
            t.focus()
        else:
            self._set_status("none found — try r to refresh")

    @work(thread=True)
    def _do_discover(self) -> None:
        log = logging.getLogger(__name__)
        self.app.call_from_thread(self._set_status, "discovering…")
        suffix = f" for {self._query!r}" if self._query else ""
        log.info(f"Discovering live matches{suffix}…")
        matches = discover_matches(self._query)
        self.app.call_from_thread(self._populate, matches)

    def action_refresh(self) -> None:
        self._do_discover()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if self._matches and event.cursor_row < len(self._matches):
            self.post_message(self.MatchChosen(self._matches[event.cursor_row]))


# ─── Monitor Screen ────────────────────────────────────────────────────────────

class MonitorScreen(Screen[None]):
    """Stream monitor for a selected match. Push on top of HomeScreen."""

    BINDINGS = [
        Binding("h",      "go_home",     "home",    show=True),
        Binding("escape", "go_home",     "",        show=False),
        Binding("r",      "refresh_now", "refresh", show=True),
        Binding("l",      "toggle_logs", "logs",    show=True),
        Binding("q",      "app.quit",    "quit",    show=True),
    ]

    CSS = """
    MonitorScreen {
        background: #0c0c10;
        layout: vertical;
    }

    /* header */
    #mon-hdr {
        height: 1;
        background: #0c0c10;
        layout: horizontal;
        padding: 0 2;
        border-bottom: solid #181820;
    }
    #mon-logo {
        width: auto;
        color: #6e5fed;
        text-style: bold;
        padding-right: 3;
    }
    #mon-match {
        width: 1fr;
        color: #c8c8d8;
    }
    #mon-status {
        width: auto;
        color: #3a3a4a;
        text-align: right;
    }

    /* streams */
    #mon-streams {
        height: 1fr;
        padding: 1 2;
    }
    #mon-streams-hdr {
        height: 1;
        layout: horizontal;
        margin-bottom: 1;
    }
    #mon-streams-lbl {
        width: auto;
        color: #3a3a4a;
        text-style: bold;
    }
    #mon-streams-count {
        width: 1fr;
        color: #3a3a4a;
        text-align: right;
    }
    #streams-table {
        height: 1fr;
        background: transparent;
    }
    #streams-table > .datatable--header {
        background: #0c0c10;
        color: #2e3440;
        text-style: none;
        height: 1;
    }
    #streams-table > .datatable--cursor {
        background: #141420;
    }
    #streams-table > .datatable--hover {
        background: #111118;
    }

    /* logs */
    #mon-logs {
        height: 10;
        border-top: solid #181820;
        padding: 0 2;
    }
    #mon-logs.hidden {
        display: none;
    }
    #mon-logs-hdr {
        height: 1;
        layout: horizontal;
        padding-top: 1;
    }
    #mon-logs-lbl {
        width: 1fr;
        color: #3a3a4a;
        text-style: bold;
    }
    #mon-logs-hint {
        width: auto;
        color: #2e3440;
    }
    RichLog {
        background: transparent;
        height: 1fr;
        scrollbar-color: #2a2a3a #0c0c10;
    }

    /* footer */
    Footer {
        background: #0c0c10;
        border-top: solid #181820;
        color: #2e3440;
    }
    Footer > .footer--key {
        color: #6e5fed;
        background: transparent;
    }
    Footer > .footer--description {
        color: #3a3a4a;
    }
    """

    def __init__(self, match: LiveMatch, mpv: MpvController) -> None:
        super().__init__()
        self._match      = match
        self._mpv        = mpv
        self._candidates: list[StreamCandidate] = []
        self._current:    StreamCandidate | None = None
        self._stop       = threading.Event()
        self._logs_vis   = True

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Container(id="mon-hdr"):
            yield Static("◉  streamer",   id="mon-logo")
            yield Static(self._match.title, id="mon-match")
            yield Static("loading…",      id="mon-status")

        with Container(id="mon-streams"):
            with Container(id="mon-streams-hdr"):
                yield Static("STREAMS", id="mon-streams-lbl")
                yield Static("",        id="mon-streams-count")
            yield DataTable(id="streams-table", show_cursor=True,
                            cursor_type="row", show_header=True)

        with Container(id="mon-logs"):
            with Container(id="mon-logs-hdr"):
                yield Static("LOGS",     id="mon-logs-lbl")
                yield Static("l · hide", id="mon-logs-hint")
            yield RichLog(id="log-output", highlight=False, markup=False, wrap=False)

        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one("#streams-table", DataTable)
        t.add_column("",        width=3,  key="play")
        t.add_column("stream",  width=46, key="label")
        t.add_column("status",  width=16, key="status")
        t.add_column("viewers", width=9,  key="viewers")
        self.set_interval(0.15, self._flush_logs)
        self._load_and_probe()

    def on_unmount(self) -> None:
        self._stop.set()
        try:
            if self._mpv.proc:
                self._mpv.proc.terminate()
        except Exception:
            pass

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _flush_logs(self) -> None:
        if not self._logs_vis:
            return
        w = self.query_one("#log-output", RichLog)
        for line in _log_handler.drain():
            w.write(line)

    def _set_status(self, text: str) -> None:
        self.query_one("#mon-status", Static).update(text)

    def _refresh_table(self) -> None:
        t = self.query_one("#streams-table", DataTable)
        # Preserve cursor row across rebuilds
        cursor_row = t.cursor_row
        t.clear()

        alive = sum(1 for c in self._candidates if c.alive)
        total = len(self._candidates)
        self.query_one("#mon-streams-count", Static).update(
            f"{alive}/{total} alive" if total else ""
        )

        for c in self._candidates:
            playing = self._current is not None and c.label == self._current.label

            play_cell = Text("▶", style="#6e5fed") if playing else Text(" ")

            if playing:
                label_cell = Text(c.label, style="bold #c8c8d8")
            elif c.alive:
                label_cell = Text(c.label, style="#7a8899")
            else:
                label_cell = Text(c.label, style="#2e3440")

            if c.resolve_attempts == 0 and c.failures == 0 and not c.resolved:
                status_cell = Text("…  pending", style="#3a3a4a")
            elif c.alive:
                ms = c.latency_ms
                ms_color = (
                    "#3dd68c" if ms < 200 else
                    "#f4b942" if ms < 500 else "#f06f6f"
                )
                status_cell = Text()
                status_cell.append("✓  ", style="#3dd68c")
                status_cell.append(f"{ms}ms", style=ms_color)
            elif c.resolve_attempts > 0 or c.failures > 0:
                status_cell = Text("✗  dead", style="#f06f6f")
            else:
                status_cell = Text("…  pending", style="#3a3a4a")

            if c.viewers:
                v = c.viewers
                viewers_cell = Text(
                    f"{v/1000:.1f}k" if v >= 1000 else str(v),
                    style="#3a3a4a",
                )
            else:
                viewers_cell = Text("")

            t.add_row(play_cell, label_cell, status_cell, viewers_cell)

        # Restore cursor to same row (clipped to valid range)
        if total:
            t.move_cursor(row=min(cursor_row, total - 1))

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_go_home(self) -> None:
        # on_unmount handles mpv + thread cleanup
        self.app.pop_screen()

    def action_toggle_logs(self) -> None:
        self._logs_vis = not self._logs_vis
        logs = self.query_one("#mon-logs")
        hint = self.query_one("#mon-logs-hint", Static)
        if self._logs_vis:
            logs.remove_class("hidden")
            hint.update("l · hide")
        else:
            logs.add_class("hidden")
            hint.update("l · show")

    def action_refresh_now(self) -> None:
        if self._candidates:
            self._force_probe()

    # ── Manual stream switch (enter on table row) ─────────────────────────────

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if not self._candidates or event.cursor_row >= len(self._candidates):
            return
        target = self._candidates[event.cursor_row]
        if self._current is target:
            return
        if not target.alive or not target.resolved:
            self._set_status("⚠  stream not ready — wait for probing")
            return
        log = logging.getLogger(__name__)
        log.info(f"Manual switch → {target.label}")
        self._current = target
        self._set_status(f"▶  {target.label}")
        self._refresh_table()
        threading.Thread(target=self._launch_mpv, args=(target,), daemon=True).start()

    # ── Workers ───────────────────────────────────────────────────────────────

    @work(thread=True)
    def _load_and_probe(self) -> None:
        log = logging.getLogger(__name__)
        log.info("Loading stream candidates…")
        candidates = load_candidates(self._match)

        if not candidates:
            self.app.call_from_thread(self._set_status, "no streams found — press h to go back")
            return

        self._candidates = candidates
        self.app.call_from_thread(self._refresh_table)

        log.info(f"Probing {len(candidates)} stream(s) in batches of {PROBE_BATCH}…")
        for i in range(0, len(candidates), PROBE_BATCH):
            if self._stop.is_set():
                return
            probe_all(candidates[i : i + PROBE_BATCH])
            self.app.call_from_thread(self._refresh_table)
            if i == 0:
                self.app.call_from_thread(self._maybe_start_playing)

        self.app.call_from_thread(self._after_all_probed)

    @work(thread=True)
    def _force_probe(self) -> None:
        log = logging.getLogger(__name__)
        log.info("Manual refresh — re-probing…")
        probe_all(self._candidates, resolve_if_dead=False)
        self.app.call_from_thread(self._refresh_table)

    def _maybe_start_playing(self) -> None:
        if self._current is not None:
            return
        alive = [c for c in self._candidates if c.alive]
        if not alive:
            return
        best = max(alive, key=lambda c: c.score())
        self._current = best
        self._set_status(f"▶  {best.label}")
        self._refresh_table()
        threading.Thread(target=self._launch_mpv, args=(best,), daemon=True).start()

    def _launch_mpv(self, c: StreamCandidate) -> None:
        log = logging.getLogger(__name__)
        log.info(f"Playing: {c.label}  ({c.latency_ms}ms)")
        try:
            self._mpv.launch(c.resolved, title=self._match.title, referrer=c.embed_url)
            self._mpv.osd(f"▶ {c.label}  ({c.latency_ms}ms)")
        except Exception as exc:
            log.error(f"mpv launch failed: {exc}")

    def _after_all_probed(self) -> None:
        self._refresh_table()
        alive = [c for c in self._candidates if c.alive]
        if not alive:
            self._set_status("no working streams — press h to go back")
            return
        self._maybe_start_playing()
        threading.Thread(target=self._monitor_loop, daemon=True, name="monitor").start()

    # ── Monitor loop ──────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        log = logging.getLogger(__name__)
        while not self._stop.wait(CHECK_EVERY):
            if not self._mpv.is_alive():
                log.warning("mpv closed — re-launching…")
                if self._current and self._current.resolved:
                    try:
                        self._mpv.launch(
                            self._current.resolved,
                            title=self._match.title,
                            referrer=self._current.embed_url,
                        )
                    except Exception as exc:
                        log.error(f"mpv re-launch: {exc}")
                continue

            log.info("Health check…")
            probe_all(
                [c for c in self._candidates if c is not self._current],
                resolve_if_dead=False,
            )
            if self._current:
                self._current.alive = True

            alive = [c for c in self._candidates if c.alive]
            self.app.call_from_thread(self._refresh_table)

            if not self._current:
                continue

            best         = max(alive, key=lambda c: c.score()) if alive else None
            current_dead = not self._current.alive
            much_better  = (
                best is not None
                and best is not self._current
                and best.latency_ms < self._current.latency_ms * SWITCH_MARGIN
            )

            if current_dead:
                log.warning("Current stream dead — finding replacement…")
                unresolved = [
                    c for c in self._candidates
                    if c is not self._current and not c.resolved
                ]
                if unresolved:
                    probe_all(unresolved, resolve_if_dead=True)
                alive = [c for c in self._candidates if c.alive]
                best  = max(alive, key=lambda c: c.score()) if alive else None

            if (current_dead or much_better) and best:
                reason = (
                    f"{self._current.label} died" if current_dead
                    else f"{best.latency_ms}ms vs {self._current.latency_ms}ms"
                )
                log.info(f"Switching: {self._current.label} → {best.label}  ({reason})")
                self._current = best
                try:
                    self._mpv.launch(
                        best.resolved, title=self._match.title, referrer=best.embed_url
                    )
                    self._mpv.osd(f"↷ {best.label}  ({best.latency_ms}ms)")
                except Exception as exc:
                    log.error(f"mpv switch: {exc}")
                self.app.call_from_thread(self._set_status, f"▶  {best.label}")
                self.app.call_from_thread(self._refresh_table)
            elif current_dead:
                log.warning("All streams dead — waiting for recovery…")
                self.app.call_from_thread(self._set_status, "⚠  all streams dead — press h to pick another")


# ─── App ───────────────────────────────────────────────────────────────────────

class StreamerApp(App[None]):
    TITLE = "stream-ripper"

    CSS = """
    Screen {
        background: #0c0c10;
    }
    """

    def __init__(self, query: str) -> None:
        super().__init__()
        self._query = query
        self._mpv   = MpvController()

    def on_mount(self) -> None:
        self.push_screen(HomeScreen(self._query))

    @on(HomeScreen.MatchChosen)
    def _match_chosen(self, event: HomeScreen.MatchChosen) -> None:
        self.push_screen(MonitorScreen(event.match, self._mpv))

    async def action_quit(self) -> None:
        try:
            if self._mpv.proc:
                self._mpv.proc.terminate()
        except Exception:
            pass
        self.exit()


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    args  = sys.argv[1:]
    query = " ".join(a for a in args if not a.startswith("--"))
    _setup_logging()
    StreamerApp(query=query).run()


if __name__ == "__main__":
    main()
