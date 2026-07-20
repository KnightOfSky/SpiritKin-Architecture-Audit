from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FailureSample:
    sample_id: str
    tool_name: str
    target: str
    operation: str
    error_code: str
    user_input_snippet: str
    observed_count: int = 1
    first_observed: float = field(default_factory=time.time)
    last_observed: float = field(default_factory=time.time)
    resolution_status: str = "open"
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "tool_name": self.tool_name,
            "target": self.target,
            "operation": self.operation,
            "error_code": self.error_code,
            "user_input_snippet": self.user_input_snippet,
            "observed_count": self.observed_count,
            "first_observed": self.first_observed,
            "last_observed": self.last_observed,
            "resolution_status": self.resolution_status,
            "metadata": self.metadata,
        }

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> FailureSample:
        return cls(
            sample_id=str(data.get("sample_id") or ""),
            tool_name=str(data.get("tool_name") or ""),
            target=str(data.get("target") or ""),
            operation=str(data.get("operation") or ""),
            error_code=str(data.get("error_code") or ""),
            user_input_snippet=str(data.get("user_input_snippet") or ""),
            observed_count=int(data.get("observed_count") or 1),
            first_observed=float(data.get("first_observed") or time.time()),
            last_observed=float(data.get("last_observed") or time.time()),
            resolution_status=str(data.get("resolution_status") or "open"),
            metadata=dict(data.get("metadata") or {}),
        )


class FailureSampleDB:
    def __init__(self, limit: int = 200):
        self._samples: dict[str, FailureSample] = {}
        self._limit = max(10, limit)
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"fail-{self._counter:06d}"

    def record(self, *, tool_name: str = "", target: str = "", operation: str = "", error_code: str = "", user_input_snippet: str = "", metadata: dict[str, Any] | None = None) -> FailureSample:
        dedup_key = f"{tool_name}:{target}:{operation}:{error_code}"
        existing = self._samples.get(dedup_key)
        now = time.time()
        if existing is not None:
            updated = FailureSample(
                sample_id=existing.sample_id,
                tool_name=existing.tool_name,
                target=existing.target,
                operation=existing.operation,
                error_code=existing.error_code,
                user_input_snippet=user_input_snippet or existing.user_input_snippet,
                observed_count=existing.observed_count + 1,
                first_observed=existing.first_observed,
                last_observed=now,
                resolution_status=existing.resolution_status,
                metadata=dict(existing.metadata, **(metadata or {})),
            )
            self._samples[dedup_key] = updated
            return updated
        sample = FailureSample(
            sample_id=self._next_id(),
            tool_name=tool_name,
            target=target,
            operation=operation,
            error_code=error_code,
            user_input_snippet=user_input_snippet,
            observed_count=1,
            first_observed=now,
            last_observed=now,
            metadata=dict(metadata or {}),
        )
        self._samples[dedup_key] = sample
        if len(self._samples) > self._limit:
            oldest = min(self._samples.keys(), key=lambda k: self._samples[k].first_observed)
            del self._samples[oldest]
        return sample

    def query(self, *, error_code: str | None = None, tool_name: str | None = None, target: str | None = None, resolution_status: str | None = None, limit: int = 50) -> list[FailureSample]:
        results: list[FailureSample] = []
        for sample in self._samples.values():
            if error_code is not None and sample.error_code != error_code:
                continue
            if tool_name is not None and sample.tool_name != tool_name:
                continue
            if target is not None and sample.target != target:
                continue
            if resolution_status is not None and sample.resolution_status != resolution_status:
                continue
            results.append(sample)
        results.sort(key=lambda s: s.last_observed, reverse=True)
        return results[:limit]

    def stats(self) -> dict[str, Any]:
        total = len(self._samples)
        open_count = sum(1 for s in self._samples.values() if s.resolution_status == "open")
        by_error: dict[str, int] = {}
        for s in self._samples.values():
            by_error[s.error_code] = by_error.get(s.error_code, 0) + max(1, int(s.observed_count))
        return {"total": total, "open": open_count, "by_error_code": by_error}

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        sorted_samples = sorted(self._samples.values(), key=lambda s: s.last_observed, reverse=True)
        return [s.snapshot() for s in sorted_samples[:limit]]


class JsonlFailureSampleDB(FailureSampleDB):
    def __init__(self, path: str | Path, limit: int = 200):
        super().__init__(limit=limit)
        self._path = Path(path).resolve()
        self._write_error_logged = False
        self._load_existing()

    def _load_existing(self) -> None:
        if not self._path.exists():
            return
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    sample = FailureSample.from_snapshot(data)
                    dedup_key = f"{sample.tool_name}:{sample.target}:{sample.operation}:{sample.error_code}"
                    if dedup_key not in self._samples:
                        self._samples[dedup_key] = sample
                except (json.JSONDecodeError, TypeError):
                    continue
        except (OSError, PermissionError) as exc:
            print(f"failure db load failed ({self._path}): {exc}", file=sys.stderr, flush=True)

    def record(self, *, tool_name: str = "", target: str = "", operation: str = "", error_code: str = "", user_input_snippet: str = "", metadata: dict[str, Any] | None = None) -> FailureSample:
        sample = super().record(tool_name=tool_name, target=target, operation=operation, error_code=error_code, user_input_snippet=user_input_snippet, metadata=metadata)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(sample.snapshot(), ensure_ascii=False) + "\n")
            self._write_error_logged = False
        except OSError as exc:
            if not self._write_error_logged:
                self._write_error_logged = True
                print(f"failure db write failed ({self._path}): {exc}; samples stay in memory only", file=sys.stderr, flush=True)
        return sample


def build_failure_sample_db(path: str | Path | None = None) -> FailureSampleDB:
    if not path:
        return FailureSampleDB()
    return JsonlFailureSampleDB(path)
