from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

PROVIDER_CONTRACT_SCHEMA_VERSION = "spiritkin.provider_contract.v1"
PROVIDER_TYPES = frozenset({"model", "tool", "worker", "vision", "storage"})


@dataclass(frozen=True)
class ProviderContract:
    provider_id: str
    provider_type: str
    version: str = "1.0.0"
    capabilities: tuple[str, ...] = ()
    status: str = "unknown"
    locality: str = ""
    permission: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.provider_id or "").strip():
            raise ValueError("provider_id is required")
        if self.provider_type not in PROVIDER_TYPES:
            raise ValueError(f"unsupported provider_type: {self.provider_type}")

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": PROVIDER_CONTRACT_SCHEMA_VERSION,
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "status": self.status,
            "locality": self.locality,
            "permission": self.permission,
            "metadata": dict(self.metadata),
        }


@runtime_checkable
class RuntimeProvider(Protocol):
    def provider_contract(self) -> ProviderContract: ...


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, RuntimeProvider] = {}

    def register(self, provider: RuntimeProvider) -> ProviderContract:
        contract = provider.provider_contract()
        self._providers[contract.provider_id] = provider
        return contract

    def unregister(self, provider_id: str) -> bool:
        return self._providers.pop(str(provider_id), None) is not None

    def get(self, provider_id: str) -> RuntimeProvider | None:
        return self._providers.get(str(provider_id))

    def list_contracts(self, *, provider_type: str = "") -> list[ProviderContract]:
        contracts = [provider.provider_contract() for provider in self._providers.values()]
        if provider_type:
            contracts = [contract for contract in contracts if contract.provider_type == provider_type]
        return sorted(contracts, key=lambda contract: (contract.provider_type, contract.provider_id))

    def snapshot(self) -> dict[str, Any]:
        contracts = self.list_contracts()
        return {
            "schema_version": PROVIDER_CONTRACT_SCHEMA_VERSION,
            "provider_count": len(contracts),
            "providers": [contract.snapshot() for contract in contracts],
        }
