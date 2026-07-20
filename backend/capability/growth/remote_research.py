from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

SCHEMA_VERSION = "spiritkin.growth_remote_research.v1"
GITHUB_SEARCH_ENDPOINT = "https://api.github.com/search/repositories"
GITHUB_API_VERSION = "2026-03-10"
MAX_RESULTS = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_QUERY_LENGTH = 220
DEFAULT_TIMEOUT_SECONDS = 8.0
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret)\b\s*[:=]"
)


def _safe_id(value: str, fallback: str = "research") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip().lower()).strip("-._")
    return normalized[:96] or fallback


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", errors="ignore")).hexdigest()


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _as_terms(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value]
    else:
        values = []
    terms: list[str] = []
    for raw in values:
        normalized = re.sub(r"[^\w+#-]+", " ", raw.replace("_", " ").replace(".", " "), flags=re.UNICODE)
        for term in normalized.split():
            term = term[:48]
            if len(term) >= 2 and term.lower() not in {item.lower() for item in terms}:
                terms.append(term)
            if len(terms) >= 8:
                return terms
    return terms


def _reject_sensitive_search_input(value: Any) -> None:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    if any(SENSITIVE_ASSIGNMENT_PATTERN.search(str(item or "")) for item in values):
        raise ValueError("GitHub research keywords must not contain credentials or secrets")


def _build_query(candidate: dict[str, Any], payload: dict[str, Any]) -> str:
    _reject_sensitive_search_input(candidate.get("requirements"))
    _reject_sensitive_search_input(payload.get("keywords"))
    terms = _as_terms(candidate.get("requirements"))
    terms.extend(term for term in _as_terms(payload.get("keywords")) if term.lower() not in {item.lower() for item in terms})
    if not terms:
        terms = _as_terms(candidate.get("title") or candidate.get("request"))
    if not terms:
        raise ValueError("candidate requirements or research keywords are required")
    keywords = " ".join(terms[:8])[:160].strip()
    query = f"{keywords} in:name,description,readme fork:false archived:false"
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError("GitHub research query exceeds the managed length limit")
    return query


def _rate_limit(headers: Any) -> dict[str, Any]:
    def read(name: str) -> str:
        try:
            return str(headers.get(name) or "").strip()
        except AttributeError:
            return ""

    return {
        "resource": read("x-ratelimit-resource"),
        "limit": int(read("x-ratelimit-limit") or 0),
        "remaining": int(read("x-ratelimit-remaining") or 0),
        "reset_at": int(read("x-ratelimit-reset") or 0),
        "retry_after_seconds": int(read("retry-after") or 0),
    }


