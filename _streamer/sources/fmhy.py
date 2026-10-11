"""FMHY's Live Sports list: the community-maintained current domains of each site.

Sites rotate domains constantly. Each site module keeps a few known mirrors
and adds whatever FMHY lists now, so a domain move doesn't break discovery.
The list is fetched once per run and cached on disk for 12 h; if GitHub is
unreachable a stale cache (or nothing) is used.
"""
import json
import logging
import os
import re
import threading
import time

from _streamer.net import http_get

log = logging.getLogger(__name__)

_URL = "https://raw.githubusercontent.com/fmhy/edit/main/docs/video.md"
_CACHE = os.path.expanduser("~/.stream-ripper/fmhy.json")
_TTL = 12 * 3600

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)[^)]*\)")
# Link labels that are metadata, not site names. Numbered links ([2], [3])
# are mirrors of the preceding site.
_NOISE = re.compile(
    r"^(mirrors?|discord|telegram|tg|status.*|github|subreddit|.*note|bypass blocks|vpns?|invite|clones|guide)$",
    re.I,
)
_INVISIBLE = "\u2060\u200b\ufeff"

_lock = threading.Lock()
_sites: dict[str, list[str]] | None = None


def mirrors(name: str, known: list[str]) -> list[str]:
    """known mirrors plus the domains FMHY currently lists under name, deduped."""
    global _sites
    with _lock:
        if _sites is None:
            _sites = _load()
    return list(dict.fromkeys(known + [u.rstrip("/") for u in _sites.get(name, [])]))


def _load() -> dict[str, list[str]]:
    try:
        with open(_CACHE) as f:
            cached = json.load(f)
    except Exception:
        cached = None
    if cached and time.time() - cached.get("ts", 0) < _TTL:
        return cached["sites"]
    try:
        parsed = _parse(http_get(_URL, timeout=5).text)
        if parsed:
            os.makedirs(os.path.dirname(_CACHE), exist_ok=True)
            with open(_CACHE, "w") as f:
                json.dump({"ts": time.time(), "sites": parsed}, f)
            log.info(f"FMHY list refreshed: {len(parsed)} live-sports sites")
            return parsed
    except Exception as exc:
        log.warning(f"FMHY list fetch failed: {exc}")
    return cached["sites"] if cached else {}


def _parse(markdown: str) -> dict[str, list[str]]:
    """{site_name: [primary_url, mirrors...]} from the Live Sports section."""
    try:
        section = markdown.split("## ▷ Live Sports", 1)[1].split("\n## ", 1)[0]
    except IndexError:
        return {}
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in section.splitlines():
        if not line.startswith("*") or "**Note**" in line:
            continue
        for name, url in _LINK.findall(line):
            name = name.strip().strip(_INVISIBLE).strip()
            if not name:
                continue
            if name.isdigit():
                if current:
                    out[current].append(url)
            elif _NOISE.match(name):
                continue
            else:
                current = name.lower()
                out.setdefault(current, []).append(url)
    return out
