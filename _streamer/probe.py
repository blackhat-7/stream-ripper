import logging
import time

import m3u8

from _streamer.models import StreamCandidate
from _streamer.net import origin, retrying_session
from _streamer.resolve import resolve
from _streamer.settings import FAIL_LIMIT, HEADERS, MAX_RESOLVES, REQ_TIMEOUT

log = logging.getLogger(__name__)


def mark_failed(c: StreamCandidate) -> None:
    """Record one failure; after FAIL_LIMIT in a row, drop the URL so it gets re-resolved."""
    with c._lock:
        c.alive = False
        c.failures += 1
        if c.failures >= FAIL_LIMIT:
            c.resolved = ""


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
    if not c.resolved:
        with c._lock:
            if c.resolve_attempts >= MAX_RESOLVES:
                return False
            c.resolve_attempts += 1
        url = resolve(c, no_browser=no_browser)
        if not url:
            with c._lock:
                if no_browser:
                    # Skipped because browser was required — don't count as a real attempt
                    c.resolve_attempts -= 1
                else:
                    c.alive = False
                    c.failures += 1
            return False
        with c._lock:
            c.resolved = url
    else:
        url = c.resolved

    h = {**HEADERS, "Referer": c.embed_url, "Origin": origin(c.embed_url)}
    http = retrying_session()
    try:
        t0 = time.monotonic()
        resp = http.get(url, headers=h, timeout=REQ_TIMEOUT)
        manifest_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            raise ValueError(f"manifest HTTP {resp.status_code}")

        pl = m3u8.loads(resp.text, uri=url)
        if pl.is_variant and pl.playlists:
            best = pl.playlists[-1].absolute_uri
            r2 = http.get(best, headers=h, timeout=REQ_TIMEOUT)
            if r2.status_code != 200:
                raise ValueError(f"variant HTTP {r2.status_code}")
            pl = m3u8.loads(r2.text, uri=best)
        if not pl.segments:
            raise ValueError("no segments")

        t1 = time.monotonic()
        with http.get(pl.segments[-1].absolute_uri, headers=h,
                          timeout=REQ_TIMEOUT, stream=True) as sr:
            if sr.status_code != 200:
                raise ValueError(f"segment HTTP {sr.status_code}")
            next(sr.iter_content(8192), None)
        seg_ms = int((time.monotonic() - t1) * 1000)

        with c._lock:
            c.alive, c.latency_ms, c.failures, c.resolve_attempts = True, manifest_ms + seg_ms, 0, 0
        log.info(f"  ✓ [{c.label}]  {manifest_ms + seg_ms}ms")
        return True

    except Exception as exc:
        mark_failed(c)
        log.warning(f"  ✗ [{c.label}]  {exc}")
        return False
