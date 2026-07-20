from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionRequest:
    target: str
    operation: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    success: bool
    message: str = ""
    data: Any = None
    error_code: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseExecutor(ABC):
    name = "base"

    @abstractmethod
    def supports(self, request: ExecutionRequest) -> bool:
        raise NotImplementedError

    @abstractmethod
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError