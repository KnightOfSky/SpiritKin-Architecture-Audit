from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from backend.capability.growth.remote_research import GITHUB_SEARCH_ENDPOINT, GitHubRepositoryResearcher


class FakeResponse:
    def __init__(self, payload: dict, headers: dict[str, str] | None = None) -> None:
        self.payload = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit: int = -1) -> bytes:
        return self.payload if limit < 0 else self.payload[:limit]


def test_github_research_is_bounded_metadata_only_and_writes_managed_report(tmp_path, monkeypatch):
    captured = {}
    items = [
        {
            "full_name": f"example/tool-{index}",
            "html_url": f"https://github.com/example/tool-{index}",
            "description": "Beat synchronization helper",
            "license": {"spdx_id": "MIT" if index else "NOASSERTION"},
            "stargazers_count": 100 - index,
            "forks_count": index,
            "language": "Python",
            "topics": ["video", "audio"],
            "default_branch": "main",
            "updated_at": "2026-07-01T00:00:00Z",
            "archived": False,
            "disabled": False,
            "fork": False,
        }
        for index in range(8)
    ]

    def fake_open(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return FakeResponse(
            {"total_count": 8, "incomplete_results": False, "items": items},
            {
                "x-ratelimit-resource": "search",
                "x-ratelimit-limit": "10",
                "x-ratelimit-remaining": "9",
                "x-ratelimit-reset": "1780000000",
            },
        )

    monkeypatch.setattr("backend.capability.growth.remote_research.urlopen", fake_open)
    researcher = GitHubRepositoryResearcher(tmp_path / "artifacts", timeout=8)
    report = researcher.research(
        {
            "candidate_id": "growth-tool-beat-sync",
            "kind": "tool",
            "workspace_id": "tenant-a",
            "requirements": ["video.beat_sync"],
        },
        {"endpoint": "https://evil.example/search", "result_limit": 5},
    )

    assert captured["url"].startswith(f"{GITHUB_SEARCH_ENDPOINT}?")
    assert "evil.example" not in captured["url"]
    assert "per_page=5" in captured["url"]
    assert "sort=" not in captured["url"]
    assert captured["headers"]["X-github-api-version"] == "2026-03-10"
    assert not any(key.lower() == "authorization" for key in captured["headers"])
    assert captured["timeout"] == 8
    assert report["result_count"] == 5
    assert report["repositories"][0]["needs_license_review"] is True
    assert report["repositories"][1]["eligible_for_sandbox_review"] is True
    assert report["rate_limit"]["remaining"] == 9
    assert report["policy"]["network_accessed"] is True
    for key in ("downloaded", "cloned", "installed", "external_code_executed", "activation_enabled"):
        assert report["policy"][key] is False
    assert report["path"].startswith(str((tmp_path / "artifacts").resolve()))
    assert json.loads(open(report["path"], encoding="utf-8").read())["report_id"] == report["report_id"]
    snapshot = researcher.snapshot(["growth-tool-beat-sync"])
    assert snapshot["count"] == 1
    assert "path" not in json.dumps(snapshot)


def test_github_research_surfaces_rate_limit_without_retry(tmp_path, monkeypatch):
    calls = []

    def rate_limited(request, timeout):
        calls.append((request, timeout))
        raise HTTPError(
            request.full_url,
            429,
            "rate limited",
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1780000000", "retry-after": "60"},
            BytesIO(b'{"message":"rate limited"}'),
        )

    monkeypatch.setattr("backend.capability.growth.remote_research.urlopen", rate_limited)
    researcher = GitHubRepositoryResearcher(tmp_path / "artifacts")

    with pytest.raises(RuntimeError, match="rate limited.*remaining=0.*retry_after=60"):
        researcher.research(
            {"candidate_id": "growth-tool-rate", "kind": "tool", "requirements": ["ffmpeg"]},
            {},
        )
    assert len(calls) == 1
    assert not list((tmp_path / "artifacts").rglob("research-*.json"))


@pytest.mark.parametrize(
    "keywords",
    ["api_key=github-secret-value", "password: do-not-send", "access-token = private"],
)
def test_github_research_rejects_sensitive_keywords_before_network(tmp_path, monkeypatch, keywords):
    calls = []
    monkeypatch.setattr("backend.capability.growth.remote_research.urlopen", lambda *args, **kwargs: calls.append((args, kwargs)))
    researcher = GitHubRepositoryResearcher(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="must not contain credentials or secrets"):
        researcher.research(
            {"candidate_id": "growth-tool-sensitive", "kind": "tool", "requirements": ["ffmpeg"]},
            {"keywords": keywords},
        )
    assert calls == []
    assert not list((tmp_path / "artifacts").rglob("research-*.json"))
