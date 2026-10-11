import threading
from dataclasses import dataclass, field


@dataclass
class StreamCandidate:
    label: str
    embed_url: str
    site: str = ""
    resolved: str = ""
    referer: str = ""          # referer the resolved URL validated with; defaults to embed_url
    alive: bool = False
    probing: bool = False
    latency_ms: int = 9999
    failures: int = 0
    resolve_attempts: int = 0
    viewers: int = 0
    language: str = ""         # e.g. "Spanish", when the source says
    country: str = ""          # ISO 3166 alpha-2, when the source says
    height: int = 0            # vertical resolution of the best variant, once probed
    checks: int = 0            # health checks run (probes, plus stalls/deaths while playing)
    passes: int = 0            # ...and how many of them passed
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def play_referer(self) -> str:
        return self.referer or self.embed_url

    @property
    def hd(self) -> bool:
        return self.height >= 720 if self.height else "· HD" in self.label

    @property
    def stability(self) -> float:
        """Share of health checks passed, 0–1; lightly smoothed (one pass = 0.75, unknown = 0.5)."""
        return (self.passes + 0.5) / (self.checks + 1)

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
        hd_bonus = 2.0 if self.hd else 1.0
        return hd_bonus * self.stability * (1_000_000.0 / (self.latency_ms + 1)) + self.viewers * 0.001


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
