from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from backend.search.base import SearchOptions, SearchResult
from backend.search.providers import (
    BraveSearchProvider,
    DuckDuckGoInstantAnswerProvider,
    FallbackSearchProvider,
)


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _Provider:
    def __init__(self, name: str, result=None, error: Exception | None = None):
        self.name = name
        self.result = result or []
        self.error = error

    def search(self, _query: str, *, options=None):
        if self.error is not None:
            raise self.error
        return list(self.result)


class SearchProviderTests(unittest.TestCase):
    def test_duckduckgo_returns_structured_results_with_count_limit(self):
        payload = {
            "Heading": "SpiritKin",
            "AbstractURL": "https://example.com/spiritkin",
            "AbstractText": "Project summary",
            "AbstractSource": "Example",
            "RelatedTopics": [
                {"FirstURL": "https://example.com/one", "Text": "One - first"},
                {"Topics": [{"FirstURL": "https://example.com/two", "Text": "Two - second"}]},
            ],
        }
        provider = DuckDuckGoInstantAnswerProvider(timeout=2.5)
        with patch("backend.search.providers.request.urlopen", return_value=_Response(payload)) as urlopen:
            results = provider.search("SpiritKin", options=SearchOptions(count=2))

        self.assertEqual(len(results), 2)
        self.assertTrue(all(isinstance(item, SearchResult) for item in results))
        self.assertTrue(all(item.provider == "duckduckgo" for item in results))
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 2.5)

    def test_search_provider_timeout_is_bounded_and_propagated(self):
        provider = DuckDuckGoInstantAnswerProvider(timeout=0.1)
        with patch("backend.search.providers.request.urlopen", side_effect=TimeoutError("timed out")) as urlopen:
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                provider.search("timeout")

        self.assertEqual(provider.timeout, 1.0)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 1.0)

    def test_brave_fails_closed_without_api_key(self):
        provider = BraveSearchProvider(api_key="")

        with self.assertRaisesRegex(RuntimeError, "BRAVE_SEARCH_API_KEY"):
            provider.search("SpiritKin")

    def test_fallback_uses_next_provider_after_timeout(self):
        expected = SearchResult(title="fallback", url="https://example.com", provider="duckduckgo")
        provider = FallbackSearchProvider(
            [
                _Provider("brave", error=TimeoutError("timed out")),
                _Provider("duckduckgo", result=[expected]),
            ]
        )

        self.assertEqual(provider.search("SpiritKin"), [expected])


if __name__ == "__main__":
    unittest.main()
