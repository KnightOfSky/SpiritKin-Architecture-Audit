from __future__ import annotations

from backend.devices.base import DeviceBackend
from backend.devices.registry import get_device_backend
from backend.executors.base import BaseExecutor, ExecutionRequest, ExecutionResult


class LocalPCExecutor(BaseExecutor):
    """本地桌面执行器：把高层动作请求落到当前 PC 设备后端。"""

    name = "local_pc"

    def __init__(self, device_backend: DeviceBackend | None = None, device_name: str = "local_pc"):
        self._device_name = device_name
        self._device_backend = device_backend or get_device_backend(device_name)

    def supports(self, request: ExecutionRequest) -> bool:
        return request.target in {self.name, "desktop", "pointer", "keyboard", "screen", "app", "software", "browser", "clipboard", "window", "file"}

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        if not self.supports(request):
            return ExecutionResult(
                success=False,
                message=f"不支持的目标: {request.target}",
                error_code="unsupported_target",
                metadata={"target": request.target},
            )

        params = dict(request.params or {})
        operation = request.operation.lower().strip()

        try:
            if operation == "move_pointer":
                data = self._device_backend.move_to(params["x"], params["y"])
            elif operation == "click_pointer":
                x = params["x"]
                y = params["y"]
                double = bool(params.get("double", False))
                data = self._device_backend.double_click(x, y) if double else self._device_backend.click(x, y)
            elif operation == "enter_text":
                data = self._device_backend.type_text(params["text"])
            elif operation == "press_keys":
                keys = list(params.get("keys", []) or [])
                if not keys:
                    return ExecutionResult(
                        success=False,
                        message="缺少参数: keys",
                        error_code="missing_params",
                        metadata={"missing_param": "keys"},
                    )
                data = self._device_backend.press_key(keys[0]) if len(keys) == 1 else self._device_backend.hotkey(*keys)
            elif operation == "launch_app":
                app_name = str(params["app_name"]).strip()
                if not app_name:
                    return ExecutionResult(success=False, message="缺少参数: app_name", error_code="missing_params", metadata={"missing_param": "app_name"})
                launch_app = getattr(self._device_backend, "launch_app", None)
                if launch_app is None:
                    return ExecutionResult(success=False, message="当前设备后端不支持启动应用", error_code="unsupported_operation")
                data = launch_app(app_name)
            elif operation == "close_app":
                app_name = str(params["app_name"]).strip()
                if not app_name:
                    return ExecutionResult(success=False, message="缺少参数: app_name", error_code="missing_params", metadata={"missing_param": "app_name"})
                close_app = getattr(self._device_backend, "close_app", None)
                if close_app is None:
                    return ExecutionResult(success=False, message="当前设备后端不支持关闭应用", error_code="unsupported_operation")
                data = close_app(app_name, force=bool(params.get("force", False)))
            elif operation == "list_installed_apps":
                list_apps = getattr(self._device_backend, "list_installed_apps", None)
                if list_apps is None:
                    return ExecutionResult(success=False, message="当前设备后端不支持扫描软件", error_code="unsupported_operation")
                data = list_apps(limit=int(params.get("limit", 80)))
            elif operation == "list_hardware_devices":
                list_hardware = getattr(self._device_backend, "list_hardware_devices", None)
                if list_hardware is None:
                    return ExecutionResult(success=False, message="当前设备后端不支持扫描硬件设备", error_code="unsupported_operation")
                data = list_hardware(limit=int(params.get("limit", 80)))
            elif operation == "browser_open_url":
                url = str(params.get("url") or "")
                if not url:
                    # Open default browser homepage (no specific URL)
                    import webbrowser
                    webbrowser.open_new("")
                    data = {"url": "(default homepage)", "opened": True}
                else:
                    data = self._require_backend_method("open_url")(url)
            elif operation == "browser_search":
                data = self._require_backend_method("search_web")(str(params.get("query") or ""), engine=str(params.get("engine") or "bing"))
            elif operation == "clipboard_read":
                data = self._require_backend_method("read_clipboard")()
            elif operation == "clipboard_write":
                data = self._require_backend_method("write_clipboard")(str(params.get("text") or ""))
            elif operation == "screen_capture":
                data = self._require_backend_method("capture_screen")(output_path=params.get("output_path"))
            elif operation == "camera_capture":
                data = self._require_backend_method("capture_camera")(output_path=params.get("output_path"), camera_index=int(params.get("camera_index", 0)))
            elif operation == "window_list":
                data = self._require_backend_method("list_windows")(limit=int(params.get("limit", 40)))
            elif operation == "window_activate":
                data = self._require_backend_method("activate_window")(str(params.get("title") or ""))
            elif operation == "window_close":
                data = self._require_backend_method("close_window")(str(params.get("title") or ""), force=bool(params.get("force", False)))
            elif operation == "window_resize":
                data = self._require_backend_method("resize_window")(str(params.get("title") or ""), int(params.get("width", 800)), int(params.get("height", 600)))
            elif operation == "window_move":
                data = self._require_backend_method("move_window")(str(params.get("title") or ""), int(params.get("x", 0)), int(params.get("y", 0)))
            elif operation == "file_search":
                data = self._require_backend_method("search_files")(str(params.get("query") or ""), root=params.get("root"), limit=int(params.get("limit", 20)))
            elif operation == "file_read":
                data = self._require_backend_method("read_file_text")(str(params.get("path") or ""), max_chars=int(params.get("max_chars", 4000)))
            elif operation == "file_open":
                data = self._require_backend_method("open_file")(str(params.get("path") or ""))
            elif operation == "file_write":
                data = self._require_backend_method("write_file_text")(str(params.get("path") or ""), str(params.get("text") or ""))
            elif operation == "file_save_as":
                data = self._require_backend_method("save_text_as")(str(params.get("path") or ""), str(params.get("text") or ""))
            elif operation == "notification_send":
                data = self._require_backend_method("send_notification")(str(params.get("title") or ""), str(params.get("text") or ""))
            elif operation == "browser_tab_list":
                data = self._require_backend_method("list_browser_tabs")()
            elif operation == "browser_tab_activate":
                data = self._require_backend_method("activate_browser_tab")(title=str(params.get("title") or ""), index=int(params.get("index", -1)))
            elif operation == "browser_tab_close":
                data = self._require_backend_method("close_browser_tab")(title=str(params.get("title") or ""), index=int(params.get("index", -1)))
            elif operation == "screen_extract_text":
                data = self._device_backend.extract_text(region=params.get("region"), lang=str(params.get("lang", "chi_sim+eng")))
            elif operation == "screen_understand":
                query = str(params.get("query") or "请描述当前屏幕并指出可操作区域。")
                data = self._device_backend.understand_screen(query=query, region=params.get("region"))
            else:
                return ExecutionResult(
                    success=False,
                    message=f"不支持的操作: {request.operation}",
                    error_code="unsupported_operation",
                    metadata={"operation": request.operation},
                )
        except KeyError as exc:
            return ExecutionResult(
                success=False,
                message=f"缺少参数: {exc.args[0]}",
                error_code="missing_params",
                metadata={"missing_param": exc.args[0]},
            )
        except Exception as exc:
            return ExecutionResult(success=False, message=str(exc), error_code="executor_exception")

        app_display_name = params.get("app_name", "应用")
        if operation == "launch_app" and isinstance(data, dict):
            app_display_name = data.get("display_name") or data.get("resolved_app") or app_display_name
            data.setdefault("command", [data.get("resolved_app") or params.get("app_name")])
        if operation == "close_app" and isinstance(data, dict):
            app_display_name = data.get("display_name") or data.get("app_name") or app_display_name

        def get_val(d, key, default="--"):
            if isinstance(d, dict):
                return d.get(key, default)
            return default

        friendly_messages = {
            "launch_app": f"已由 {self._device_name} 执行打开 {app_display_name}。",
            "close_app": self._summarize_close_app_result(app_display_name, data),
            "list_installed_apps": self._summarize_installed_apps(data),
            "list_hardware_devices": self._summarize_hardware_devices(data),
            "browser_open_url": f"已打开网页：{get_val(data, 'url', params.get('url', '--'))}。",
            "browser_search": f"已搜索：{get_val(data, 'query', params.get('query', '--'))}。",
            "clipboard_read": self._summarize_clipboard_read(data),
            "clipboard_write": self._summarize_clipboard_write(data),
            "screen_capture": f"已截取当前屏幕：{get_val(data, 'path')}。",
            "camera_capture": f"已拍摄摄像头画面：{get_val(data, 'path', get_val(data, 'error'))}。",
            "window_list": self._summarize_window_list(data),
            "window_activate": self._summarize_window_action("激活", data),
            "window_close": self._summarize_window_action("关闭", data),
            "file_search": self._summarize_file_search(data),
            "file_read": self._summarize_file_read(data),
            "file_open": f"已打开文件：{get_val(data, 'path')}。",
            "file_write": f"已写入文件：{get_val(data, 'path', params.get('path'))}，共 {get_val(data, 'length', 0)} 个字符。",
            "file_save_as": f"已保存文件：{get_val(data, 'path', params.get('path'))}，共 {get_val(data, 'length', 0)} 个字符。",
            "notification_send": f"已发送通知：{get_val(data, 'title', params.get('title'))}。",
            "browser_tab_list": self._summarize_browser_tab_list(data),
            "browser_tab_activate": self._summarize_browser_tab_action("切换", data),
            "browser_tab_close": self._summarize_browser_tab_action("关闭", data),
            "window_resize": self._summarize_window_resize(data),
            "window_move": self._summarize_window_move(data),
            "screen_extract_text": "已读取当前屏幕文字。",
            "screen_understand": "已完成当前屏幕理解。",
        }
        return ExecutionResult(
            success=True,
            message=friendly_messages.get(operation, f"执行成功: {self._device_name}.{request.operation}"),
            data=data,
            metadata={"executor": self.name, "device": self._device_name, "target": request.target, "operation": operation},
        )

    def _require_backend_method(self, name: str):
        method = getattr(self._device_backend, name, None)
        if method is None:
            raise RuntimeError(f"当前设备后端不支持 {name}")
        return method

    def _summarize_installed_apps(self, data) -> str:
        if not isinstance(data, list) or not data:
            return f"已扫描 {self._device_name} 的软件清单，但没有发现可展示的软件记录。"
        names = [str(item.get("name") or "").strip() for item in data if isinstance(item, dict) and str(item.get("name") or "").strip()]
        launchable = sum(1 for item in data if isinstance(item, dict) and item.get("can_launch"))
        examples = "、".join(names[:8])
        suffix = f"，例如：{examples}" if examples else ""
        launchable_part = f"，其中 {launchable} 个可直接启动" if launchable else ""
        return f"已扫描 {self._device_name} 的软件清单，发现 {len(data)} 条记录{launchable_part}{suffix}。"

    def _summarize_hardware_devices(self, data) -> str:
        if not isinstance(data, list) or not data:
            return f"已扫描 {self._device_name} 的硬件设备清单，但没有发现可展示的设备记录。"
        names = []
        for item in data:
            if isinstance(item, dict):
                name = str(item.get("FriendlyName") or item.get("name") or item.get("Class") or "").strip()
                if name:
                    names.append(name)
        examples = "、".join(names[:8])
        suffix = f"，例如：{examples}" if examples else ""
        return f"已扫描 {self._device_name} 的硬件设备清单，发现 {len(data)} 条记录{suffix}。"

    @staticmethod
    def _summarize_clipboard_read(data) -> str:
        length = int(data.get("length") or 0) if isinstance(data, dict) else 0
        return f"已读取剪贴板文本，共 {length} 个字符。"

    @staticmethod
    def _summarize_clipboard_write(data) -> str:
        length = int(data.get("length") or 0) if isinstance(data, dict) else 0
        return f"已写入剪贴板，共 {length} 个字符。"

    @staticmethod
    def _summarize_window_list(data) -> str:
        if not isinstance(data, list) or not data:
            return "已读取窗口列表，但没有发现可展示窗口。"
        titles = [str(item.get("title") or item.get("MainWindowTitle") or "").strip() for item in data if isinstance(item, dict)]
        examples = "、".join([title for title in titles if title][:6])
        suffix = f"，例如：{examples}" if examples else ""
        return f"已读取窗口列表，发现 {len(data)} 个窗口{suffix}。"

    @staticmethod
    def _summarize_window_action(action: str, data) -> str:
        matched_count = int(data.get("matched_count") or 0) if isinstance(data, dict) else 0
        title = data.get("title") if isinstance(data, dict) else "窗口"
        if matched_count <= 0:
            return f"没有找到标题匹配“{title}”的窗口。"
        return f"已请求{action}标题匹配“{title}”的窗口，共匹配 {matched_count} 个。"

    @staticmethod
    def _summarize_file_search(data) -> str:
        matches = data.get("matches") if isinstance(data, dict) else None
        if not isinstance(matches, list) or not matches:
            return "已搜索文件，但没有发现匹配项。"
        examples = "、".join(str(item.get("name") or "").strip() for item in matches[:6] if isinstance(item, dict))
        suffix = f"，例如：{examples}" if examples else ""
        return f"已搜索文件，发现 {len(matches)} 个匹配项{suffix}。"

    @staticmethod
    def _summarize_file_read(data) -> str:
        if not isinstance(data, dict):
            return "已读取文件内容。"
        truncated = "，内容已截断" if data.get("truncated") else ""
        return f"已读取文件：{data.get('path', '--')}，共 {data.get('length', 0)} 个字符{truncated}。"

    @staticmethod
    def _summarize_window_resize(data) -> str:
        if not isinstance(data, dict):
            return "已调整窗口大小。"
        matched = int(data.get("matched_count") or 0)
        title = data.get("title", "窗口")
        if matched <= 0:
            return f"没有找到标题匹配“{title}”的窗口。"
        return f"已调整标题匹配“{title}”的窗口大小为 {data.get('width', '--')}x{data.get('height', '--')}，共匹配 {matched} 个。"

    @staticmethod
    def _summarize_window_move(data) -> str:
        if not isinstance(data, dict):
            return "已移动窗口位置。"
        matched = int(data.get("matched_count") or 0)
        title = data.get("title", "窗口")
        if matched <= 0:
            return f"没有找到标题匹配“{title}”的窗口。"
        return f"已移动标题匹配“{title}”的窗口到 ({data.get('x', '--')}, {data.get('y', '--')})，共匹配 {matched} 个。"

    @staticmethod
    def _summarize_browser_tab_list(data) -> str:
        if not isinstance(data, dict):
            return "已列出浏览器标签页。"
        tabs = data.get("tabs") if isinstance(data.get("tabs"), list) else []
        count = len(tabs)
        if count <= 0:
            return "已列出浏览器标签页，但未检测到 Edge/IE 标签页。"
        examples = "、".join(str(t.get("title") or "").strip() for t in tabs[:5] if isinstance(t, dict))
        suffix = f"，例如：{examples}" if examples else ""
        return f"已列出浏览器标签页，检测到 {count} 个标签页{suffix}。"

    @staticmethod
    def _summarize_browser_tab_action(action: str, data) -> str:
        if not isinstance(data, dict):
            return f"已请求{action}浏览器标签页。"
        if not data.get("ok"):
            title = data.get("title", "")
            index = data.get("index", -1)
            detail = f"标题“{title}”" if title else f"索引 {index}"
            return f"未找到{detail}对应的浏览器标签页，{action}未执行。"
        return f"已{action}浏览器标签页：{data.get('title', '--')}。"

    @staticmethod
    def _summarize_close_app_result(app_display_name, data) -> str:
        if isinstance(data, dict):
            closed_count = int(data.get("closed_count") or 0)
            if closed_count > 0:
                return f"已请求关闭 {app_display_name}，共匹配到 {closed_count} 个进程/窗口。"
            return f"没有找到正在运行的 {app_display_name}，所以未执行关闭。"
        return f"已请求关闭 {app_display_name}。"
