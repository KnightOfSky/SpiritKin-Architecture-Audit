from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class UserIdentity:
    user_id: str
    display_name: str
    roles: tuple[str, ...] = ()
    channel_preferences: dict[str, Any] = field(default_factory=dict)


class RoleHierarchy:
    ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
        "admin": ("*",),
        "operator": ("local_pc.*", "remote.*", "feishu.*", "knowledge.search", "openclaw.status"),
        "viewer": ("knowledge.search", "device.status", "hardware.list_devices", "software.list_installed"),
    }

    @classmethod
    def get_capabilities(cls, role: str) -> tuple[str, ...]:
        return cls.ROLE_PERMISSIONS.get(role, ())

    @classmethod
    def get_capabilities_for_roles(cls, roles: tuple[str, ...]) -> tuple[str, ...]:
        caps: set[str] = set()
        for role in roles:
            for cap in cls.get_capabilities(role):
                if cap == "*":
                    return ("*",)
                caps.add(cap)
        return tuple(sorted(caps))


def resolve_actor_identity(
    auth_header: str = "",
    channel: str = "desktop",
    *,
    default_roles: tuple[str, ...] = ("operator",),
) -> UserIdentity:
    if not auth_header:
        return UserIdentity(
            user_id=f"anonymous_{channel}",
            display_name=f"Anonymous ({channel})",
            roles=("viewer",) if channel == "public" else default_roles,
            channel_preferences={"channel": channel},
        )
    return UserIdentity(
        user_id=f"auth_{hash(auth_header) & 0xFFFF:04x}",
        display_name="Authenticated User",
        roles=default_roles,
        channel_preferences={"channel": channel},
    )
