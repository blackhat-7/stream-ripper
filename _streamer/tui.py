#!/usr/bin/env python3
"""Textual TUI for stream-ripper: pick a live match, watch the best stream."""
from __future__ import annotations

import collections
import logging
import sys
import time
from datetime import datetime

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.widgets import DataTable, Input, RichLog, Static

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.player import MpvController
from _streamer.sources import discover_matches
from _streamer.watch import Watcher

log = logging.getLogger(__name__)

# ─── Kanagawa Dragon ───────────────────────────────────────────────────────────
# Single source of truth: exposed to CSS as $variables, and used by Rich cells.

C = {
    "dragon-black0": "#0d0c0c", "dragon-black1": "#12120f", "dragon-black2": "#1D1C19",
    "dragon-black3": "#181616", "dragon-black4": "#282727", "dragon-black5": "#393836",
    "dragon-black6": "#625e5a", "dragon-white": "#c5c9c5", "old-white": "#C8C093",
    "fuji-gray": "#727169", "dragon-gray": "#a6a69c", "dragon-gray2": "#9e9b93",
    "dragon-gray3": "#7a8382", "dragon-red": "#c4746e", "dragon-green": "#87a987",
    "dragon-green2": "#8a9a7b", "dragon-yellow": "#c4b28a", "dragon-orange": "#b6927b",
    "dragon-blue2": "#8ba4b0", "dragon-aqua": "#8ea4a2", "dragon-violet": "#8992a7",
    "dragon-pink": "#a292a3", "dragon-ash": "#737c73", "samurai-red": "#E82424",
    "ronin-yellow": "#FF9E3B", "wave-blue1": "#223249", "wave-blue2": "#2D4F67",
}

THEME = Theme(
    name="kanagawa-dragon",
    dark=True,
    primary=C["dragon-blue2"], secondary=C["dragon-violet"], accent=C["dragon-yellow"],
    foreground=C["dragon-white"], background=C["dragon-black3"], surface=C["dragon-black3"],
    panel=C["dragon-black4"], success=C["dragon-green"], warning=C["dragon-yellow"],
    error=C["dragon-red"],
    variables={
        **C,
        "block-cursor-background": C["wave-blue2"],
        "block-cursor-foreground": C["dragon-white"],
        "block-cursor-text-style": "none",
        "block-cursor-blurred-background": C["dragon-black4"],
        "block-cursor-blurred-foreground": C["dragon-white"],
        "block-cursor-blurred-text-style": "none",
        "block-hover-background": C["dragon-black2"],
        "input-cursor-background": C["old-white"],
        "input-selection-background": C["wave-blue2"],
    },
)

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def spin() -> str:
    return SPINNER[int(time.monotonic() * 10) % len(SPINNER)]


def keyed(items: list, key) -> dict:
    """Map items to unique, stable row keys (duplicates get a ' suffix)."""
    out: dict = {}
    for item in items:
        k = key(item)
        while k in out:
            k += "'"
        out[k] = item
    return out


def fmt_viewers(n: int) -> str:
    return "" if not n else f"{n / 1000:.1f}k" if n >= 1000 else str(n)


# ─── Log capture ───────────────────────────────────────────────────────────────

class LogBuffer(logging.Handler):
    """Keeps the last lines of logging as styled Text; panes pull by sequence number."""

    COLORS = {"DEBUG": "dragon-black6", "INFO": "dragon-blue2", "WARNING": "dragon-yellow",
              "ERROR": "dragon-red", "CRITICAL": "samurai-red"}

    def __init__(self) -> None:
        super().__init__()
        self.lines: collections.deque[tuple[int, Text]] = collections.deque(maxlen=1000)
        self.seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = Text(no_wrap=True)
            line.append(datetime.fromtimestamp(record.created).strftime("%H:%M:%S  "), C["dragon-black6"])
            line.append(f"{record.levelname[:4]:<4}  ", C[self.COLORS.get(record.levelname, "fuji-gray")])
            line.append(record.getMessage(), C["dragon-gray2"])
            self.seq += 1
            self.lines.append((self.seq, line))
        except Exception:
            self.handleError(record)


