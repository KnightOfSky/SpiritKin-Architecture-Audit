from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.tools.base import ExecutionTool, ToolCall, ToolResult, ToolSpec


@dataclass(frozen=True)
class ToolManifestDiscovery:
    tools: tuple[ExecutionTool, ...]
    loaded_files: tuple[str, ...]
    errors: tuple[dict[str, str], ...]
    conflicts: tuple[dict[str, str], ...] = ()
    root_precedence: tuple[str, ...] = ()


class ManifestScriptTool(ExecutionTool):
    def __init__(self, spec: ToolSpec, *, script_path: Path, argv_fields: tuple[str, ...] = ()):
        super().__init__(spec)
        self.script_path = script_path
        self.argv_fields = argv_fields

    def invoke(self, call: ToolCall) -> ToolResult:
        if not self.supports(call):
            return super().invoke(call)
        missing = [field for field in self.argv_fields if field not in call.arguments]
        if missing:
            return ToolResult(
                success=False,
                message=f"缺少参数: {', '.join(missing)}",
                error_code="missing_params",
                metadata={"tool_name": call.name, "missing_params": missing},
            )
        arguments = dict(call.arguments or {})
        arguments["script_path"] = str(self.script_path)
        arguments["args"] = [str(call.arguments[field]) for field in self.argv_fields]
        return super().invoke(ToolCall(call.name, arguments))


def _manifest_roots(roots: list[str | os.PathLike[str]] | None = None) -> list[Path]:
    if roots is None:
        raw = os.getenv("SPIRITKIN_TOOL_MANIFEST_ROOTS", "tools")
        roots = [part for part in raw.split(os.pathsep) if part.strip()]
    resolved = []
    for raw_root in roots:
        root = Path(raw_root)
        if not root.is_absolute():
            root = Path.cwd() / root
        resolved.append(root.resolve())
    return resolved


def _canonical_risk(value: Any) -> str:
    normalized = str(value or "safe").strip().lower()
    if normalized in {"safe", "network", "fs-write", "shell"}:
        return normalized
    return {"low": "safe", "medium": "network", "high": "shell"}.get(normalized, "network")


def _risk_level(value: Any) -> str:
    normalized = str(value or "low").strip().lower()
    return {
        "safe": "low",
        "network": "medium",
        "fs-write": "medium",
        "shell": "high",
    }.get(normalized, normalized if normalized in {"low", "medium", "high"} else "medium")


def _tool_from_manifest(payload: dict[str, Any], *, manifest_path: Path) -> ExecutionTool:
    tool_id = str(payload.get("id") or payload.get("name") or "").strip()
    if not tool_id or "." not in tool_id:
        raise ValueError("manifest tool id must be a non-empty namespaced id")
    entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else {}
    target = str(entry.get("target") or payload.get("target") or "").strip()
    operation = str(entry.get("operation") or payload.get("operation") or "").strip()
    script_value = str(entry.get("script") or "").strip()
    script_path: Path | None = None
    argv_fields: tuple[str, ...] = ()
    if script_value:
        candidate = (manifest_path.parent / script_value).resolve()
        if manifest_path.parent.resolve() not in {candidate, *candidate.parents}:
            raise ValueError("entry.script must stay inside the manifest tool directory")
        if candidate.suffix.lower() != ".py":
            raise ValueError("entry.script must point to a .py file")
        if not candidate.is_file():
            raise ValueError(f"entry.script does not exist: {script_value}")
        if target and target != "python":
            raise ValueError("entry.script target must be python")
        if operation and operation not in {"python.run", "python.execute"}:
            raise ValueError("entry.script operation must be python.run or python.execute")
        target = "python"
        operation = "python.run"
        raw_argv = entry.get("argv") or []
        if not isinstance(raw_argv, list) or any(not str(item).strip() for item in raw_argv):
            raise ValueError("entry.argv must be a list of input field names")
        argv_fields = tuple(str(item).strip() for item in raw_argv)
        script_path = candidate
    if not target or not operation:
        raise ValueError("manifest requires entry.target and entry.operation")
    schema = payload.get("input_schema") if isinstance(payload.get("input_schema"), dict) else payload.get("schema")
    schema = dict(schema) if isinstance(schema, dict) else {}
    spec = ToolSpec(
        name=tool_id,
        description=str(payload.get("description") or tool_id),
        target=target,
        operation=operation,
        risk_level=_risk_level(payload.get("risk")),
        read_only=bool(payload.get("read_only", False)),
        schema=schema,
        authz_risk=_canonical_risk(payload.get("risk")),
    )
    return ManifestScriptTool(spec, script_path=script_path, argv_fields=argv_fields) if script_path else ExecutionTool(spec)


def discover_manifest_tools(roots: list[str | os.PathLike[str]] | None = None) -> ToolManifestDiscovery:
    tools: list[ExecutionTool] = []
    loaded_files: list[str] = []
    errors: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    seen_ids: dict[str, dict[str, str]] = {}
    resolved_roots = _manifest_roots(roots)
    for priority, root in enumerate(resolved_roots):
        if not root.exists() or not root.is_dir():
            continue
        for path in sorted(root.glob("*/manifest.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("manifest must contain a JSON object")
                tool = _tool_from_manifest(payload, manifest_path=path)
                existing = seen_ids.get(tool.spec.name)
                if existing is not None:
                    conflicts.append(
                        {
                            "tool_id": tool.spec.name,
                            "winner_path": existing["path"],
                            "winner_root": existing["root"],
                            "shadowed_path": str(path),
                            "shadowed_root": str(root),
                            "resolution": "first_manifest_root_wins",
                        }
                    )
                    continue
                seen_ids[tool.spec.name] = {"path": str(path), "root": str(root), "priority": str(priority)}
                tools.append(tool)
                loaded_files.append(str(path))
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                errors.append({"path": str(path), "error": str(exc)})
    return ToolManifestDiscovery(
        tuple(tools),
        tuple(loaded_files),
        tuple(errors),
        tuple(conflicts),
        tuple(str(root) for root in resolved_roots),
    )
