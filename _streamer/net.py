import time
import urllib.parse as urlparse

from curl_cffi import requests as cf

from _streamer.settings import REQ_TIMEOUT

_session = cf.Session(impersonate="chrome")


def http_get(url: str, timeout: int = REQ_TIMEOUT, retries: int = 3, **kwargs):
    """GET with retries using browser-like TLS fingerprint (bypasses Cloudflare)."""
    last: Exception = RuntimeError("no attempts")
    for i in range(retries):
        try:
            return _session.get(url, timeout=timeout, **kwargs)
        except Exception as exc:
            last = exc
            if i < retries - 1:
                time.sleep(1.5 ** i)
    raise last


def origin(url: str) -> str:
    try:
        p = urlparse.urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""
