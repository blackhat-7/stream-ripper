import logging
import sys
import threading
import time

from _streamer.models import LiveMatch
from _streamer.player import MpvController
from _streamer.sources import discover_matches
from _streamer.watch import Watcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

TABLE_EVERY = 5  # seconds; min gap between stream-table prints


def stream_table(w: Watcher) -> str:
    rows = []
    for c in w.candidates:
        playing = "▶" if c is w.current else " "
        status = f"✓ {c.latency_ms}ms" if c.status == "alive" else c.status
        viewers = f"  ({c.viewers}v)" if c.viewers else ""
        rows.append(f"│ {playing} {c.label:<44} {status}{viewers}")
    return "┌─ Streams " + "─" * 50 + "\n" + "\n".join(rows) + "\n└" + "─" * 60


def pick_match(matches: list[LiveMatch]) -> LiveMatch:
    if len(matches) == 1:
        log.info(f"One match: {matches[0].title}")
        return matches[0]
    print("\n  Live matches:\n")
    for i, m in enumerate(matches, 1):
        print(f"  [{i:2}] {m.title}  — {m.source_site}")
    print()
    while True:
        try:
            idx = int(input("  Select (number): ").strip()) - 1
            if 0 <= idx < len(matches):
                return matches[idx]
        except (ValueError, KeyboardInterrupt, EOFError):
            sys.exit(0)
        print("  Invalid.")


def print_changes(w: Watcher, changed: threading.Event) -> None:
    """Print the status line whenever it changes; the stream table at most every TABLE_EVERY s."""
    last_status, last_table, table_at = "", "", 0.0
    while True:
        changed.wait()
        changed.clear()
        status = f"[{w.phase}] {w.message}"
        if status != last_status:
            print(status, flush=True)
            last_status = status
        table = stream_table(w)
        if table != last_table and time.monotonic() - table_at >= TABLE_EVERY:
            print(table, flush=True)
            last_table, table_at = table, time.monotonic()
        if table != last_table:
            changed.set()  # print the pending table once TABLE_EVERY has passed
        time.sleep(0.5)  # throttle


def read_commands(w: Watcher) -> None:
    for line in sys.stdin:
        cmd = line.strip().lower()
        if cmd == "q":
            return
        if cmd == "r":
            w.reprobe()
        else:
            w.next_stream()
    threading.Event().wait()  # stdin closed (headless): run until Ctrl+C


def main() -> None:
    args  = sys.argv[1:]
    query = " ".join(a for a in args if not a.startswith("--"))

    log.info("Finding live matches…")
    matches = discover_matches(query)
    if not matches:
        sys.exit("No live matches found" + (f" for '{query}'" if query else "") +
                 ". Try again closer to kick-off.")

    if "--list" in args:
        for m in matches:
            print(f"  {m.title}  [{m.source_site}]")
        return

    match = pick_match(matches)
    print(f"\n  {match.title}\n")
    print("  enter = next stream / resume · r = re-probe · q or Ctrl+C = quit\n")

    changed = threading.Event()
    w = Watcher(match, MpvController(), changed.set)
    threading.Thread(target=print_changes, args=(w, changed), daemon=True).start()
    w.start()
    try:
        read_commands(w)
    except KeyboardInterrupt:
        pass
    log.info("Shutting down.")
    w.stop()


if __name__ == "__main__":
    main()
