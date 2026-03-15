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
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
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
            line.append(ts,                    style="#2e3440")
            line.append("  ")
            line.append(f"{record.levelname[:4]:<4}", style=color)
            line.append("  ")
            line.append(record.getMessage(),   style="#8899aa")
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


# ─── Match Select Screen ───────────────────────────────────────────────────────

class MatchSelectScreen(Screen["LiveMatch"]):
    """Full-screen match picker — returns the selected LiveMatch."""

    BINDINGS = [Binding("q", "app.quit", "quit")]

    CSS = """
    MatchSelectScreen {
        background: #0c0c10;
        align: center middle;
    }
    #box {
        width: 84;
        height: auto;
        max-height: 46;
        border: solid #1e1e28;
        background: #10101a;
        padding: 2 3;
    }
    #box-title {
        color: #6e5fed;
        text-style: bold;
        text-align: center;
        margin-bottom: 2;
    }
    DataTable {
        background: transparent;
        height: auto;
        max-height: 36;
    }
    DataTable > .datatable--header {
        background: transparent;
        color: #3a3a4a;
        text-style: none;
    }
    DataTable > .datatable--cursor {
        background: #1a1a2e;
    }
    DataTable > .datatable--hover {
        background: #141420;
    }
    #box-hint {
        color: #2e3440;
        text-align: center;
        margin-top: 2;
    }
    """

    def __init__(self, matches: list[LiveMatch]) -> None:
        super().__init__()
        self._matches = matches

    def compose(self) -> ComposeResult:
        with Container(id="box"):
            yield Static("◉  our-streamer", id="box-title")
            yield DataTable(id="match-table", cursor_type="row", show_cursor=True)
            yield Static(
                "↑ ↓  navigate   ·   enter  select   ·   q  quit",
                id="box-hint",
            )

    def on_mount(self) -> None:
        t = self.query_one("#match-table", DataTable)
        t.add_column("match",  width=52, key="title")
        t.add_column("source", width=24, key="source")
        for m in self._matches:
            t.add_row(
                Text(m.title,       style="#c8c8d8"),
                Text(m.source_site, style="#4a4a5a"),
            )
        t.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(self._matches[event.cursor_row])


# ─── Main App ─────────────────────────────────────────────────────────────────