LOGS = LogBuffer()


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [LOGS]
    for lib in ("urllib3", "asyncio", "playwright", "httpx"):
        logging.getLogger(lib).setLevel(logging.WARNING)


class LogPane(Vertical):
    """Collapsible log view (hidden by default)."""

    def __init__(self) -> None:
        super().__init__(classes="hidden")
        self._seen = 0

    def compose(self) -> ComposeResult:
        yield Static("LOGS", classes="section")
        yield RichLog(highlight=False, markup=False, wrap=False, max_lines=1000)

    def on_mount(self) -> None:
        self.set_interval(0.25, self._pull)

    def toggle(self) -> None:
        self.toggle_class("hidden")
        self._pull()

    def _pull(self) -> None:
        if self.has_class("hidden"):
            return
        out = self.query_one(RichLog)
        for seq, line in list(LOGS.lines):
            if seq > self._seen:
                out.write(line)
                self._seen = seq


# ─── Shared widgets ────────────────────────────────────────────────────────────

class HintBar(Horizontal):
    """Persistent, context-aware key hints."""

    def compose(self) -> ComposeResult:
        yield Static(id="hints")
        yield Static(self._keys([("?", "help"), ("q", "quit")]), id="hints-right")

    @staticmethod
    def _keys(pairs: list[tuple[str, str]]) -> Text:
        t = Text()
        for key, label in pairs:
            t.append(key, f"bold {C['old-white']}").append(f" {label}   ", C["fuji-gray"])
        return t

    def show(self, *pairs: tuple[str, str]) -> None:
        self.query_one("#hints", Static).update(self._keys(list(pairs)))


class VimTable(DataTable):
    """DataTable with vim motions: j/k, gg/G, ctrl+d/u/f/b, l to select, count prefixes (5j)."""

    def __init__(self, **kwargs) -> None:
        super().__init__(cursor_type="row", zebra_stripes=False, **kwargs)
        self._count = ""
        self._g = False

    def on_key(self, event: events.Key) -> None:
        key = event.character if event.is_printable else event.key
        if key and key.isdigit() and (self._count or key != "0"):
            self._count += key
            event.stop()
            return
        if key == "g" and not self._g:
            self._g = True
            event.stop()
            return
        count, was_g = int(self._count or 0), self._g
        self._count, self._g = "", False
        n, row, last = count or 1, self.cursor_row, self.row_count - 1
        page = max(1, self.scrollable_content_region.height - 1)
        target = {
            "j": row + n, "k": row - n,
            "ctrl+d": row + page // 2, "ctrl+u": row - page // 2,
            "ctrl+f": row + page, "ctrl+b": row - page,
            "G": count - 1 if count else last,
            "g": (count - 1 if count else 0) if was_g else None,
        }.get(key)
        if target is not None:
            self.move_cursor(row=max(0, min(last, target)))
        elif key == "l":
            self.action_select_cursor()
        else:
            return
        event.stop()

    def sync(self, columns: list[tuple[str, str, int | None]], rows: list[tuple[str, list]],
             max_flex: int) -> None:
        """Show `rows` (key, cells) without flicker.

        Same keys and widths: changed cells are updated in place. Otherwise the table is rebuilt,
        keeping the cursor on the same row key and the scroll position unchanged.
        The column with width None takes the free space (up to `max_flex`); the last column
        absorbs the rest, so the table always spans the full width.
        """
        width = self.size.width or self.app.size.width - 4
        free = width - 1 - sum(2 * self.cell_padding + (w or 0) for _, _, w in columns)
        flex = max(12, min(free, max_flex))
        widths = [flex if w is None else w for _, _, w in columns]
        widths[-1] += max(0, free - flex)
        keys = [k for k, _ in rows]
        if keys == [r.key.value for r in self.ordered_rows] and widths == [c.width for c in self.ordered_columns]:
            for key, cells in rows:
                for (col, _, _), cell in zip(columns, cells):
                    if self.get_cell(key, col) != cell:
                        self.update_cell(key, col, cell)
            return
        keep = self.coordinate_to_cell_key(self.cursor_coordinate).row_key.value if self.row_count else None
        y = self.scroll_y
        self.clear(columns=True)
        for (key, label, _), w in zip(columns, widths):
            self.add_column(Text(label, C["dragon-gray3"]), key=key, width=w)
        for key, cells in rows:
            self.add_row(*cells, key=key)
        if keep in keys:
            self.move_cursor(row=keys.index(keep), scroll=False)
        self.call_after_refresh(self.scroll_to, y=y, animate=False)


