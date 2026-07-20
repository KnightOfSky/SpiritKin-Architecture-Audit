from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from backend.skills.base import SkillSpec


class SkillSpecStore(ABC):
    @abstractmethod
    def save(self, spec: SkillSpec) -> None:
        raise NotImplementedError

    @abstractmethod
    def load(self, name: str) -> SkillSpec | None:
        raise NotImplementedError

    @abstractmethod
    def list_all(self) -> list[SkillSpec]:
        raise NotImplementedError

    @abstractmethod
    def delete(self, name: str) -> bool:
        raise NotImplementedError


class InMemorySkillSpecStore(SkillSpecStore):
    def __init__(self):
        self._specs: dict[str, SkillSpec] = {}

    def save(self, spec: SkillSpec) -> None:
        self._specs[spec.name] = spec

    def load(self, name: str) -> SkillSpec | None:
        return self._specs.get(name)

    def list_all(self) -> list[SkillSpec]:
        return list(self._specs.values())

    def delete(self, name: str) -> bool:
        if name in self._specs:
            del self._specs[name]
            return True
        return False


class JsonlSkillSpecStore(InMemorySkillSpecStore):
    def __init__(self, path: str | Path):
        super().__init__()
        self._path = Path(path).resolve()
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
                    snapshot = json.loads(line)
                    spec = self._dict_to_spec(snapshot)
                    if spec:
                        super().save(spec)
                except (json.JSONDecodeError, TypeError):
                    continue
        except (OSError, PermissionError):
            pass

    def save(self, spec: SkillSpec) -> None:
        super().save(spec)
        self._append(spec)

    def delete(self, name: str) -> bool:
        removed = super().delete(name)
        if removed:
            self._rewrite()
        return removed

    def _append(self, spec: SkillSpec) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(self._spec_to_dict(spec), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _rewrite(self) -> None:
        try:
            lines = [json.dumps(self._spec_to_dict(spec), ensure_ascii=False) + "\n" for spec in self.list_all()]
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("".join(lines), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _spec_to_dict(spec: SkillSpec) -> dict:
        return {
            "name": spec.name,
            "description": spec.description,
            "trigger_intents": list(spec.trigger_intents),
            "input_schema": spec.input_schema,
            "preconditions": list(spec.preconditions),
            "steps": [
                {
                    "tool_name": s.tool_name,
                    "arguments": s.arguments,
                    "description": s.description,
                    "optional": s.optional,
                }
                for s in spec.steps
            ],
            "tool_allowlist": list(spec.tool_allowlist),
            "risk_level": spec.risk_level,
            "confirmation_policy": spec.confirmation_policy,
            "rollback_strategy": spec.rollback_strategy,
            "success_criteria": list(spec.success_criteria),
            "memory_policy": spec.memory_policy,
            "eval_cases": list(spec.eval_cases),
            "version": spec.version,
            "usage_count": spec.usage_count,
            "metadata": spec.metadata,
            "output_schema": spec.output_schema,
            "cost_hint": spec.cost_hint,
            "latency_hint_ms": spec.latency_hint_ms,
            "success_rate": spec.success_rate,
            "required_capabilities": list(spec.required_capabilities),
            "required_worker_needs": list(spec.required_worker_needs),
            "side_effects": list(spec.side_effects),
            "artifact_contract": spec.artifact_contract,
        }

    @staticmethod
    def _dict_to_spec(snapshot: dict) -> SkillSpec | None:
        try:
            from backend.skills.base import SkillStepSpec
            steps = tuple(
                SkillStepSpec(
                    tool_name=s.get("tool_name", ""),
                    arguments=s.get("arguments", {}),
                    description=s.get("description", ""),
                    optional=s.get("optional", False),
                )
                for s in snapshot.get("steps", [])
            )
            return SkillSpec(
                name=snapshot.get("name", ""),
                description=snapshot.get("description", ""),
                trigger_intents=tuple(snapshot.get("trigger_intents", [])),
                input_schema=snapshot.get("input_schema", {}),
                preconditions=tuple(snapshot.get("preconditions", [])),
                steps=steps,
                tool_allowlist=tuple(snapshot.get("tool_allowlist", [])),
                risk_level=snapshot.get("risk_level", "low"),
                confirmation_policy=snapshot.get("confirmation_policy", "risk_based"),
                rollback_strategy=snapshot.get("rollback_strategy", "manual_review"),
                success_criteria=tuple(snapshot.get("success_criteria", [])),
                memory_policy=snapshot.get("memory_policy", "record_summary"),
                eval_cases=tuple(snapshot.get("eval_cases", [])),
                version=snapshot.get("version", "0.1.0"),
                usage_count=snapshot.get("usage_count", 0),
                metadata=snapshot.get("metadata", {}),
                output_schema=snapshot.get("output_schema", {}),
                cost_hint=snapshot.get("cost_hint", ""),
                latency_hint_ms=snapshot.get("latency_hint_ms"),
                success_rate=snapshot.get("success_rate"),
                required_capabilities=tuple(snapshot.get("required_capabilities", [])),
                required_worker_needs=tuple(snapshot.get("required_worker_needs", [])),
                side_effects=tuple(snapshot.get("side_effects", [])),
                artifact_contract=snapshot.get("artifact_contract", {}),
            )
        except Exception:
            return None


def build_skill_store(path: str | Path | None = None) -> SkillSpecStore:
    if not path:
        return InMemorySkillSpecStore()
    return JsonlSkillSpecStore(path)