class StreamerApp(App[None]):
    TITLE = "our-streamer"

    BINDINGS = [
        Binding("l",     "toggle_logs",  "logs",    show=True),
        Binding("r",     "refresh_now",  "refresh", show=True),
        Binding("q",     "quit",         "quit",    show=True),
    ]

    CSS = """
    /* ── base ── */
    Screen {
        background: #0c0c10;
        layout: vertical;
    }

    /* ── header bar ── */
    #hdr {
        height: 1;
        background: #0c0c10;
        layout: horizontal;
        padding: 0 2;
        border-bottom: solid #181820;
    }
    #hdr-logo {
        width: auto;
        color: #6e5fed;
        text-style: bold;
        padding-right: 3;
    }
    #hdr-match {
        width: 1fr;
        color: #c8c8d8;
    }
    #hdr-status {
        width: auto;
        color: #3a3a4a;
        text-align: right;
    }

    /* ── streams panel ── */
    #streams {
        height: 1fr;
        padding: 1 2;
    }
    #streams-hdr {
        height: 1;
        layout: horizontal;
        margin-bottom: 1;
    }
    #streams-lbl {
        width: auto;
        color: #3a3a4a;
        text-style: bold;
    }
    #streams-count {
        width: 1fr;
        color: #3a3a4a;
        text-align: right;
    }
    DataTable {
        background: transparent;
        height: 1fr;
    }
    DataTable > .datatable--header {
        background: #0c0c10;
        color: #2e3440;
        text-style: none;
        height: 1;
    }
    DataTable > .datatable--cursor {
        background: #141420;
    }
    DataTable > .datatable--hover {
        background: #111118;
    }

    /* ── log panel ── */
    #logs {
        height: 10;
        border-top: solid #181820;
        padding: 0 2;
    }
    #logs.hidden {
        display: none;
    }
    #logs-hdr {
        height: 1;
        layout: horizontal;
        padding-top: 1;
        margin-bottom: 0;
    }
    #logs-lbl {
        width: 1fr;
        color: #3a3a4a;
        text-style: bold;
    }
    #logs-hint {
        width: auto;
        color: #2e3440;
    }
    RichLog {
        background: transparent;
        height: 1fr;
        scrollbar-color: #2a2a3a #0c0c10;
    }

    /* ── footer ── */
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

    def __init__(self, query: str) -> None:
        super().__init__()
        self._query      = query
        self._match:      LiveMatch | None      = None
        self._candidates: list[StreamCandidate] = []
        self._current:    StreamCandidate | None = None
        self._mpv        = MpvController()
        self._running    = True
        self._logs_vis   = True

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Container(id="hdr"):
            yield Static("◉  streamer", id="hdr-logo")
            yield Static("",            id="hdr-match")
            yield Static("discovering…", id="hdr-status")

        with Container(id="streams"):
            with Container(id="streams-hdr"):
                yield Static("STREAMS", id="streams-lbl")
                yield Static("",        id="streams-count")
            yield DataTable(id="streams-table", show_cursor=False, show_header=True)

        with Container(id="logs"):
            with Container(id="logs-hdr"):
                yield Static("LOGS",     id="logs-lbl")
                yield Static("l · hide", id="logs-hint")
            yield RichLog(id="log-output", highlight=False, markup=False, wrap=False)

        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one("#streams-table", DataTable)
        t.add_column("",        width=3,  key="play")
        t.add_column("stream",  width=46, key="label")
        t.add_column("status",  width=16, key="status")
        t.add_column("viewers", width=9,  key="viewers")
        self.set_interval(0.15, self._flush_logs)
        self._discover()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _flush_logs(self) -> None:
        if not self._logs_vis:
            return
        w = self.query_one("#log-output", RichLog)
        for line in _log_handler.drain():
            w.write(line)

    def _set_status(self, text: str) -> None:
        self.query_one("#hdr-status", Static).update(text)

    def _set_match_name(self, name: str) -> None:
        self.query_one("#hdr-match", Static).update(name)

    def _refresh_table(self) -> None:
        t = self.query_one("#streams-table", DataTable)
        t.clear()

        alive = sum(1 for c in self._candidates if c.alive)
        total = len(self._candidates)
        self.query_one("#streams-count", Static).update(
            f"{alive}/{total} alive" if total else ""
        )

        for c in self._candidates:
            playing = self._current is not None and c.label == self._current.label

            # Play indicator
            play_cell = Text("▶", style="#6e5fed") if playing else Text(" ")

            # Label
            if playing:
                label_cell = Text(c.label, style="bold #c8c8d8")
            elif c.alive:
                label_cell = Text(c.label, style="#7a8899")
            else:
                label_cell = Text(c.label, style="#2e3440")

            # Status
            if c.resolve_attempts == 0 and c.failures == 0 and not c.resolved:
                status_cell = Text("…  pending", style="#3a3a4a")
            elif c.alive:
                ms = c.latency_ms
                ms_color = (
                    "#3dd68c" if ms < 200 else
                    "#f4b942" if ms < 500 else
                    "#f06f6f"
                )
                status_cell = Text()
                status_cell.append("✓  ", style="#3dd68c")
                status_cell.append(f"{ms}ms", style=ms_color)
            elif c.resolve_attempts > 0 or c.failures > 0:
                status_cell = Text("✗  dead", style="#f06f6f")
            else:
                status_cell = Text("…  pending", style="#3a3a4a")

            # Viewers
            if c.viewers:
                v = c.viewers
                viewers_str = f"{v/1000:.1f}k" if v >= 1000 else str(v)
                viewers_cell = Text(viewers_str, style="#3a3a4a")
            else:
                viewers_cell = Text("")

            t.add_row(play_cell, label_cell, status_cell, viewers_cell)

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_toggle_logs(self) -> None:
        self._logs_vis = not self._logs_vis
        logs = self.query_one("#logs")
        hint = self.query_one("#logs-hint", Static)
        if self._logs_vis:
            logs.remove_class("hidden")
            hint.update("l · hide")
        else:
            logs.add_class("hidden")
            hint.update("l · show")

    def action_refresh_now(self) -> None:
        if self._candidates:
            self._force_probe()

    async def action_quit(self) -> None:
        self._running = False
        try:
            if self._mpv.proc:
                self._mpv.proc.terminate()
        except Exception:
            pass
        self.exit()

    # ── Workers ───────────────────────────────────────────────────────────────

    @work(thread=True)
    def _discover(self) -> None:
        log = logging.getLogger(__name__)
        log.info("Discovering live matches…")
        matches = discover_matches(self._query)
        self.call_from_thread(self._on_matches_ready, matches)

    def _on_matches_ready(self, matches: list[LiveMatch]) -> None:
        if not matches:
            self._set_status("no matches found")
            self.exit()
            return
        if len(matches) == 1:
            self._select_match(matches[0])
        else:
            self.push_screen(MatchSelectScreen(matches), self._select_match)

    def _select_match(self, match: LiveMatch | None) -> None:
        if match is None:
            return
        self._match = match
        self._set_match_name(match.title)
        self._set_status("loading streams…")
        self._load_and_probe()

    @work(thread=True)
    def _load_and_probe(self) -> None:
        log = logging.getLogger(__name__)
        assert self._match is not None

        log.info("Loading stream candidates…")
        candidates = load_candidates(self._match)
        if not candidates:
            self.call_from_thread(self._set_status, "no streams found")
            return

        self._candidates = candidates
        self.call_from_thread(self._refresh_table)

        log.info(f"Probing {len(candidates)} stream(s) in batches of {PROBE_BATCH}…")
        for i in range(0, len(candidates), PROBE_BATCH):
            probe_all(candidates[i : i + PROBE_BATCH])
            self.call_from_thread(self._refresh_table)
            if i == 0:
                self.call_from_thread(self._maybe_start_playing)

        self.call_from_thread(self._after_all_probed)

    @work(thread=True)
    def _force_probe(self) -> None:
        log = logging.getLogger(__name__)
        log.info("Manual refresh — re-probing…")
        probe_all(self._candidates, resolve_if_dead=False)
        self.call_from_thread(self._refresh_table)

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
        assert self._match is not None
        try:
            self._mpv.launch(c.resolved, title=self._match.title, referrer=c.embed_url)
            self._mpv.osd(f"▶ {c.label}  ({c.latency_ms}ms)")
        except Exception as exc:
            log.error(f"mpv launch failed: {exc}")

    def _after_all_probed(self) -> None:
        self._refresh_table()
        alive = [c for c in self._candidates if c.alive]
        if not alive:
            self._set_status("no working streams")
            return
        self._maybe_start_playing()
        threading.Thread(
            target=self._monitor_loop, daemon=True, name="monitor"
        ).start()

    # ── Monitor loop ──────────────────────────────────────────────────────────

    def _monitor_loop(self) -> None:
        log = logging.getLogger(__name__)
        while self._running:
            time.sleep(CHECK_EVERY)
            if not self._running:
                break

            if not self._mpv.is_alive():
                log.warning("mpv closed — re-launching…")
                if self._current and self._current.resolved:
                    assert self._match is not None
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
            self.call_from_thread(self._refresh_table)

            if not self._current:
                continue

            best = max(alive, key=lambda c: c.score()) if alive else None
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
                assert self._match is not None
                try:
                    self._mpv.launch(
                        best.resolved, title=self._match.title, referrer=best.embed_url
                    )
                    self._mpv.osd(f"↷ {best.label}  ({best.latency_ms}ms)")
                except Exception as exc:
                    log.error(f"mpv switch: {exc}")
                self.call_from_thread(self._set_status, f"▶  {best.label}")
                self.call_from_thread(self._refresh_table)
            elif current_dead:
                log.warning("All streams dead — waiting for recovery…")
                self.call_from_thread(self._set_status, "⚠  all streams dead")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    args  = sys.argv[1:]
    query = " ".join(a for a in args if not a.startswith("--"))
    _setup_logging()
    StreamerApp(query=query).run()


if __name__ == "__main__":
    main()