# ─── Matches screen ────────────────────────────────────────────────────────────

class MatchesScreen(Screen[None]):
    BINDINGS = [
        Binding("slash", "search", "search"),
        Binding("escape", "escape", "clear"),
        Binding("r", "refresh", "refresh"),
    ]
    REFRESH_EVERY = 60

    def __init__(self, query: str) -> None:
        super().__init__()
        self.query_text = query
        self.matches: list[LiveMatch] = []
        self.fetching = False
        self.error = ""
        self.updated_at = 0.0
        self.updated_clock = ""
        self._by_key: dict[str, LiveMatch] = {}
        self._width = 0

    def compose(self) -> ComposeResult:
        with Vertical(classes="body"):
            with Horizontal(classes="topbar"):
                yield Static(id="brand")
                yield Static(id="status")
            with Horizontal(id="search-box"):
                yield Static("/", id="search-icon")
                yield Input(placeholder="Search matches and leagues", id="search")
            yield VimTable(id="table")
            yield Static(id="empty")
            yield LogPane()
        yield HintBar()

    @property
    def table(self) -> VimTable:
        return self.query_one(VimTable)

    def on_mount(self) -> None:
        brand = Text("◆ ", C["dragon-red"]).append("stream-ripper", f"bold {C['old-white']}")
        if self.query_text:
            brand.append(f"  ›  {self.query_text}", C["fuji-gray"])
        self.query_one("#brand", Static).update(brand)
        self.set_interval(0.1, self._tick)
        self.set_interval(self.REFRESH_EVERY, self._auto_refresh)
        self.action_refresh()

    def on_screen_resume(self) -> None:
        self._hints()
        if time.monotonic() - self.updated_at > self.REFRESH_EVERY:
            self.action_refresh()

    def on_resize(self, event: events.Resize) -> None:
        if event.size.width != self._width:
            self._width = event.size.width
            self.call_after_refresh(self._render_rows)

    # ── data ──────────────────────────────────────────────────────────────────

    def _auto_refresh(self) -> None:
        if self.is_current:
            self.action_refresh()

    def action_refresh(self) -> None:
        if not self.fetching:
            self.fetching = True
            self._discover()

    @work(thread=True, exit_on_error=False)
    def _discover(self) -> None:
        suffix = f" for {self.query_text!r}" if self.query_text else ""
        log.info(f"Discovering live matches{suffix}…")
        try:
            matches, error = discover_matches(self.query_text), ""
        except Exception as exc:
            log.exception("Discovery failed")
            matches, error = None, str(exc) or type(exc).__name__
        self.app.call_from_thread(self._loaded, matches, error)

    def _loaded(self, matches: list[LiveMatch] | None, error: str) -> None:
        self.fetching, self.error = False, error
        if matches is not None:
            self.matches, self.updated_at = matches, time.monotonic()
            self.updated_clock = datetime.now().strftime("%H:%M")
        self._render_rows()

    def visible(self) -> list[LiveMatch]:
        words = self.query_one("#search", Input).value.lower().split()
        return [m for m in self.matches
                if all(w in f"{m.title} {m.league} {' '.join(m.sites)}".lower() for w in words)]

    # ── rendering ─────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if self.fetching:
            self._render_status()
            if not self.matches:
                self._render_empty()

    def _render_status(self) -> None:
        t = Text()
        if self.fetching:
            t.append(f"{spin()} {'refreshing' if self.matches else 'searching'}", C["dragon-yellow"])
        elif self.error and not self.matches:
            t.append("● offline", C["dragon-red"])
        elif self.updated_at:
            t.append("● ", C["dragon-red"]).append(f"{len(self.matches)} live", C["dragon-white"])
            t.append(f"   updated {self.updated_clock}", C["fuji-gray"])
        self.query_one("#status", Static).update(t)

    def _render_rows(self) -> None:
        self._render_status()
        rows = self.visible()
        # Group by league in order of first appearance; show the league once per group.
        order = list(dict.fromkeys(m.league for m in rows))
        rows.sort(key=lambda m: order.index(m.league))
        self._by_key = keyed(rows, lambda m: f"{m.sport}:{m.title.lower()}")
        out, prev = [], None
        for key, m in self._by_key.items():
            n = len(m.sites)
            league = Text(m.league if m.league != prev else "", C["dragon-violet"])
            badge = Text(f"{n} site{'s' if n > 1 else ''}", C["dragon-aqua"] if n > 1 else C["fuji-gray"])
            out.append((key, [league, Text(m.title, C["dragon-white"]), badge]))
            prev = m.league
        columns = [("league", "LEAGUE", 22), ("title", "MATCH", None), ("sites", "SOURCES", 9)]
        self.table.sync(columns, out, max_flex=60)
        self._render_empty()
        self._hints()

    def _render_empty(self) -> None:
        empty = self.query_one("#empty", Static)
        has_rows = self.table.row_count > 0
        self.table.display, empty.display = has_rows, not has_rows
        if has_rows:
            if self.focused is None:
                self.table.focus()
            return
        search = self.query_one("#search", Input).value
        if self.fetching:
            msg, hint = f"{spin()}  Finding live matches…", "this can take a few seconds"
        elif self.error:
            msg, hint = "Couldn't reach the match sources", "press r to retry  ·  L for logs"
        elif search and self.matches:
            msg, hint = f"Nothing matches “{search}”", "press esc to clear the search"
        else:
            msg, hint = "No live matches right now", "press r to refresh"
        empty.update(Text(msg, C["old-white"]).append(f"\n\n{hint}", C["fuji-gray"]))

    def _hints(self) -> None:
        bar = self.query_one(HintBar)
        if self.query_one("#search", Input).has_focus:
            bar.show(("enter", "done"), ("esc", "cancel"))
        else:
            bar.show(("j/k", "move"), ("enter", "watch"), ("/", "search"), ("r", "refresh"), ("L", "logs"))

    # ── input ─────────────────────────────────────────────────────────────────

    def on_descendant_focus(self) -> None:
        self._hints()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._render_rows()
        if self.table.row_count:
            self.table.move_cursor(row=0)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.table.focus()

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_escape(self) -> None:
        search = self.query_one("#search", Input)
        if search.value:
            search.value = ""
        self.table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        match = self._by_key.get(event.row_key.value)
        if match:
            self.app.push_screen(WatchScreen(match))


