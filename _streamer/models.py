import threading
from dataclasses import dataclass, field


@dataclass
class StreamCandidate:
    label: str
    embed_url: str
    site: str = ""
    resolved: str = ""
    alive: bool = False
    probing: bool = False
    latency_ms: int = 9999
    failures: int = 0
    resolve_attempts: int = 0
    viewers: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def status(self) -> str:
        """One of: pending, probing, alive, dead."""
        if self.probing:
            return "probing"
        if self.alive:
            return "alive"
        if self.resolve_attempts or self.failures:
            return "dead"
        return "pending"

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
    league: str = ""
    alts: list["LiveMatch"] = field(default_factory=list)  # same match listed on other sites

    @property
    def sites(self) -> list[str]:
        return [self.source_site] + [a.source_site for a in self.alts]
