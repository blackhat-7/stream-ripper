import logging
import threading
import time

import m3u8
import requests

from _streamer.models import StreamCandidate
from _streamer.net import origin
from _streamer.resolve import resolve
from _streamer.settings import FAIL_LIMIT, HEADERS, MAX_RESOLVES, REQ_TIMEOUT

log = logging.getLogger(__name__)


def probe(c: StreamCandidate, resolve_if_dead: bool = True) -> bool:
    """Health-check one candidate. Updates c in-place.

    resolve_if_dead=False skips Playwright/yt-dlp re-resolution — use during
    background checks so Chrome never opens while mpv is playing.
    """
    if not c.resolved:
        if not resolve_if_dead:
            return False
        with c._lock:
            if c.resolve_attempts >= MAX_RESOLVES:
                return False
            c.resolve_attempts += 1
        url = resolve(c)
        if not url:
            with c._lock:
                c.alive = False
                c.failures += 1
            return False
        with c._lock:
            c.resolved = url
    else:
        url = c.resolved

    h = {**HEADERS, "Referer": c.embed_url, "Origin": origin(c.embed_url)}
    try:
        t0 = time.monotonic()
        resp = requests.get(url, headers=h, timeout=REQ_TIMEOUT)
        manifest_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code != 200:
            raise ValueError(f"manifest HTTP {resp.status_code}")

        pl = m3u8.loads(resp.text, uri=url)
        if pl.is_variant and pl.playlists:
            best = pl.playlists[-1].absolute_uri
            r2 = requests.get(best, headers=h, timeout=REQ_TIMEOUT)
            if r2.status_code != 200:
                raise ValueError(f"variant HTTP {r2.status_code}")
            pl = m3u8.loads(r2.text, uri=best)
        if not pl.segments:
            raise ValueError("no segments")

        t1 = time.monotonic()
        sr = requests.get(pl.segments[-1].absolute_uri, headers=h, timeout=REQ_TIMEOUT, stream=True)
        next(sr.iter_content(8192), None)
        seg_ms = int((time.monotonic() - t1) * 1000)
        if sr.status_code != 200:
            raise ValueError(f"segment HTTP {sr.status_code}")

        with c._lock:
            c.alive, c.latency_ms, c.failures, c.resolve_attempts = True, manifest_ms + seg_ms, 0, 0
        log.info(f"  ✓ [{c.label}]  {manifest_ms + seg_ms}ms")
        return True

    except Exception as exc:
        with c._lock:
            c.alive = False
            c.failures += 1
            if c.failures >= FAIL_LIMIT:
                c.resolved = ""
        log.warning(f"  ✗ [{c.label}]  {exc}")
        return False


def probe_all(candidates: list[StreamCandidate], resolve_if_dead: bool = True) -> None:
    threads = [threading.Thread(target=probe, args=(c, resolve_if_dead), daemon=True)
               for c in candidates]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