# ─── Watching screen ───────────────────────────────────────────────────────────

PHASES = {  # phase -> (pill label, color, animated)
    "loading": ("LOADING", "dragon-blue2", True),
    "probing": ("PROBING", "dragon-yellow", True),
    "playing": ("PLAYING", "dragon-green", False),
    "switching": ("SWITCHING", "dragon-orange", True),
    "dead": ("ALL DEAD", "dragon-red", False),
    "no-streams": ("NO STREAMS", "dragon-red", False),
    "stopped": ("STOPPED", "fuji-gray", False),
}
STATUS_ORDER = {"alive": 0, "probing": 1, "pending": 2, "dead": 3}


def latency_cell(c: StreamCandidate) -> Text:
    if c.status == "probing":
        return Text("probing…", C["dragon-gray3"])
    if c.status == "pending":
        return Text("—", C["dragon-black6"])
    if c.status == "dead":
        return Text("offline", C["dragon-red"])
    ms = c.latency_ms
    color = C["dragon-green"] if ms < 200 else C["dragon-yellow"] if ms < 500 else C["dragon-red"]
    filled = 5 if ms < 120 else 4 if ms < 200 else 3 if ms < 350 else 2 if ms < 500 else 1
    return Text("━" * filled, color).append("━" * (5 - filled), C["dragon-black5"]).append(f" {ms:>4} ms", color)