def _repository(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    full_name = _bounded_text(item.get("full_name"), 180)
    html_url = _bounded_text(item.get("html_url"), 500)
    parsed = urlparse(html_url)
    if not full_name or parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        return None
    license_value = item.get("license") if isinstance(item.get("license"), dict) else {}
    spdx = _bounded_text(license_value.get("spdx_id"), 80) or "NOASSERTION"
    archived = bool(item.get("archived"))
    disabled = bool(item.get("disabled"))
    fork = bool(item.get("fork"))
    return {
        "source_type": "github_repository_metadata",
        "full_name": full_name,
        "url": html_url,
        "description": _bounded_text(item.get("description"), 500),
        "license_spdx": spdx,
        "stars": max(0, int(item.get("stargazers_count") or 0)),
        "forks": max(0, int(item.get("forks_count") or 0)),
        "language": _bounded_text(item.get("language"), 80),
        "topics": [_bounded_text(topic, 80) for topic in (item.get("topics") or [])[:20] if _bounded_text(topic, 80)],
        "default_branch": _bounded_text(item.get("default_branch"), 160),
        "updated_at": _bounded_text(item.get("updated_at"), 80),
        "archived": archived,
        "disabled": disabled,
        "fork": fork,
        "needs_license_review": spdx in {"NOASSERTION", "OTHER"},
        "eligible_for_sandbox_review": not archived and not disabled and not fork,
    }


class GitHubRepositoryResearcher:
    """Fetch bounded public repository metadata; never clone, download, install, or execute."""

    def __init__(self, report_root: str | Path, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.root = Path(report_root).resolve()
        self.timeout = max(2.0, min(15.0, float(timeout)))

    def research(self, candidate: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id:
            raise ValueError("candidate_id is required")
        query = _build_query(candidate, payload)
        result_limit = max(1, min(MAX_RESULTS, int(payload.get("result_limit") or MAX_RESULTS)))
        url = f"{GITHUB_SEARCH_ENDPOINT}?{urlencode({'q': query, 'per_page': result_limit, 'page': 1})}"
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": "SpiritKin-Growth-Runtime/1.0",
            },
            method="GET",
        )
        started_at = time.time()
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                headers = response.headers
                status_code = int(getattr(response, "status", 200) or 200)
        except HTTPError as exc:
            limit = _rate_limit(exc.headers)
            if exc.code in {403, 429}:
                raise RuntimeError(
                    f"GitHub repository research is rate limited; remaining={limit['remaining']} "
                    f"reset_at={limit['reset_at']} retry_after={limit['retry_after_seconds']}"
                ) from exc
            raise RuntimeError(f"GitHub repository research failed with HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"GitHub repository research failed: {type(exc).__name__}: {exc}") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RuntimeError("GitHub repository research response exceeded the managed size limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("GitHub repository research returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("GitHub repository research returned an invalid payload")
        repositories = [repository for item in (decoded.get("items") or [])[:result_limit] if (repository := _repository(item))]
        created_at = time.time()
        report_id = f"research-{_digest({'candidate_id': candidate_id, 'query': query, 'created_at': created_at})[:16]}"
        report = {
            "schema_version": SCHEMA_VERSION,
            "report_id": report_id,
            "candidate_id": candidate_id,
            "kind": str(candidate.get("kind") or "capability"),
            "workspace_id": str(candidate.get("workspace_id") or ""),
            "status": "completed",
            "provider": "github_repository_search",
            "endpoint": GITHUB_SEARCH_ENDPOINT,
            "query": query,
            "result_count": len(repositories),
            "total_count": max(0, int(decoded.get("total_count") or 0)),
            "incomplete_results": bool(decoded.get("incomplete_results")),
            "repositories": repositories,
            "rate_limit": _rate_limit(headers),
            "http_status": status_code,
            "duration_ms": round((created_at - started_at) * 1000, 2),
            "created_at": created_at,
            "policy": {
                "network_accessed": True,
                "network_scope": "fixed_public_metadata_endpoint",
                "authenticated": False,
                "downloaded": False,
                "cloned": False,
                "installed": False,
                "external_code_executed": False,
                "candidate_stage_advanced": False,
                "activation_enabled": False,
                "requires_human_review": True,
            },
        }
        candidate_dir = (self.root / _safe_id(candidate_id, "candidate")).resolve()
        if not candidate_dir.is_relative_to(self.root):
            raise ValueError("unsafe Growth research report path")
        report_path = candidate_dir / f"{report_id}.json"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(report_path)
        return {**report, "path": str(report_path)}

    def snapshot(self, candidate_ids: list[str] | None = None) -> dict[str, Any]:
        reports: list[dict[str, Any]] = []
        for candidate_id in candidate_ids or []:
            candidate_dir = (self.root / _safe_id(candidate_id, "candidate")).resolve()
            if not candidate_dir.is_relative_to(self.root) or not candidate_dir.exists():
                continue
            for path in candidate_dir.glob("research-*.json"):
                try:
                    report = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(report, dict):
                    reports.append(report)
        reports.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return {
            "schema_version": SCHEMA_VERSION,
            "count": len(reports),
            "recent": [
                {
                    "report_id": item.get("report_id"),
                    "candidate_id": item.get("candidate_id"),
                    "workspace_id": item.get("workspace_id"),
                    "provider": item.get("provider"),
                    "query": item.get("query"),
                    "result_count": item.get("result_count", 0),
                    "incomplete_results": bool(item.get("incomplete_results")),
                    "created_at": item.get("created_at"),
                }
                for item in reports[:20]
            ],
            "policy": {
                "fixed_endpoint": GITHUB_SEARCH_ENDPOINT,
                "metadata_only": True,
                "automatic_download": False,
                "automatic_install": False,
                "automatic_activation": False,
            },
        }
