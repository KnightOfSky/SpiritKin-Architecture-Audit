from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RUNTIME_CONTRACT_SCHEMA_VERSION = "spiritkin.runtime_contract.v1"


@dataclass(frozen=True)
class ContractValidationResult:
    valid: bool
    issues: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {"valid": self.valid, "issues": list(self.issues)}


@dataclass(frozen=True)
class RuntimeContract:
    object_type: str
    object_id: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    resources: tuple[str, ...] = ()
    permission: str = ""
    schema_ref: str = ""
    version: str = "1.0.0"

    def validate_input(self, payload: Any) -> ContractValidationResult:
        return validate_schema(payload, self.input_schema, path="input")

    def validate_output(self, payload: Any) -> ContractValidationResult:
        return validate_schema(payload, self.output_schema, path="output")

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_CONTRACT_SCHEMA_VERSION,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "version": self.version,
            "input": dict(self.input_schema),
            "output": dict(self.output_schema),
            "resource": list(self.resources),
            "permission": self.permission,
            "schema": self.schema_ref,
        }


def validate_schema(value: Any, schema: dict[str, Any] | None, *, path: str = "value") -> ContractValidationResult:
    definition = dict(schema or {})
    if not definition:
        return ContractValidationResult(True)
    issues: list[str] = []
    _validate_value(value, definition, path, issues)
    return ContractValidationResult(not issues, tuple(issues))


def _validate_value(value: Any, schema: dict[str, Any], path: str, issues: list[str]) -> None:
    expected = str(schema.get("type") or "").strip().lower()
    type_matches = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if expected and expected in type_matches and not type_matches[expected](value):
        issues.append(f"{path}:expected_{expected}")
        return
    if isinstance(value, dict):
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        for key in required:
            if str(key) not in value:
                issues.append(f"{path}.{key}:required")
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for key, nested in properties.items():
            if key in value and isinstance(nested, dict):
                _validate_value(value[key], nested, f"{path}.{key}", issues)
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    issues.append(f"{path}.{key}:additional_property")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], f"{path}[{index}]", issues)
    if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
        issues.append(f"{path}:not_in_enum")
