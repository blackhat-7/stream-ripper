import time
import urllib.parse as urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from curl_cffi import requests as cf
from urllib3.util.retry import Retry

from _streamer.settings import REQ_TIMEOUT


def http_get(url: str, timeout: float = REQ_TIMEOUT, retries: int = 2, **kwargs):
    """GET with a browser-like TLS fingerprint (bypasses Cloudflare).

    Retries once by default: some ISPs randomly reset connections to these
    hosts, and a reset fails fast, so a quick retry is cheap. A fresh request
    per call keeps this thread-safe (a shared curl_cffi Session is not).
    """
    last: Exception = RuntimeError("no attempts")
    for i in range(retries):
        try:
            return cf.get(url, impersonate="chrome", timeout=timeout, **kwargs)
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


def retrying_session() -> requests.Session:
    """requests Session that retries GETs on connection resets (flaky ISPs) and 5xx."""
    s = requests.Session()
    retry = Retry(total=3, connect=3, read=2, status=2, backoff_factor=0.2,
                  status_forcelist=(502, 503, 504), raise_on_status=False)
    s.mount("http://", requests.adapters.HTTPAdapter(max_retries=retry))
    s.mount("https://", requests.adapters.HTTPAdapter(max_retries=retry))
    return s


def origin(url: str) -> str:
    try:
        p = urlparse.urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""
