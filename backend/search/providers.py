from __future__ import annotations

import json
import os
from typing import Any
from urllib import parse, request

from backend.app.settings import resolve_web_search_provider
from backend.search.base import SearchOptions, SearchProvider, SearchResult


class BraveSearchProvider:
    name = "brave"

    def __init__(self, *, api_key: str | None = None, endpoint: str = "https://api.search.brave.com/res/v1/web/search", timeout: float = 8.0):
        self.api_key = (api_key if api_key is not None else os.getenv("BRAVE_SEARCH_API_KEY", "")).strip()
        self.endpoint = endpoint
        self.timeout = max(1.0, float(timeout))

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, *, options: SearchOptions | None = None) -> list[SearchResult]:
        if not self.available:
            raise RuntimeError("BRAVE_SEARCH_API_KEY is not configured")
        opts = options or SearchOptions()
        params = {
            "q": query,
            "count": str(max(1, min(int(opts.count or 5), 20))),
        }
        if opts.country:
            params["country"] = opts.country
        if opts.language:
            params["search_lang"] = opts.language
        if opts.freshness:
            params["freshness"] = opts.freshness
        if opts.safe_search:
            params["safesearch"] = opts.safe_search
        url = f"{self.endpoint}?{parse.urlencode(params)}"
        req = request.Request(
            url,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
                "User-Agent": "SpiritKinAI/1.0",
            },
        )
        with request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        web_results = (payload.get("web") or {}).get("results") or []
        return [
            _brave_result(item, index=index)
            for index, item in enumerate(web_results, start=1)
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        ]


class DuckDuckGoInstantAnswerProvider:
    name = "duckduckgo"

    def __init__(self, *, endpoint: str = "https://api.duckduckgo.com/", timeout: float = 6.0):
        self.endpoint = endpoint
        self.timeout = max(1.0, float(timeout))

    def search(self, query: str, *, options: SearchOptions | None = None) -> list[SearchResult]:
        params = {
            "q": query,
            "format": "json",
            "no_redirect": "1",
            "no_html": "1",
            "skip_disambig": "1",
        }
        url = f"{self.endpoint}?{parse.urlencode(params)}"
        req = request.Request(url, headers={"Accept": "application/json", "User-Agent": "SpiritKinAI/1.0"})
        with request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        results: list[SearchResult] = []
        heading = str(payload.get("Heading") or "").strip()
        abstract_url = str(payload.get("AbstractURL") or payload.get("AbstractSource") or "").strip()
        abstract = str(payload.get("AbstractText") or "").strip()
        if heading and abstract_url:
            results.append(
                SearchResult(
                    title=heading,
                    url=abstract_url,
                    snippet=abstract,
                    source=str(payload.get("AbstractSource") or ""),
                    provider=self.name,
                    score=1.0,
                )
            )
        for item in _flatten_duck_related(payload.get("RelatedTopics") or []):
            if len(results) >= int((options or SearchOptions()).count or 5):
                break
            first_url = str(item.get("FirstURL") or "").strip()
            text = str(item.get("Text") or "").strip()
            if not first_url or not text:
                continue
            title = text.split(" - ", 1)[0][:120] or first_url
            results.append(SearchResult(title=title, url=first_url, snippet=text, provider=self.name, score=0.5))
        return results[: max(1, int((options or SearchOptions()).count or 5))]


class FallbackSearchProvider:
    name = "fallback"

    def __init__(self, providers: list[SearchProvider]):
        self.providers = list(providers)

    def search(self, query: str, *, options: SearchOptions | None = None) -> list[SearchResult]:
        errors: list[str] = []
        for provider in self.providers:
            try:
                results = provider.search(query, options=options)
            except Exception as exc:
                errors.append(f"{getattr(provider, 'name', provider.__class__.__name__)}: {exc}")
                continue
            if results:
                return results
        raise RuntimeError("; ".join(errors) or "no search providers configured")


def build_default_search_provider() -> SearchProvider:
    preferred = resolve_web_search_provider()
    providers: list[SearchProvider] = []
    for name in [part.strip().lower() for part in preferred.split(",") if part.strip()]:
        if name == "brave":
            providers.append(BraveSearchProvider())
        elif name in {"duckduckgo", "ddg"}:
            providers.append(DuckDuckGoInstantAnswerProvider())
    if not providers:
        providers.append(DuckDuckGoInstantAnswerProvider())
    return providers[0] if len(providers) == 1 else FallbackSearchProvider(providers)


def _brave_result(item: dict[str, Any], *, index: int) -> SearchResult:
    return SearchResult(
        title=str(item.get("title") or "").strip(),
        url=str(item.get("url") or "").strip(),
        snippet=str(item.get("description") or item.get("snippet") or "").strip(),
        source=str(item.get("profile", {}).get("name") or item.get("family_friendly") or ""),
        published_at=str(item.get("age") or item.get("page_age") or ""),
        provider=BraveSearchProvider.name,
        score=1.0 / max(1, index),
        metadata={"subtype": item.get("subtype"), "language": item.get("language"), "family_friendly": item.get("family_friendly")},
    )


def _flatten_duck_related(items: list[Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("Topics"), list):
            flattened.extend(_flatten_duck_related(item["Topics"]))
        else:
            flattened.append(item)
    return flattened
