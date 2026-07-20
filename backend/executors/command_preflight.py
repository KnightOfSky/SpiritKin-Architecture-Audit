from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutablePreflight:
    executable: str
    available: bool
    resolved_path: str = ""
    install_suggestion: str = ""

    def failure_context(self) -> dict[str, object]:
        return {
            "kind": "fixable",
            "reason": "missing_executable",
            "executable": self.executable,
            "install_suggestion": self.install_suggestion,
        }


def check_executable(executable: str, *, install_suggestion: str = "") -> ExecutablePreflight:
    value = str(executable or "").strip()
    if not value:
        return ExecutablePreflight(value, False, install_suggestion=install_suggestion)
    path = Path(value)
    if path.is_absolute() or "/" in value or "\\" in value:
        available = path.exists() and path.is_file()
        return ExecutablePreflight(
            value,
            available,
            resolved_path=str(path.resolve()) if available else "",
            install_suggestion=install_suggestion,
        )
    resolved = shutil.which(value)
    return ExecutablePreflight(
        value,
        bool(resolved),
        resolved_path=str(resolved or ""),
        install_suggestion=install_suggestion,
    )
