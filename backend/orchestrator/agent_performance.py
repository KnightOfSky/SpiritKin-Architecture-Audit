from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentPerformanceRecord:
    agent_name: str
    domain: str
    success: bool
    latency_ms: float = 0.0
    user_input_snippet: str = ""
    error_code: str = ""
    recorded_at: float = field(default_factory=time.time)


class AgentPerformanceTracker:
    def __init__(self, window_size: int = 100):
        self._records: list[AgentPerformanceRecord] = []
        self._window = max(5, window_size)

    def record(self, *, agent_name: str = "", domain: str = "", success: bool = True, latency_ms: float = 0.0, user_input_snippet: str = "", error_code: str = "") -> None:
        self._records.append(AgentPerformanceRecord(
            agent_name=agent_name, domain=domain, success=success,
            latency_ms=latency_ms, user_input_snippet=user_input_snippet[:200], error_code=error_code,
        ))
        if len(self._records) > self._window:
            self._records = self._records[-self._window:]

    def rank_agents(self) -> list[dict[str, Any]]:
        stats: dict[str, dict[str, Any]] = {}
        for rec in self._records:
            key = rec.agent_name or "unknown"
            if key not in stats:
                stats[key] = {"agent_name": key, "total": 0, "successes": 0, "failures": 0, "total_latency_ms": 0.0, "last_seen": 0.0}
            s = stats[key]
            s["total"] += 1
            if rec.success:
                s["successes"] += 1
            else:
                s["failures"] += 1
            s["total_latency_ms"] += rec.latency_ms
            s["last_seen"] = max(s["last_seen"], rec.recorded_at)

        ranked = []
        for s in stats.values():
            s["success_rate"] = s["successes"] / max(1, s["total"])
            s["avg_latency_ms"] = s["total_latency_ms"] / max(1, s["total"])
            ranked.append(s)
        ranked.sort(key=lambda s: (s["success_rate"], -s["avg_latency_ms"]), reverse=True)
        return ranked

    def suggest_prompts(self, agent_name: str) -> list[str]:
        failures = [r for r in self._records if r.agent_name == agent_name and not r.success]
        suggestions: list[str] = []
        if not failures:
            return suggestions

        error_codes: dict[str, int] = {}
        for f in failures:
            if f.error_code:
                error_codes[f.error_code] = error_codes.get(f.error_code, 0) + 1

        top_error = max(error_codes, key=error_codes.get) if error_codes else ""
        if top_error == "executor_not_found":
            suggestions.append(f"Agent [{agent_name}] 大量失败来自执行器缺失，建议检查工具注册表")
        elif top_error == "tool_not_registered":
            suggestions.append(f"Agent [{agent_name}] 工具未注册，建议补全 ToolSpec")
        elif top_error == "executor_failed":
            suggestions.append(f"Agent [{agent_name}] 执行器失败率高，建议检查 DeviceBackend 实现")

        recent_rate = sum(1 for r in self._records[-20:] if r.agent_name == agent_name and r.success) / max(1, sum(1 for r in self._records[-20:] if r.agent_name == agent_name))
        if recent_rate < 0.5 and len(failures) >= 3:
            suggestions.append(f"Agent [{agent_name}] 近期成功率 {recent_rate:.0%}，建议人工审核或降权")
        return suggestions

    def stats(self) -> dict[str, Any]:
        ranked = self.rank_agents()
        return {
            "total_records": len(self._records),
            "agents_tracked": len(ranked),
            "top_performer": ranked[0]["agent_name"] if ranked else "",
            "ranking": ranked[:5],
        }
