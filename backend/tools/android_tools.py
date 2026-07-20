from __future__ import annotations

from backend.tools.base import ExecutionTool, ToolSpec


def get_android_tools() -> list[ExecutionTool]:
    return [
        ExecutionTool(ToolSpec(
            name="android.device.info",
            description="获取 Android 设备基本信息（型号、系统版本、电池等）",
            target="android_device",
            operation="device_status",
            risk_level="low",
            read_only=True,
            schema={"device_id": "str"},
        )),
        ExecutionTool(ToolSpec(
            name="android.notification.push",
            description="向 Android 设备推送通知",
            target="android_device",
            operation="push_notification",
            risk_level="low",
            schema={"title": "str", "body": "str", "device_id": "str"},
        )),
        ExecutionTool(ToolSpec(
            name="android.app.launch",
            description="在 Android 设备上启动应用",
            target="android_device",
            operation="launch_app",
            risk_level="medium",
            schema={"app_name": "str"},
        )),
        ExecutionTool(ToolSpec(
            name="android.app.close",
            description="在 Android 设备上关闭应用",
            target="android_device",
            operation="close_app",
            risk_level="medium",
            schema={"app_name": "str", "force": "bool"},
        )),
        ExecutionTool(ToolSpec(
            name="android.software.list",
            description="获取 Android 设备已安装应用清单",
            target="android_device",
            operation="list_installed_apps",
            risk_level="low",
            read_only=True,
            schema={"limit": "int"},
        )),
        ExecutionTool(ToolSpec(
            name="android.battery.status",
            description="获取 Android 设备电池状态",
            target="android_device",
            operation="device_status",
            risk_level="low",
            read_only=True,
            schema={"device_id": "str"},
        )),
    ]
