import threading
from dataclasses import dataclass, field


@dataclass
class StreamCandidate:
    label: str
    embed_url: str
    resolved: str = ""
    alive: bool = False
    latency_ms: int = 9999
    failures: int = 0
    resolve_attempts: int = 0
    viewers: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def score(self) -> float:
        if not self.alive:
            return -1.0
        hd_bonus = 2.0 if "· HD" in self.label else 1.0
        return hd_bonus * (1_000_000.0 / (self.latency_ms + 1)) + self.viewers * 0.001


@dataclass
class LiveMatch:
    id: str
    title: str
    source_site: str
    sport: str
    raw: dict
