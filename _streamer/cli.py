import logging
import signal
import sys
import threading
import time

from _streamer.models import LiveMatch, StreamCandidate
from _streamer.player import MpvController
from _streamer.probe import probe, probe_all
from _streamer.settings import CHECK_EVERY, SWITCH_MARGIN
from _streamer.sources import discover_matches, load_candidates

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def print_status(candidates: list[StreamCandidate], current: StreamCandidate | None) -> None:
    print("\n┌─ Streams " + "─" * 50)
    for c in candidates:
        playing = "▶" if current and c.label == current.label else " "
        status  = f"✓ {c.latency_ms}ms" if c.alive else "✗ dead"
        viewers = f"  ({c.viewers}v)" if c.viewers else ""
        print(f"│ {playing} {c.label:<44} {status}{viewers}")
    print("└" + "─" * 60 + "\n")


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
        except (ValueError, KeyboardInterrupt):
            sys.exit(0)
        print("  Invalid.")


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

    log.info("Loading stream sources…")
    candidates = load_candidates(match)
    if not candidates:
        sys.exit("No stream sources found.")

    log.info(f"Found {len(candidates)} source(s). Resolving…")
    mpv     = MpvController()
    current: StreamCandidate | None = None

    for c in candidates:
        probe(c)
        if c.alive:
            current = c
            mpv.launch(c.resolved, title=match.title, referrer=c.embed_url)
            mpv.osd(f"▶ {c.label}  ({c.latency_ms}ms)")
            log.info(f"Playing: {c.label}")
            break

    if not current:
        sys.exit("No working streams. Try a different match.")

    print_status(candidates, current)

    def monitor() -> None:
        nonlocal current
        while True:
            time.sleep(CHECK_EVERY)

            if not mpv.is_alive():
                log.warning("mpv closed — re-launching")
                if current and current.resolved:
                    mpv.launch(current.resolved, title=match.title, referrer=current.embed_url)
                continue

            log.info("Health check…")
            # Don't probe current: CDN rejects a second connection while proxy is active.
            # Trust mpv.is_alive() for the playing stream's health.
            probe_all([c for c in candidates if c is not current], resolve_if_dead=False)
            if current:
                current.alive = True
            alive = [c for c in candidates if c.alive]
            print_status(candidates, current)

            if not current:
                continue

            best = max(alive, key=lambda c: c.score()) if alive else None
            current_dead = not current.alive
            much_better  = (best is not None and best is not current
                            and best.latency_ms < current.latency_ms * SWITCH_MARGIN)

            if current_dead:
                log.warning("Current stream died — finding replacement…")
                unresolved = [c for c in candidates if c is not current and not c.resolved]
                if unresolved:
                    probe_all(unresolved, resolve_if_dead=True)
                alive = [c for c in candidates if c.alive]
                best  = max(alive, key=lambda c: c.score()) if alive else None

            if (current_dead or much_better) and best:
                reason = (f"{current.label} died" if current_dead
                          else f"{best.latency_ms}ms vs {current.latency_ms}ms")
                log.info(f"Switch: {current.label} → {best.label}  ({reason})")
                mpv.launch(best.resolved, title=match.title, referrer=best.embed_url)
                mpv.osd(f"↷ {best.label}  ({best.latency_ms}ms)")
                current = best
            elif current_dead:
                log.warning("All streams dead — waiting…")

    threading.Thread(target=monitor, daemon=True).start()
    log.info(f"Monitoring {len(candidates)} stream(s) every {CHECK_EVERY}s. Ctrl+C to quit.")

    def shutdown(*_) -> None:
        log.info("Shutting down.")
        if mpv.proc:
            mpv.proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while mpv.is_alive():
        time.sleep(1)
    log.info("mpv closed — done.")
