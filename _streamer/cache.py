"""Disk cache: embed_url → {url, referer, ts}. Skips re-resolution on repeat views.

Entries are re-validated before use (see resolve.py), so stale CDN tokens
just fall through to a fresh resolve.
"""
import json
import logging
import os
import threading
import time

from _streamer.settings import CACHE_PATH, CACHE_TTL

log = logging.getLogger(__name__)

_lock = threading.Lock()
_data: dict | None = None


def _load() -> dict:
    global _data
    if _data is None:
        try:
            with open(CACHE_PATH) as f:
                _data = json.load(f)
        except Exception:
            _data = {}
    return _data


def _save() -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_data, f)
        os.replace(tmp, CACHE_PATH)
    except Exception as exc:
        log.debug(f"cache save: {exc}")


def get(embed_url: str) -> tuple[str, str] | None:
    """Return (resolved_url, referer) if a fresh entry exists."""
    with _lock:
        e = _load().get(embed_url)
        if e and time.time() - e.get("t", 0) < CACHE_TTL:
            return e["u"], e.get("ref", "")
    return None


def put(embed_url: str, resolved_url: str, referer: str = "") -> None:
    with _lock:
        _load()[embed_url] = {"u": resolved_url, "ref": referer, "t": time.time()}
        _save()