class WatchScreen(Screen[None]):
    BINDINGS = [
        Binding("h,escape", "back", "back"),
        Binding("n", "next_stream", "next stream"),
        Binding("r", "reprobe", "reprobe"),
    ]

    def __init__(self, match: LiveMatch) -> None:
        super().__init__()
        self.match = match
        self.watcher: Watcher | None = None
        self._dirty = True
        self._shape: tuple = ()
        self._order: list[str] = []
        self._by_key: dict[str, StreamCandidate] = {}

    def compose(self) -> ComposeResult:
        with Vertical(classes="body"):
            with Horizontal(classes="topbar"):
                yield Static(Text("◆ ", C["dragon-red"]).append("stream-ripper", f"bold {C['old-white']}")
                             .append("  ›  watching", C["fuji-gray"]), id="brand")
                yield Static(Text(self.match.league, C["dragon-violet"]), id="status")
            with Vertical(id="now") as now:
                now.border_title = "NOW PLAYING"
                yield Static(id="now-title")
                yield Static(id="now-stream")
                yield Static(id="now-message")
            with Horizontal(classes="section-row"):
                yield Static("STREAMS", classes="section")
                yield Static(id="counts")
            yield VimTable(id="table")
            yield Static(id="empty")
            yield LogPane()
        yield HintBar()

    @property
    def table(self) -> VimTable:
        return self.query_one(VimTable)

    def on_mount(self) -> None:
        self.watcher = Watcher(self.match, self.app.mpv, self._changed)
        self.watcher.start()
        self.set_interval(0.1, self._tick)
        self._tick()

    def on_unmount(self) -> None:
        self.stop_watcher()

    def stop_watcher(self) -> None:
        if self.watcher:
            watcher, self.watcher = self.watcher, None
            watcher.stop()

    def _changed(self) -> None:
        # Called from watcher threads: just mark dirty; the 10 Hz tick redraws.
        self._dirty = True

    def on_resize(self, event: events.Resize) -> None:
        self._dirty = True

    # ── rendering ─────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        w = self.watcher
        if w is None:
            return
        cands = list(w.candidates)
        animated = PHASES.get(w.phase, ("", "", False))[2] or any(c.status == "probing" for c in cands)
        if not (self._dirty or animated):
            return
        self._dirty = False
        self._render_card(w, cands)
        self._render_table(w, cands)
        self._render_empty(w)
        self._hints(w)

    def _render_card(self, w: Watcher, cands: list[StreamCandidate]) -> None:
        label, color, animated = PHASES.get(w.phase, (w.phase.upper(), "fuji-gray", False))
        icon = spin() if animated else "●"
        title = Text(self.match.title, f"bold {C['old-white']}")
        line = Text().append(f" {icon} {label} ", f"bold {C['dragon-black0']} on {C[color]}").append("   ")
        cur = w.current
        if cur:
            line.append(cur.label, f"bold {C['dragon-white']}")
            if cur.site:
                line.append(f"  ·  {cur.site}", C["fuji-gray"])
            if cur.alive:
                line.append("  ·  ", C["fuji-gray"]).append_text(latency_cell(cur))
        else:
            line.append("waiting for a working stream…", C["fuji-gray"])
        self.query_one("#now-title", Static).update(title)
        self.query_one("#now-stream", Static).update(line)
        self.query_one("#now-message", Static).update(Text(w.message, C["dragon-gray3"]))
        alive = sum(c.alive for c in cands)
        counts = Text(f"{alive}", C["dragon-green"] if alive else C["dragon-red"])
        counts.append(f" alive  ·  {len(cands)} total", C["fuji-gray"])
        self.query_one("#counts", Static).update(counts if cands else "")

    def _row(self, c: StreamCandidate, playing: bool) -> list[Text]:
        dot = {"alive": ("●", "dragon-green"), "probing": (spin(), "dragon-yellow"),
               "pending": ("○", "dragon-black6"), "dead": ("●", "dragon-red")}[c.status]
        label = Text("▶ " if playing else "  ", C["dragon-green"])
        style = (f"bold {C['old-white']}" if playing else C["dragon-white"] if c.alive
                 else C["dragon-black6"] if c.status == "dead" else C["dragon-gray3"])
        label.append(c.label, style)
        return [Text(dot[0], C[dot[1]]), label, Text(c.site, C["fuji-gray"]),
                latency_cell(c), Text(fmt_viewers(c.viewers), C["dragon-gray3"])]

    def _render_table(self, w: Watcher, cands: list[StreamCandidate]) -> None:
        self._by_key = keyed(cands, lambda c: f"{c.site}|{c.label}|{c.embed_url}")
        # Re-sort only when the row set or the phase changes, so rows don't jump under the cursor.
        shape = (tuple(self._by_key), w.phase)
        if shape != self._shape:
            self._shape = shape
            self._order = sorted(self._by_key, key=lambda k: (STATUS_ORDER[self._by_key[k].status],
                                                              self._by_key[k].latency_ms))
        columns = [("dot", "", 1), ("label", "  STREAM", None), ("site", "SITE", 14),
                   ("latency", "LATENCY", 13), ("viewers", "VIEWERS", 7)]
        rows = [(k, self._row(self._by_key[k], self._by_key[k] is w.current)) for k in self._order]
        self.table.sync(columns, rows, max_flex=48)

    def _render_empty(self, w: Watcher) -> None:
        empty = self.query_one("#empty", Static)
        has_rows = self.table.row_count > 0
        self.table.display, empty.display = has_rows, not has_rows
        if has_rows:
            if self.focused is None:
                self.table.focus()
            return
        if w.phase == "no-streams":
            msg, hint = "No streams found for this match", "press h to pick another match  ·  r to retry"
        else:
            msg, hint = f"{spin()}  Finding streams…", "resolving sources for this match"
        empty.update(Text(msg, C["old-white"]).append(f"\n\n{hint}", C["fuji-gray"]))

    def _hints(self, w: Watcher) -> None:
        bar = self.query_one(HintBar)
        if w.phase in ("dead", "no-streams"):
            bar.show(("r", "reprobe"), ("h", "back"), ("L", "logs"))
        else:
            bar.show(("enter", "play"), ("n", "next stream"), ("r", "reprobe"), ("h", "back"))

    # ── input ─────────────────────────────────────────────────────────────────

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        c = self._by_key.get(event.row_key.value)
        if c and self.watcher:
            self.watcher.play(c)

    def action_back(self) -> None:
        self.stop_watcher()
        self.app.pop_screen()

    def action_next_stream(self) -> None:
        if self.watcher:
            self.watcher.next_stream()

    def action_reprobe(self) -> None:
        if self.watcher:
            self.watcher.reprobe()


