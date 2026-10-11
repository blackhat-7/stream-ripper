"""Single HTTP client for the whole app (browser-like TLS via curl_cffi).

Sources, resolvers, probing and the proxy all go through here, so a stream
that scrapes fine can't 403 on the health check or in mpv.
"""
import re
import threading
import time
import urllib.parse as urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import m3u8
from curl_cffi import requests as cf

from _streamer.settings import HEADERS, REQ_TIMEOUT

# Media segments / static assets — never the stream playlist itself.
SEGMENT_RE = re.compile(
    r"\d+\.ts|seg-\d|\.aac|\.mp4|\.(js|css|png|jpe?g|gif|svg|ico|woff2?)(\?|$)", re.I)

# curl_cffi sessions are not thread-safe — one per thread (keeps connection reuse).
_local = threading.local()


def _session() -> cf.Session:
    if not hasattr(_local, "session"):
        _local.session = cf.Session(impersonate="chrome")
    return _local.session


def http_get(url: str, timeout: float = REQ_TIMEOUT, retries: int = 2, **kwargs):
    """GET with a browser-like TLS fingerprint (bypasses Cloudflare).

    Retries once by default: some ISPs randomly reset connections to these
    hosts, and a reset fails fast, so a quick retry is cheap.
    """
    last: Exception = RuntimeError("no attempts")
    for i in range(retries):
        try:
            return _session().get(url, timeout=timeout, **kwargs)
        except Exception as exc:
            last = exc
            if i < retries - 1:
                time.sleep(0.3)
    raise last


def get_first(urls: list[str], timeout: float = 6, json: bool = False, **kwargs):
    """Fetch mirror URLs in parallel; return the first HTTP 200 response.

    Sites rotate domains and some mirrors hang, so racing them keeps one dead
    mirror from costing a full timeout. json=True also rejects non-JSON bodies
    (seized/parked mirrors answer 200 with HTML). Raises if no mirror is usable.
    """
    ex = ThreadPoolExecutor(len(urls))
    futures = {ex.submit(http_get, u, timeout, **kwargs): u for u in urls}
    errors = []
    try:
        for f in as_completed(futures):
            try:
                resp = f.result()
            except Exception as exc:
                errors.append(f"{origin(futures[f])}: {str(exc)[:60]}")
                continue
            if resp.status_code == 200 and (not json or resp.text.lstrip()[:1] in ("[", "{")):
                return resp
            errors.append(f"{origin(futures[f])}: HTTP {resp.status_code}")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("; ".join(errors))


def headers_for(referer: str) -> dict:
    """Request headers carrying the embed page's Referer/Origin."""
    return {**HEADERS, "Referer": referer, "Origin": origin(referer)}


def check_stream(url: str, referer: str) -> int:
    """Verify an HLS URL end-to-end (manifest → best variant → last segment).

    Returns latency in ms; raises ValueError (or a transport error) saying what failed.
    """
    h = headers_for(referer)
    t0 = time.monotonic()
    r = http_get(url, headers=h)
    if r.status_code != 200:
        raise ValueError(f"manifest HTTP {r.status_code}")
    pl = m3u8.loads(r.text, uri=url)
    if pl.is_variant and pl.playlists:
        best = max(pl.playlists, key=lambda p: p.stream_info.bandwidth or 0).absolute_uri
        r = http_get(best, headers=h)
        if r.status_code != 200:
            raise ValueError(f"variant HTTP {r.status_code}")
        pl = m3u8.loads(r.text, uri=best)
    if not pl.segments:
        raise ValueError("no segments")
    sr = http_get(pl.segments[-1].absolute_uri, headers=h, stream=True)
    try:
        if sr.status_code != 200:
            raise ValueError(f"segment HTTP {sr.status_code}")
        next(sr.iter_content(8192), None)
    finally:
        sr.close()
    return int((time.monotonic() - t0) * 1000)


def origin(url: str) -> str:
    try:
        p = urlparse.urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""
