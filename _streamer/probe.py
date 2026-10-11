import logging

from _streamer.models import StreamCandidate
from _streamer.net import check_stream
from _streamer.resolve import resolve
from _streamer.settings import FAIL_LIMIT, MAX_RESOLVES

log = logging.getLogger(__name__)


def mark_failed(c: StreamCandidate) -> None:
    """Record one failure; after FAIL_LIMIT in a row, drop the URL so it gets re-resolved."""
    with c._lock:
        c.alive = False
        c.failures += 1
        c.checks += 1
        if c.failures >= FAIL_LIMIT:
            c.resolved, c.referer = "", ""


def probe(c: StreamCandidate, no_browser: bool = False) -> bool:
    """Resolve (if needed) and health-check one candidate. Updates c in-place.

    no_browser=True skips playwright/Chrome — use while mpv is playing so no
    browser window pops up over the stream.
    """
    c.probing = True
    try:
        return _probe(c, no_browser)
    finally:
        c.probing = False


def _probe(c: StreamCandidate, no_browser: bool) -> bool:
    if c.resolved:
        try:
            ms, height = check_stream(c.resolved, c.play_referer)
        except Exception as exc:
            mark_failed(c)
            log.warning(f"  ✗ [{c.label}]  {exc}")
            return False
    else:
        with c._lock:
            if c.resolve_attempts >= MAX_RESOLVES:
                return False
            c.resolve_attempts += 1
        hit = resolve(c, no_browser=no_browser)
        if not hit:
            with c._lock:
                if no_browser:
                    # Skipped because browser was required — don't count as a real attempt
                    c.resolve_attempts -= 1
                else:
                    c.alive = False
                    c.failures += 1
                    c.checks += 1
            return False
        url, referer, ms, height = hit
        with c._lock:
            c.resolved, c.referer = url, referer

    with c._lock:
        c.alive, c.latency_ms, c.failures, c.resolve_attempts = True, ms, 0, 0
        c.height = height or c.height
        c.checks += 1
        c.passes += 1
    log.info(f"  ✓ [{c.label}]  {ms}ms")
    return True