# ─── Help overlay ──────────────────────────────────────────────────────────────

HELP = (
    [("Move", [("j k ↑ ↓", "down / up"), ("5j", "count prefix"), ("gg G", "top / bottom"),
               ("ctrl+d ctrl+u", "half page"), ("ctrl+f ctrl+b", "full page")]),
     ("Anywhere", [("L ctrl+l", "toggle logs"), ("?", "this help"), ("q", "quit")])],
    [("Matches", [("enter l", "watch match"), ("/", "search"), ("esc", "clear search"),
                  ("r", "refresh (auto 60s)")]),
     ("Watching", [("enter l", "play stream"), ("n", "next best stream"), ("r", "re-probe all"),
                   ("h esc", "back to matches")])],
)


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape,question_mark,q,enter", "app.pop_screen", "close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            with Horizontal():
                for sections in HELP:
                    t = Text()
                    for i, (section, rows) in enumerate(sections):
                        t.append(("\n" if i else "") + section.upper() + "\n", f"bold {C['dragon-gray3']}")
                        for keys, what in rows:
                            t.append(f"{keys:<15}", C["dragon-yellow"]).append(f"{what}\n", C["dragon-white"])
                    yield Static(t, classes="help-col")
            yield Static(Text("esc to close", C["fuji-gray"]), id="help-foot")

    def on_mount(self) -> None:
        self.query_one("#help").border_title = "KEYS"


