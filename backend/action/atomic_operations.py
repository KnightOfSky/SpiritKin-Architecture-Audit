from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AtomicOperationSpec:
    """跨设备软件层原子操作定义。

    这里不绑定某个具体软件，而是描述“任意设备节点”都可以实现的最小动作。
    Planner 负责把口语化输入映射到这些 operation，Executor/RemoteNode 决定落到本机还是远端。
    """

    name: str
    operation: str
    description: str
    params_schema: dict[str, Any] = field(default_factory=dict)
    risk_level: str = "low"
    read_only: bool = False
    target_type: str = "software"
    confirmation_policy: str = "risk_based"
    eval_cases: tuple[str, ...] = ()


DEFAULT_ATOMIC_OPERATIONS: tuple[AtomicOperationSpec, ...] = (
    AtomicOperationSpec(
        name="app.launch",
        operation="launch_app",
        description="在目标设备上启动/打开一个应用或软件。",
        params_schema={"app_name": "str"},
        risk_level="medium",
    ),
    AtomicOperationSpec(
        name="app.close",
        operation="close_app",
        description="在目标设备上关闭一个正在运行的应用或软件，默认优先尝试正常关闭窗口。",
        params_schema={"app_name": "str", "force": "bool"},
        risk_level="high",
        eval_cases=("关闭火豹浏览器", "退出微信"),
    ),
    AtomicOperationSpec(
        name="screen.read_text",
        operation="screen_extract_text",
        description="读取目标设备当前屏幕上的文字。",
        read_only=True,
    ),
    AtomicOperationSpec(
        name="screen.ask",
        operation="screen_understand",
        description="根据视觉模型回答目标设备当前屏幕相关问题。",
        params_schema={"query": "str"},
        read_only=True,
    ),
    AtomicOperationSpec(
        name="screen.capture",
        operation="screen_capture",
        description="截取目标设备当前屏幕并返回截图文件路径。",
        params_schema={"output_path": "str"},
        read_only=True,
    ),
    AtomicOperationSpec(
        name="camera.capture",
        operation="camera_capture",
        description="拍摄目标设备摄像头的一帧画面并保存为 JPG。",
        params_schema={"output_path": "str", "camera_index": "int"},
        read_only=True,
        risk_level="medium",
        target_type="camera",
        eval_cases=("拍一张照", "摄像头画面", "自拍"),
    ),
    AtomicOperationSpec(
        name="clipboard.read",
        operation="clipboard_read",
        description="读取目标设备剪贴板文本内容。剪贴板可能包含隐私信息，默认需要确认。",
        read_only=True,
        risk_level="high",
        target_type="clipboard",
    ),
    AtomicOperationSpec(
        name="clipboard.write",
        operation="clipboard_write",
        description="向目标设备剪贴板写入文本。",
        params_schema={"text": "str"},
        risk_level="high",
        target_type="clipboard",
    ),
    AtomicOperationSpec(
        name="browser.open_url",
        operation="browser_open_url",
        description="用目标设备默认浏览器打开指定 URL。",
        params_schema={"url": "str"},
        risk_level="medium",
        target_type="browser",
    ),
    AtomicOperationSpec(
        name="browser.search",
        operation="browser_search",
        description="用目标设备默认浏览器搜索关键词。",
        params_schema={"query": "str", "engine": "str"},
        risk_level="medium",
        target_type="browser",
    ),
    AtomicOperationSpec(
        name="browser.tab.list",
        operation="browser_tab_list",
        description="列出当前浏览器打开的标签页（当前最小实现仅支持 Edge/IE COM 接口）。",
        read_only=True,
        target_type="browser",
        risk_level="low",
        confirmation_policy="never",
    ),
    AtomicOperationSpec(
        name="browser.tab.activate",
        operation="browser_tab_activate",
        description="切换到指定标题或索引的浏览器标签页（当前最小实现仅支持 Edge/IE COM 接口）。",
        params_schema={"title": "str", "index": "int"},
        risk_level="medium",
        target_type="browser",
    ),
    AtomicOperationSpec(
        name="browser.tab.close",
        operation="browser_tab_close",
        description="关闭指定标题或索引的浏览器标签页（当前最小实现仅支持 Edge/IE COM 接口）。",
        params_schema={"title": "str", "index": "int"},
        risk_level="medium",
        target_type="browser",
    ),
    AtomicOperationSpec(
        name="notification.send",
        operation="notification_send",
        description="向目标设备发送一条桌面通知/弹窗提醒。",
        params_schema={"title": "str", "text": "str"},
        risk_level="low",
        target_type="notification",
    ),
    AtomicOperationSpec(
        name="file.search",
        operation="file_search",
        description="在限定目录内搜索文件名。默认优先当前工作区/当前目录。",
        params_schema={"query": "str", "root": "str", "limit": "int"},
        read_only=True,
        target_type="file",
        confirmation_policy="never",
    ),
    AtomicOperationSpec(
        name="file.read",
        operation="file_read",
        description="读取文本文件内容。可能涉及隐私/敏感信息，默认需要确认。",
        params_schema={"path": "str", "max_chars": "int"},
        read_only=True,
        risk_level="high",
        target_type="file",
    ),
    AtomicOperationSpec(
        name="file.open",
        operation="file_open",
        description="用系统默认程序打开文件或文件夹。",
        params_schema={"path": "str"},
        risk_level="medium",
        target_type="file",
    ),
    AtomicOperationSpec(
        name="file.write",
        operation="file_write",
        description="将文本写入指定文件路径（覆盖写入）。",
        params_schema={"path": "str", "text": "str"},
        risk_level="high",
        target_type="file",
    ),
    AtomicOperationSpec(
        name="file.save_as",
        operation="file_save_as",
        description="将文本保存到新文件路径（覆盖写入）。",
        params_schema={"path": "str", "text": "str"},
        risk_level="high",
        target_type="file",
    ),
    AtomicOperationSpec(
        name="window.list",
        operation="window_list",
        description="列出目标设备当前可见窗口。",
        params_schema={"limit": "int"},
        read_only=True,
        target_type="window",
        confirmation_policy="never",
    ),
    AtomicOperationSpec(
        name="window.activate",
        operation="window_activate",
        description="激活/切换到标题匹配的窗口。",
        params_schema={"title": "str"},
        risk_level="medium",
        target_type="window",
    ),
    AtomicOperationSpec(
        name="window.close",
        operation="window_close",
        description="关闭标题匹配的窗口。",
        params_schema={"title": "str", "force": "bool"},
        risk_level="high",
        target_type="window",
    ),
    AtomicOperationSpec(
        name="window.resize",
        operation="window_resize",
        description="调整标题匹配窗口的大小。",
        params_schema={"title": "str", "width": "int", "height": "int"},
        risk_level="medium",
        target_type="window",
    ),
    AtomicOperationSpec(
        name="window.move",
        operation="window_move",
        description="移动标题匹配窗口到指定坐标。",
        params_schema={"title": "str", "x": "int", "y": "int"},
        risk_level="medium",
        target_type="window",
    ),
    AtomicOperationSpec(
        name="pointer.move",
        operation="move_pointer",
        description="把目标设备鼠标移动到指定坐标。",
        params_schema={"x": "int", "y": "int"},
    ),
    AtomicOperationSpec(
        name="pointer.click",
        operation="click_pointer",
        description="在目标设备指定坐标执行鼠标单击或双击。",
        params_schema={"x": "int", "y": "int", "double": "bool"},
        risk_level="medium",
    ),
    AtomicOperationSpec(
        name="text.input",
        operation="enter_text",
        description="向目标设备当前焦点窗口输入文本。",
        params_schema={"text": "str"},
        risk_level="medium",
    ),
    AtomicOperationSpec(
        name="keyboard.press",
        operation="press_keys",
        description="在目标设备执行单键或组合键。",
        params_schema={"keys": "list[str]"},
        risk_level="medium",
    ),
    AtomicOperationSpec(
        name="software.list_installed",
        operation="list_installed_apps",
        description="读取目标设备已安装软件/应用清单。",
        params_schema={"limit": "int"},
        read_only=True,
        target_type="inventory",
        confirmation_policy="never",
        eval_cases=("扫描本机软件", "列出本机应用"),
    ),
    AtomicOperationSpec(
        name="hardware.list_devices",
        operation="list_hardware_devices",
        description="读取目标设备硬件、外设或 USB 设备清单。",
        params_schema={"limit": "int"},
        read_only=True,
        target_type="inventory",
        confirmation_policy="never",
        eval_cases=("列出本机硬件设备", "扫描当前电脑外设"),
    ),
    AtomicOperationSpec(
        name="android.notification.push",
        operation="push_notification",
        description="向 Android 设备推送通知消息。",
        params_schema={"title": "str", "body": "str", "device_id": "str"},
        risk_level="low",
        target_type="android",
    ),
    AtomicOperationSpec(
        name="android.device.info",
        operation="device_status",
        description="获取 Android 设备基本信息（型号、电池、系统版本等）。",
        read_only=True,
        target_type="android",
        confirmation_policy="never",
    ),
    AtomicOperationSpec(
        name="android.app.launch",
        operation="launch_app",
        description="在 Android 设备上启动应用。",
        params_schema={"app_name": "str"},
        risk_level="medium",
        target_type="android",
    ),
    AtomicOperationSpec(
        name="android.app.close",
        operation="close_app",
        description="在 Android 设备上关闭应用。",
        params_schema={"app_name": "str", "force": "bool"},
        risk_level="medium",
        target_type="android",
    ),
    AtomicOperationSpec(
        name="android.software.list",
        operation="list_installed_apps",
        description="获取 Android 设备已安装应用清单（需 Companion App 上报）。",
        params_schema={"limit": "int"},
        read_only=True,
        target_type="android",
        confirmation_policy="never",
    ),
    AtomicOperationSpec(
        name="android.battery.status",
        operation="device_status",
        description="获取 Android 设备电池电量和充电状态。",
        read_only=True,
        target_type="android",
        confirmation_policy="never",
    ),
    AtomicOperationSpec(
        name="ios.shortcut.query",
        operation="shortcut_query",
        description="通过 iOS 快捷指令发起的文本查询。",
        params_schema={"text": "str"},
        read_only=True,
        target_type="ios",
    ),
    AtomicOperationSpec(
        name="ios.notification.push",
        operation="push_notification",
        description="向 iOS 设备推送通知（通过快捷指令通知动作）。",
        params_schema={"title": "str", "body": "str"},
        risk_level="low",
        target_type="ios",
    ),
    AtomicOperationSpec(
        name="ios.battery.status",
        operation="device_status",
        description="获取 iOS 设备电池状态。",
        read_only=True,
        target_type="ios",
        confirmation_policy="never",
    ),
)


def list_default_atomic_operations() -> list[AtomicOperationSpec]:
    return list(DEFAULT_ATOMIC_OPERATIONS)