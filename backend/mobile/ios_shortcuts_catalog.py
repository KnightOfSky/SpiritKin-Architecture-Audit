from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ShortcutDefinition:
    name: str
    description: str
    icon: str = "command"
    color: str = "blue"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_type: str = "json"
    example_usage: str = ""
    confirmation_required: bool = False


SHORTCUT_CATALOG: tuple[ShortcutDefinition, ...] = (
    ShortcutDefinition(
        name="Ask Spirit",
        description="向 SpiritKin AI 语音/文字助手提问",
        icon="text.bubble",
        color="blue",
        input_schema={"text": "str"},
        output_type="json",
        example_usage="今天天气怎么样",
    ),
    ShortcutDefinition(
        name="Read Clipboard",
        description="读取 iOS 剪贴板内容并发送给 SpiritKin",
        icon="doc.on.clipboard",
        color="green",
        input_schema={"text": "str"},
        output_type="json",
    ),
    ShortcutDefinition(
        name="Write Clipboard",
        description="将 SpiritKin 回复写入 iOS 剪贴板",
        icon="arrow.right.doc.on.clipboard",
        color="orange",
        input_schema={"text": "str"},
        output_type="text",
    ),
    ShortcutDefinition(
        name="Check Spirit Status",
        description="检查已配对的 SpiritKin Runtime 是否在线",
        icon="heart.text.square",
        color="blue",
        output_type="text",
    ),
    ShortcutDefinition(
        name="Send Notification",
        description="在当前 iPhone 上发送本地通知",
        icon="bell",
        color="yellow",
        input_schema={"text": "str"},
        output_type="json",
        confirmation_required=True,
    ),
    ShortcutDefinition(
        name="Check Battery",
        description="获取当前 iOS 设备电池状态",
        icon="battery.100",
        color="green",
        input_schema={},
        output_type="json",
    ),
)
