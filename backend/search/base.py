from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SearchOptions:
    count: int = 5
    country: str = ""
    language: str = ""
    freshness: str = ""
    safe_search: str = "moderate"


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""
    published_at: str = ""
    provider: str = ""
    score: float = 0.0
    metadata: dict[str, object] = field(default_factory=dict)

    def snapshot(self) -> dict[str, object]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "published_at": self.published_at,
            "provider": self.provider,
            "score": self.score,
            "metadata": dict(self.metadata),
        }


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, options: SearchOptions | None = None) -> list[SearchResult]:
        ...
