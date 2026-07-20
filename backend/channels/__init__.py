"""External message channel contracts and adapters."""

from backend.channels.wechat_ilink import (
    ILinkAuthError,
    ILinkConfig,
    ILinkProtocolClient,
    ILinkSessionExpired,
    WeChatILinkChannel,
    build_ilink_channel_from_env,
)

__all__ = [
    "ILinkAuthError",
    "ILinkConfig",
    "ILinkProtocolClient",
    "ILinkSessionExpired",
    "WeChatILinkChannel",
    "build_ilink_channel_from_env",
]