# ─── App ───────────────────────────────────────────────────────────────────────

class StreamerApp(App[None]):
    TITLE = "stream-ripper"
    ENABLE_COMMAND_PALETTE = False
    AUTO_FOCUS = None  # each screen focuses its table once it has rows
    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("ctrl+c", "quit", "quit", priority=True, show=False),
        Binding("question_mark", "help", "help"),
        Binding("L,ctrl+l", "toggle_logs", "logs"),
    ]
    CSS = """
    Screen { background: $dragon-black3; color: $dragon-white; }
    .body { height: 1fr; padding: 1 2 0 2; }
    .topbar { height: 1; margin-bottom: 1; }
    #brand { width: 1fr; }
    #status { width: auto; }
    .section { color: $dragon-gray3; text-style: bold; width: auto; }
    .section-row { height: 1; margin: 1 0 0 1; }
    #counts { width: 1fr; text-align: right; padding-right: 1; }

    #search-box { height: 3; background: $dragon-black2; padding: 1 2; margin-bottom: 1; }
    #search-box:focus-within { background: $dragon-black4; }
    #search-icon { width: 3; color: $fuji-gray; }
    #search-box:focus-within #search-icon { color: $dragon-yellow; }
    #search { border: none; height: 1; padding: 0; background: transparent; color: $old-white; }
    #search:focus { border: none; background-tint: transparent 0%; }
    #search > .input--placeholder { color: $dragon-black6; }

    #now { height: 5; border: round $dragon-black5; border-title-color: $dragon-gray3;
           border-title-style: bold; padding: 0 2; }
    #now-stream { margin: 0; }

    VimTable { height: 1fr; background: $dragon-black3; margin-top: 1; overflow-x: hidden;
               scrollbar-size-vertical: 1; scrollbar-background: $dragon-black3;
               scrollbar-color: $dragon-black5; scrollbar-color-hover: $dragon-black6;
               scrollbar-color-active: $fuji-gray; }
    VimTable:focus { background-tint: transparent 0%; }
    VimTable > .datatable--header, VimTable:focus > .datatable--header {
        background: $dragon-black3; color: $dragon-gray3; text-style: bold; background-tint: transparent 0%; }
    #empty { height: 1fr; content-align: center middle; text-align: center; }

    LogPane { height: 12; border-top: solid $dragon-black5; padding-top: 1; }
    LogPane.hidden { display: none; }
    LogPane RichLog { background: $dragon-black3; scrollbar-size-vertical: 1;
                      scrollbar-background: $dragon-black3; scrollbar-color: $dragon-black5; }

    HintBar { height: 1; background: $dragon-black1; padding: 0 2; }
    #hints { width: 1fr; }
    #hints-right { width: auto; }

    HelpScreen { align: center middle; background: $dragon-black0 70%; padding: 0; }
    #help { width: 76; height: auto; max-height: 100%; background: $dragon-black2;
            border: round $dragon-black5; border-title-color: $dragon-yellow;
            border-title-style: bold; padding: 1 3; }
    #help Horizontal { height: auto; }
    .help-col { width: 1fr; }
    #help-foot { margin-top: 1; }
    """

    def __init__(self, query: str) -> None:
        super().__init__()
        self.search_query = query
        self.mpv = MpvController()
        self.register_theme(THEME)
        self.theme = THEME.name

    def get_default_screen(self) -> Screen:
        return MatchesScreen(self.search_query)

    def action_help(self) -> None:
        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen())

    def action_toggle_logs(self) -> None:
        for pane in self.screen.query(LogPane):
            pane.toggle()

    async def action_quit(self) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, WatchScreen):
                screen.stop_watcher()
        self.exit()


def main() -> None:
    query = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
    setup_logging()
    StreamerApp(query=query).run()


if __name__ == "__main__":
    main()
