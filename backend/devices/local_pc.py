from __future__ import annotations

import difflib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus, urlparse


class LocalPCDevice:
    """本地 PC 设备适配器，为将来远端设备接入保留统一接口。"""

    name = "local_pc"
    _DEFAULT_BROWSER_ALIASES = {"browser", "defaultbrowser", "默认浏览器", "浏览器", "游览器", "留览器"}
    _SYSTEM_APP_ALIASES = {
        "cmd": ("cmd.exe", "命令提示符"),
        "cmdexe": ("cmd.exe", "命令提示符"),
        "命令提示符": ("cmd.exe", "命令提示符"),
        "命令行": ("cmd.exe", "命令提示符"),
        "控制台": ("cmd.exe", "命令提示符"),
        "powershell": ("powershell.exe", "PowerShell"),
        "pwsh": ("pwsh.exe", "PowerShell"),
        "terminal": ("wt.exe", "Windows Terminal"),
        "windowsterminal": ("wt.exe", "Windows Terminal"),
        "终端": ("wt.exe", "Windows Terminal"),
    }
    _APP_ALIASES = {
        "msedge": ("msedge", "Edge 浏览器", ("microsoft edge", "edge", "msedge")),
        "edge": ("msedge", "Edge 浏览器", ("microsoft edge", "edge", "msedge")),
        "microsoftedge": ("msedge", "Edge 浏览器", ("microsoft edge", "edge", "msedge")),
        "chrome": ("chrome", "Chrome 浏览器", ("google chrome", "chrome", "谷歌浏览器")),
        "googlechrome": ("chrome", "Chrome 浏览器", ("google chrome", "chrome", "谷歌浏览器")),
        "谷歌": ("chrome", "Chrome 浏览器", ("google chrome", "chrome", "谷歌浏览器")),
        "谷歌浏览器": ("chrome", "Chrome 浏览器", ("google chrome", "chrome", "谷歌浏览器")),
        "firefox": ("firefox", "Firefox 浏览器", ("mozilla firefox", "firefox", "火狐浏览器")),
        "火狐": ("firefox", "Firefox 浏览器", ("mozilla firefox", "firefox", "火狐浏览器")),
        "火狐浏览器": ("firefox", "Firefox 浏览器", ("mozilla firefox", "firefox", "火狐浏览器")),
        "brave": ("brave", "Brave 浏览器", ("brave browser", "brave")),
        "bravebrowser": ("brave", "Brave 浏览器", ("brave browser", "brave")),
        "opera": ("opera", "Opera 浏览器", ("opera browser", "opera")),
        "360": ("360se", "360 浏览器", ("360安全浏览器", "360极速浏览器", "360se", "360chrome")),
        "360浏览器": ("360se", "360 浏览器", ("360安全浏览器", "360极速浏览器", "360se", "360chrome")),
        "qq浏览器": ("QQBrowser", "QQ 浏览器", ("qqbrowser", "qq浏览器", "腾讯浏览器")),
        "qqbrowser": ("QQBrowser", "QQ 浏览器", ("qqbrowser", "qq浏览器", "腾讯浏览器")),
        "搜狗浏览器": ("SogouExplorer", "搜狗浏览器", ("sogouexplorer", "搜狗浏览器")),
        "火豹": ("火豹浏览器", "火豹浏览器", ("火豹浏览器", "火豹", "huobao", "huobaobrowser")),
        "火豹浏览器": ("火豹浏览器", "火豹浏览器", ("火豹浏览器", "火豹", "huobao", "huobaobrowser")),
        "火爆": ("火豹浏览器", "火豹浏览器", ("火豹浏览器", "火豹", "火爆", "huobao", "huobaobrowser")),
        "火爆浏览器": ("火豹浏览器", "火豹浏览器", ("火豹浏览器", "火豹", "火爆", "huobao", "huobaobrowser")),
        "火暴浏览器": ("火豹浏览器", "火豹浏览器", ("火豹浏览器", "火豹", "火暴", "huobao", "huobaobrowser")),
        "code": ("code", "VSCode", ("visual studio code", "vscode", "code")),
        "vscode": ("code", "VSCode", ("visual studio code", "vscode", "code")),
        "visualstudiocode": ("code", "VSCode", ("visual studio code", "vscode", "code")),
        "wechat": ("WeChat", "微信", ("wechat", "微信")),
        "微信": ("WeChat", "微信", ("wechat", "微信")),
        "dingtalk": ("DingTalk", "钉钉", ("dingtalk", "钉钉")),
        "钉钉": ("DingTalk", "钉钉", ("dingtalk", "钉钉")),
    }

    _KNOWN_CLI_TOOLS: tuple[tuple[str, str, str], ...] = (
        ("ffmpeg", "media", "音视频转码/剪辑/混流"),
        ("ffprobe", "media", "媒体信息探测"),
        ("yt-dlp", "download", "在线视频下载"),
        ("youtube-dl", "download", "在线视频下载(旧)"),
        ("gallery-dl", "download", "图站素材批量下载"),
        ("aria2c", "download", "多线程下载器"),
        ("curl", "download", "HTTP 下载/请求"),
        ("wget", "download", "HTTP 下载"),
        ("git", "vcs", "版本控制"),
        ("python", "runtime", "Python 运行时"),
        ("node", "runtime", "Node.js 运行时"),
        ("npm", "runtime", "Node 包管理"),
        ("pip", "runtime", "Python 包管理"),
        ("adb", "device", "Android 调试桥"),
        ("magick", "image", "ImageMagick 图像处理"),
        ("pandoc", "document", "文档格式转换"),
        ("7z", "archive", "7-Zip 压缩解压"),
    )

    def __init__(self):
        self._installed_apps_cache: list[dict[str, object]] | None = None
        self._cli_tools_cache: list[dict[str, object]] | None = None

    def list_cli_tools(self, limit: int = 80, refresh: bool = False):
        """Probe well-known command-line tools on PATH (ffmpeg/yt-dlp/git/...).

        Complements list_installed_apps, which only sees GUI apps with
        registry/Start-Menu entries and misses PATH-resident CLIs.
        """
        if refresh or self._cli_tools_cache is None:
            records: list[dict[str, object]] = []
            for name, category, description in self._KNOWN_CLI_TOOLS:
                resolved = shutil.which(name)
                records.append(
                    {
                        "name": name,
                        "category": category,
                        "description": description,
                        "path": resolved or "",
                        "available": bool(resolved),
                    }
                )
            self._cli_tools_cache = records
        return self._cli_tools_cache[: int(limit)]

    @staticmethod
    def _hidden_subprocess_kwargs() -> dict[str, object]:
        if platform.system().lower() != "windows":
            return {}
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}

    @staticmethod
    def _run_text(command: list[str], *, timeout: float, input: str | None = None):
        return subprocess.run(
            command,
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **LocalPCDevice._hidden_subprocess_kwargs(),
        )

    @staticmethod
    def _popen(command, *, shell: bool, visible: bool = False):
        kwargs = {} if visible else LocalPCDevice._hidden_subprocess_kwargs()
        return subprocess.Popen(command, shell=shell, **kwargs)

    @staticmethod
    def _pyautogui():
        import pyautogui

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.2
        return pyautogui

    def get_screen_size(self):
        return self._pyautogui().size()

    def move_to(self, x: int, y: int):
        return self._pyautogui().moveTo(x, y)

    def click(self, x: int, y: int):
        return self._pyautogui().click(x, y)

    def double_click(self, x: int, y: int):
        return self._pyautogui().doubleClick(x, y)

    def type_text(self, text: str):
        return self._pyautogui().typewrite(text, interval=0.05)

    def press_key(self, key: str):
        return self._pyautogui().press(key)

    def hotkey(self, *keys: str):
        return self._pyautogui().hotkey(*keys)

    def launch_app(self, app_name: str):
        normalized_app = str(app_name or "").strip()
        compact_app = self._compact_app_name(normalized_app)
        if compact_app in self._DEFAULT_BROWSER_ALIASES:
            start_url = os.getenv("SPIRITKIN_DEFAULT_BROWSER_URL", "https://www.bing.com/").strip() or "https://www.bing.com/"
            opened = webbrowser.open(start_url, new=2, autoraise=True)
            if not opened:
                raise RuntimeError("系统默认浏览器启动失败")
            return {"pid": None, "app_name": app_name, "resolved_app": "default_browser", "display_name": "默认浏览器", "url": start_url}

        system_alias = self._SYSTEM_APP_ALIASES.get(compact_app)
        if system_alias:
            command, display_name = system_alias
            # Alias table values are fixed executable names; no shell needed.
            process = self._popen([command], shell=False, visible=True)
            return {
                "pid": process.pid,
                "app_name": app_name,
                "resolved_app": command,
                "display_name": display_name,
                "matched_app": None,
                "launch_method": "system_command",
                "permission": "current_user",
            }

        alias = self._APP_ALIASES.get(compact_app)
        command = alias[0] if alias else normalized_app
        display_name = alias[1] if alias else normalized_app
        matched_app = self.find_installed_app(normalized_app)
        if matched_app and matched_app.get("exe_path") and os.path.isfile(str(matched_app["exe_path"])):
            exe_path = str(matched_app["exe_path"])
            process = self._popen([exe_path], shell=False)
            return {
                "pid": process.pid,
                "app_name": app_name,
                "resolved_app": exe_path,
                "display_name": matched_app.get("name") or display_name,
                "matched_app": matched_app,
                "launch_method": "installed_app_exe",
                "permission": "current_user",
            }

        if alias and shutil.which(command):
            command = shutil.which(command) or command

        # `command` may originate from user/model input; never hand it to a shell
        # (command injection). Launch as a single argv entry instead.
        try:
            process = self._popen([command], shell=False)
        except FileNotFoundError as exc:
            raise RuntimeError(f"未找到可执行程序: {command}") from exc
        return {
            "pid": process.pid,
            "app_name": app_name,
            "resolved_app": command,
            "display_name": display_name,
            "matched_app": matched_app,
            "launch_method": "shell_command",
            "permission": "current_user",
        }

    def close_app(self, app_name: str, force: bool = False):
        normalized_app = str(app_name or "").strip()
        if not normalized_app:
            raise ValueError("app_name 不能为空")

        compact_app = self._compact_app_name(normalized_app)
        alias = self._APP_ALIASES.get(compact_app) or self._APP_ALIASES.get(self._strip_generic_app_words(normalized_app))
        display_name = alias[1] if alias else normalized_app
        matched_app = self.find_installed_app(normalized_app)
        search_terms = self._build_app_search_terms(normalized_app)
        if matched_app:
            search_terms.extend(self._build_installed_app_terms(matched_app))
        if alias:
            search_terms.extend([alias[0], alias[1], *alias[2]])
        search_terms = self._dedupe_search_terms(search_terms)

        if platform.system().lower() != "windows":
            raise RuntimeError("当前 close_app 仅支持 Windows 本机进程关闭")

        result = self._close_windows_app(search_terms, force=force)
        return {
            "app_name": app_name,
            "display_name": matched_app.get("name") if matched_app else display_name,
            "matched_app": matched_app,
            "search_terms": search_terms[:12],
            "closed_count": len(result),
            "closed": result,
            "force": bool(force),
            "permission": "current_user",
        }

    def list_installed_apps(self, limit: int = 80, refresh: bool = False):
        if platform.system().lower() == "windows":
            if refresh or self._installed_apps_cache is None:
                scan_limit = max(int(limit), 300)
                self._installed_apps_cache = self._merge_app_records(
                    [
                        *self._list_windows_installed_apps(limit=scan_limit),
                        *self._list_windows_start_menu_apps(limit=scan_limit),
                    ],
                    limit=scan_limit,
                )
            return self._installed_apps_cache[: int(limit)]
        return []

    def find_installed_app(self, app_name: str, limit: int = 300):
        query_terms = self._build_app_search_terms(app_name)
        if not query_terms:
            return None

        best_match: dict[str, object] | None = None
        best_score = 0.0
        for app in self.list_installed_apps(limit=limit):
            score, matched_query, matched_term = self._score_app_match(app, query_terms)
            if score > best_score:
                best_score = score
                best_match = dict(app)
                best_match.update({"matched_query": matched_query, "matched_term": matched_term, "match_score": round(score, 3)})
        return best_match if best_match and best_score >= 0.45 else None

    def list_hardware_devices(self, limit: int = 80):
        if platform.system().lower() != "windows":
            return []

        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Get-PnpDevice | Select-Object -First {int(limit)} Class,FriendlyName,Status,InstanceId | ConvertTo-Json -Compress",
        ]
        completed = self._run_text(command, timeout=8)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or "硬件设备扫描失败").strip())
        output = (completed.stdout or "").strip()
        if not output:
            return []
        data = json.loads(output)
        if isinstance(data, dict):
            data = [data]
        return [self._normalize_hardware_record(item) for item in data[: int(limit)]]

    def open_url(self, url: str):
        target_url = self._normalize_url(url)
        opened = webbrowser.open(target_url, new=2, autoraise=True)
        if not opened:
            raise RuntimeError("默认浏览器打开 URL 失败")
        return {"url": target_url, "opened": True, "permission": "current_user"}

    def search_web(self, query: str, engine: str = "bing"):
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("query 不能为空")
        engine_key = str(engine or "bing").strip().lower()
        templates = {
            "bing": "https://www.bing.com/search?q={query}",
            "google": "https://www.google.com/search?q={query}",
            "baidu": "https://www.baidu.com/s?wd={query}",
        }
        template = templates.get(engine_key, templates["bing"])
        result = self.open_url(template.format(query=quote_plus(normalized_query)))
        return {**result, "query": normalized_query, "engine": engine_key if engine_key in templates else "bing"}

    def read_clipboard(self):
        if platform.system().lower() == "windows":
            completed = self._run_text(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"], timeout=5)
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or "读取剪贴板失败").strip())
            text = completed.stdout or ""
            return {"text": text.rstrip("\r\n"), "length": len(text.rstrip("\r\n")), "permission": "current_user"}
        root = self._tk_root()
        try:
            text = root.clipboard_get()
            return {"text": text, "length": len(text), "permission": "current_user"}
        finally:
            root.destroy()

    def write_clipboard(self, text: str):
        value = str(text or "")
        if platform.system().lower() == "windows":
            completed = self._run_text(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"], input=value, timeout=5)
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or "写入剪贴板失败").strip())
            return {"length": len(value), "permission": "current_user"}
        root = self._tk_root()
        try:
            root.clipboard_clear()
            root.clipboard_append(value)
            root.update()
            return {"length": len(value), "permission": "current_user"}
        finally:
            root.destroy()

    def capture_screen(self, output_path: str | None = None):
        screenshot = self._pyautogui().screenshot()
        path = Path(output_path or Path(tempfile.gettempdir()) / f"spiritkin_screen_{int(time.time())}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        screenshot.save(path)
        return {"path": str(path), "width": getattr(screenshot, "width", None), "height": getattr(screenshot, "height", None)}

    def capture_camera(self, output_path: str | None = None, camera_index: int = 0):
        try:
            import cv2
            cap = cv2.VideoCapture(camera_index)
            if not cap.isOpened():
                return {"error": f"无法打开摄像头 index={camera_index}，请检查设备连接和权限"}
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return {"error": f"摄像头 index={camera_index} 读取帧失败"}
            path = Path(output_path or Path(tempfile.gettempdir()) / f"spiritkin_camera_{int(time.time())}.jpg")
            path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(path), frame)
            h, w = frame.shape[:2]
            return {"path": str(path), "width": w, "height": h, "camera_index": camera_index, "format": "jpg"}
        except ImportError:
            return {"error": "opencv-python 未安装，无法使用摄像头"}
        except Exception as exc:
            return {"error": str(exc)}

    def list_windows(self, limit: int = 40):
        if platform.system().lower() != "windows":
            return []
        script = f"""
Get-Process | Where-Object {{ $_.MainWindowTitle }} |
Select-Object -First {int(limit)} Id,ProcessName,MainWindowTitle |
ConvertTo-Json -Compress
"""
        completed = self._run_text(["powershell", "-NoProfile", "-Command", script], timeout=6)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or "窗口列表读取失败").strip())
        output = (completed.stdout or "").strip()
        if not output:
            return []
        data = json.loads(output)
        if isinstance(data, dict):
            data = [data]
        return [self._normalize_window_record(item) for item in data[: int(limit)]]

    def activate_window(self, title: str):
        matches = self._run_window_action(title, action="activate", force=False)
        return {"title": title, "matched_count": len(matches), "windows": matches}

    def close_window(self, title: str, force: bool = False):
        matches = self._run_window_action(title, action="close", force=force)
        return {"title": title, "matched_count": len(matches), "windows": matches, "force": bool(force)}

    def resize_window(self, title: str, width: int, height: int):
        matches = self._resize_or_move_window(title, width=int(width), height=int(height))
        return {"title": title, "matched_count": len(matches), "width": int(width), "height": int(height), "windows": matches}

    def move_window(self, title: str, x: int, y: int):
        matches = self._resize_or_move_window(title, x=int(x), y=int(y))
        return {"title": title, "matched_count": len(matches), "x": int(x), "y": int(y), "windows": matches}

    def search_files(self, query: str, root: str | None = None, limit: int = 20):
        normalized_query = str(query or "").strip().lower()
        if not normalized_query:
            raise ValueError("query 不能为空")
        search_root = self._resolve_file_root(root)
        results = []
        for path in search_root.rglob("*"):
            try:
                if normalized_query not in path.name.lower():
                    continue
                results.append(
                    {
                        "name": path.name,
                        "path": str(path),
                        "is_dir": path.is_dir(),
                        "suffix": path.suffix,
                    }
                )
                if len(results) >= int(limit):
                    break
            except OSError:
                continue
        return {"query": query, "root": str(search_root), "matches": results}

    def read_file_text(self, path: str, max_chars: int = 4000):
        target = self._resolve_existing_path(path)
        if target.is_dir():
            raise RuntimeError("目标路径是目录，不能直接按文本文件读取")
        text = target.read_text(encoding="utf-8", errors="ignore")
        limited = text[: int(max_chars)]
        return {
            "path": str(target),
            "content": limited,
            "truncated": len(text) > len(limited),
            "length": len(limited),
        }

    def open_file(self, path: str):
        target = self._resolve_existing_path(path)
        if platform.system().lower() == "windows":
            os.startfile(str(target))
            return {"path": str(target), "opened": True}
        opened = webbrowser.open(target.as_uri(), new=2, autoraise=True)
        if not opened:
            raise RuntimeError("打开文件失败")
        return {"path": str(target), "opened": True}

    def write_file_text(self, path: str, text: str):
        target = self._resolve_write_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(text or ""), encoding="utf-8")
        return {"path": str(target), "length": len(str(text or "")), "operation": "write"}

    def save_text_as(self, path: str, text: str):
        return self.write_file_text(path, text)

    def send_notification(self, title: str, text: str):
        notification_title = str(title or "SpiritKin").strip()
        notification_text = str(text or "").strip()
        if platform.system().lower() == "windows":
            script = f"""
Add-Type -AssemblyName System.Windows.Forms
$balloon = New-Object System.Windows.Forms.NotifyIcon
$balloon.Icon = [System.Drawing.SystemIcons]::Information
$balloon.BalloonTipTitle = '{notification_title.replace("'", "''")}'
$balloon.BalloonTipText = '{notification_text.replace("'", "''")}'
$balloon.Visible = $True
$balloon.ShowBalloonTip(5000)
Start-Sleep -Milliseconds 200
$balloon.Dispose()
"""
            completed = self._run_text(["powershell", "-NoProfile", "-Command", script], timeout=8)
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or "发送通知失败").strip())
            return {"title": notification_title, "text": notification_text, "sent": True, "backend": "winforms_notify"}
        return {"title": notification_title, "text": notification_text, "sent": False, "backend": "unsupported_platform"}

    def list_browser_tabs(self):
        if platform.system().lower() != "windows":
            return {"tabs": [], "backend": "unsupported_platform", "note": "当前仅支持 Windows Edge/IE COM 接口"}
        script = """
try {
    $shell = New-Object -ComObject Shell.Application
    $tabs = @()
    foreach ($window in $shell.Windows()) {
        if ($window -and $window.LocationURL) {
            $tabs += [PSCustomObject]@{
                title = $window.LocationName
                url = $window.LocationURL
                hwnd = $window.HWND
            }
        }
    }
    $tabs | ConvertTo-Json -Compress
} catch {
    @() | ConvertTo-Json -Compress
}
"""
        completed = self._run_text(["powershell", "-NoProfile", "-Command", script], timeout=6)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or "枚举浏览器标签页失败").strip())
        output = (completed.stdout or "").strip()
        if not output:
            return {"tabs": [], "backend": "shell_com", "note": "未检测到 Edge/IE COM 标签页"}
        data = json.loads(output)
        if isinstance(data, dict):
            data = [data]
        return {"tabs": data if isinstance(data, list) else [], "backend": "shell_com", "count": len(data) if isinstance(data, list) else 0}

    def activate_browser_tab(self, title: str = "", index: int = -1):
        if platform.system().lower() != "windows":
            raise RuntimeError("当前浏览器标签页操作仅支持 Windows")
        target_title = str(title or "").strip()
        target_index = max(-1, int(index))
        title_json = json.dumps(target_title, ensure_ascii=False).replace("'", "''")
        script = f"""
$title = ConvertFrom-Json '{title_json}'
$index = {target_index}
try {{
    $shell = New-Object -ComObject Shell.Application
    $windows = $shell.Windows() | Where-Object {{ $_.LocationURL }}
    $matched = $null
    if ($index -ge 0) {{
        $matched = $windows | Select-Object -First 1 -Skip $index
    }} elseif ($title) {{
        $matched = $windows | Where-Object {{ $_.LocationName -like "*$title*" -or $_.LocationURL -like "*$title*" }} | Select-Object -First 1
    }}
    if ($matched) {{
        $matched.Visible = $true
        $matched.Document.Body.Focus() | Out-Null
        [PSCustomObject]@{{ title=$matched.LocationName; url=$matched.LocationURL; ok=$true }}
    }} else {{
        [PSCustomObject]@{{ title=$title; index=$index; ok=$false; error="not_found" }}
    }}
}} catch {{
    [PSCustomObject]@{{ title=$title; index=$index; ok=$false; error=$_.Exception.Message }}
}}
"""
        completed = self._run_text(["powershell", "-NoProfile", "-Command", script], timeout=6)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or "激活浏览器标签页失败").strip())
        data = json.loads(completed.stdout or "{}")
        if isinstance(data, dict):
            return {"title": data.get("title", target_title), "index": target_index, "ok": bool(data.get("ok")), "error": data.get("error", "")}
        return {"title": target_title, "index": target_index, "ok": False, "error": "unexpected_response"}

    def close_browser_tab(self, title: str = "", index: int = -1):
        if platform.system().lower() != "windows":
            raise RuntimeError("当前浏览器标签页操作仅支持 Windows")
        target_title = str(title or "").strip()
        target_index = max(-1, int(index))
        title_json = json.dumps(target_title, ensure_ascii=False).replace("'", "''")
        script = f"""
$title = ConvertFrom-Json '{title_json}'
$index = {target_index}
try {{
    $shell = New-Object -ComObject Shell.Application
    $windows = $shell.Windows() | Where-Object {{ $_.LocationURL }}
    $matched = $null
    if ($index -ge 0) {{
        $matched = $windows | Select-Object -First 1 -Skip $index
    }} elseif ($title) {{
        $matched = $windows | Where-Object {{ $_.LocationName -like "*$title*" -or $_.LocationURL -like "*$title*" }} | Select-Object -First 1
    }}
    if ($matched) {{
        $matched.Quit()
        [PSCustomObject]@{{ title=$matched.LocationName; url=$matched.LocationURL; ok=$true }}
    }} else {{
        [PSCustomObject]@{{ title=$title; index=$index; ok=$false; error="not_found" }}
    }}
}} catch {{
    [PSCustomObject]@{{ title=$title; index=$index; ok=$false; error=$_.Exception.Message }}
}}
"""
        completed = self._run_text(["powershell", "-NoProfile", "-Command", script], timeout=6)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or "关闭浏览器标签页失败").strip())
        data = json.loads(completed.stdout or "{}")
        if isinstance(data, dict):
            return {"title": data.get("title", target_title), "index": target_index, "ok": bool(data.get("ok")), "error": data.get("error", "")}
        return {"title": target_title, "index": target_index, "ok": False, "error": "unexpected_response"}

    @staticmethod
    def _list_windows_installed_apps(limit: int = 80):
        import winreg

        registry_roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        apps = []
        seen = set()
        for root, path in registry_roots:
            try:
                key = winreg.OpenKey(root, path)
            except OSError:
                continue
            with key:
                for index in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        subkey_name = winreg.EnumKey(key, index)
                        subkey = winreg.OpenKey(key, subkey_name)
                        with subkey:
                            name = str(winreg.QueryValueEx(subkey, "DisplayName")[0]).strip()
                            version = LocalPCDevice._read_registry_value(subkey, "DisplayVersion")
                            publisher = LocalPCDevice._read_registry_value(subkey, "Publisher")
                            install_location = LocalPCDevice._read_registry_value(subkey, "InstallLocation")
                            display_icon = LocalPCDevice._read_registry_value(subkey, "DisplayIcon")
                    except OSError:
                        continue
                    dedupe_key = name.lower()
                    if not name or dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    exe_path = LocalPCDevice._resolve_app_executable(name, install_location, display_icon)
                    apps.append(
                        {
                            "name": name,
                            "version": version,
                            "publisher": publisher,
                            "install_location": install_location,
                            "display_icon": display_icon,
                            "exe_path": exe_path,
                            "can_launch": bool(exe_path),
                        }
                    )
                    if len(apps) >= int(limit):
                        return apps
        return apps

    @staticmethod
    def _list_windows_start_menu_apps(limit: int = 300):
        script = rf"""
$paths = @(
  "$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
  "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
)
$shell = New-Object -ComObject WScript.Shell
Get-ChildItem -Path $paths -Recurse -Filter *.lnk -ErrorAction SilentlyContinue |
  Select-Object -First {int(limit)} |
  ForEach-Object {{
    try {{
      $shortcut = $shell.CreateShortcut($_.FullName)
      [PSCustomObject]@{{
        name = $_.BaseName
        version = ""
        publisher = ""
        install_location = Split-Path -Parent $shortcut.TargetPath
        display_icon = $shortcut.IconLocation
        exe_path = $shortcut.TargetPath
        shortcut_path = $_.FullName
        source = "start_menu"
        can_launch = ($shortcut.TargetPath -like "*.exe")
      }}
    }} catch {{}}
  }} | ConvertTo-Json -Compress
"""
        completed = LocalPCDevice._run_text(["powershell", "-NoProfile", "-Command", script], timeout=10)
        if completed.returncode != 0 or not (completed.stdout or "").strip():
            return []
        data = json.loads(completed.stdout)
        if isinstance(data, dict):
            data = [data]
        records = []
        for item in data:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            exe_path = LocalPCDevice._extract_executable_path(str(item.get("exe_path") or ""))
            records.append({**item, "exe_path": exe_path, "can_launch": bool(exe_path)})
        return records

    @staticmethod
    def _merge_app_records(records: list[dict[str, object]], limit: int = 300) -> list[dict[str, object]]:
        merged: dict[str, dict[str, object]] = {}
        for record in records:
            name = str(record.get("name") or "").strip()
            if not name:
                continue
            exe_path = str(record.get("exe_path") or "").strip()
            key = LocalPCDevice._compact_app_name(name) or exe_path.lower()
            target = merged.setdefault(key, {"name": name, "sources": []})
            for field, value in record.items():
                if value not in (None, "", []):
                    target.setdefault(field, value)
            source = str(record.get("source") or "registry")
            sources = target.setdefault("sources", [])
            if isinstance(sources, list) and source not in sources:
                sources.append(source)
            target["can_launch"] = bool(target.get("exe_path"))
            if len(merged) >= int(limit):
                break
        return list(merged.values())[: int(limit)]

    @staticmethod
    def _read_registry_value(key, name: str) -> str:
        try:
            return str(__import__("winreg").QueryValueEx(key, name)[0]).strip()
        except OSError:
            return ""

    @staticmethod
    def _normalize_url(url: str) -> str:
        raw_url = str(url or "").strip()
        if not raw_url:
            raise ValueError("url 不能为空")
        parsed = urlparse(raw_url)
        if not parsed.scheme:
            raw_url = "https://" + raw_url
        return raw_url

    @staticmethod
    def _tk_root():
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        return root

    @staticmethod
    def _normalize_window_record(item: dict[str, object]) -> dict[str, object]:
        return {
            "pid": item.get("Id") or item.get("pid"),
            "process_name": item.get("ProcessName") or item.get("process_name") or "",
            "title": item.get("MainWindowTitle") or item.get("title") or "",
        }

    @staticmethod
    def _resolve_file_root(root: str | None = None) -> Path:
        root_value = str(root or "").strip() or os.getenv("SPIRITKIN_FILE_SEARCH_ROOT") or os.getcwd()
        target = Path(root_value).expanduser().resolve()
        if not target.exists() or not target.is_dir():
            raise RuntimeError(f"文件搜索根目录不存在：{target}")
        return target

    @staticmethod
    def _resolve_existing_path(path: str) -> Path:
        raw = str(path or "").strip()
        if not raw:
            raise ValueError("path 不能为空")
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = (Path(os.getcwd()) / target).resolve()
        else:
            target = target.resolve()
        if not target.exists():
            raise RuntimeError(f"路径不存在：{target}")
        return target

    @staticmethod
    def _resolve_write_path(path: str) -> Path:
        raw = str(path or "").strip()
        if not raw:
            raise ValueError("path 不能为空")
        root = Path(os.getcwd()).resolve()
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
        allowed_roots = [root / "state", root / "data", root / "runs"]
        if not any(target == allowed or allowed in target.parents for allowed in allowed_roots):
            raise RuntimeError("写文件路径必须位于 state/、data/ 或 runs/ 目录内")
        return target

    @staticmethod
    def _compact_app_name(value: str) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())

    @staticmethod
    def _text_tokens(value: str) -> set[str]:
        return {token for token in re.split(r"[^0-9a-z]+", str(value or "").lower()) if len(token) >= 2}

    @staticmethod
    def _strip_generic_app_words(value: str) -> str:
        compact = LocalPCDevice._compact_app_name(value)
        for word in ("浏览器", "游览器", "留览器", "软件", "应用", "程序"):
            compact = compact.replace(word, "")
        return compact

    @classmethod
    def _build_app_search_terms(cls, app_name: str) -> list[str]:
        compact = cls._compact_app_name(app_name)
        stripped = cls._strip_generic_app_words(app_name)
        terms = [compact, stripped, str(app_name or "").lower().strip()]
        terms.extend(cls._expand_asr_confusion_terms(compact))
        terms.extend(cls._expand_asr_confusion_terms(stripped))
        alias = cls._APP_ALIASES.get(compact) or cls._APP_ALIASES.get(stripped)
        if alias:
            command, display_name, search_terms = alias
            terms.extend([command, display_name, *search_terms])
        return cls._dedupe_search_terms(terms)

    @classmethod
    def _dedupe_search_terms(cls, terms) -> list[str]:
        deduped = []
        for term in terms:
            compact_term = cls._strip_generic_app_words(str(term))
            if compact_term and compact_term not in deduped:
                deduped.append(compact_term)
        return deduped

    @staticmethod
    def _expand_asr_confusion_terms(term: str) -> list[str]:
        compact = LocalPCDevice._compact_app_name(term)
        if not compact:
            return []
        replacements = {
            "火爆": "火豹",
            "火暴": "火豹",
            "火包": "火豹",
        }
        expanded = []
        for source, target in replacements.items():
            if source in compact:
                expanded.append(compact.replace(source, target))
        return expanded

    @classmethod
    def _build_installed_app_terms(cls, app: dict[str, object]) -> set[str]:
        values = [app.get("name"), app.get("publisher"), app.get("display_icon"), app.get("exe_path"), app.get("shortcut_path")]
        terms = {cls._strip_generic_app_words(str(value or "")) for value in values}
        exe_path = str(app.get("exe_path") or "")
        if exe_path:
            terms.add(cls._strip_generic_app_words(Path(exe_path).stem))
        return {term for term in terms if term}

    @classmethod
    def _score_app_match(cls, app: dict[str, object], query_terms: list[str]) -> tuple[float, str, str]:
        app_terms = cls._build_installed_app_terms(app)
        app_raw = " ".join(str(app.get(key) or "") for key in ("name", "publisher", "exe_path", "shortcut_path")).lower()
        app_tokens = cls._text_tokens(app_raw)
        best = (0.0, "", "")
        ignored = {"浏览器", "游览器", "留览器", "软件", "应用", "程序", "app", "browser"}
        for query in query_terms:
            query = cls._strip_generic_app_words(query)
            if not query or query in ignored:
                continue
            query_tokens = cls._text_tokens(query)
            for term in app_terms:
                if not term:
                    continue
                score = 0.0
                if query == term:
                    score = 1.0
                elif len(query) >= 3 and query in term:
                    score = 0.94
                elif len(term) >= 3 and term in query:
                    score = 0.88
                else:
                    score = difflib.SequenceMatcher(None, query, term).ratio() * 0.82
                if query_tokens and query_tokens.issubset(app_tokens):
                    score = max(score, 0.92)
                if score > best[0]:
                    best = (score, query, term)
        return best

    @staticmethod
    def _close_windows_app(search_terms: list[str], force: bool = False):
        terms_json = json.dumps([term for term in search_terms if term], ensure_ascii=False).replace("'", "''")
        force_literal = "$true" if force else "$false"
        script = f"""
$terms = ConvertFrom-Json '{terms_json}'
$force = {force_literal}
$matches = Get-Process | Where-Object {{
  $processName = ($_.ProcessName | ForEach-Object {{ "$($_)" }}).ToLowerInvariant()
  $windowTitle = ($_.MainWindowTitle | ForEach-Object {{ "$($_)" }}).ToLowerInvariant()
  $matched = $false
  foreach ($term in $terms) {{
    $t = ("$term").ToLowerInvariant()
    if ($t -and ($processName -eq $t -or $processName -like "*$t*" -or $windowTitle -like "*$t*")) {{
      $matched = $true
      break
    }}
  }}
  $matched
}} | Select-Object -First 12
$results = @()
foreach ($process in $matches) {{
  $closed = $false
  try {{
    if ($process.MainWindowHandle -ne 0) {{ $closed = $process.CloseMainWindow() }}
    if ($force -and -not $process.HasExited) {{ Stop-Process -Id $process.Id -Force -ErrorAction Stop; $closed = $true }}
    $results += [PSCustomObject]@{{ pid=$process.Id; name=$process.ProcessName; title=$process.MainWindowTitle; closed=$closed }}
  }} catch {{
    $results += [PSCustomObject]@{{ pid=$process.Id; name=$process.ProcessName; title=$process.MainWindowTitle; closed=$false; error=$_.Exception.Message }}
  }}
}}
$results | ConvertTo-Json -Compress
"""
        completed = LocalPCDevice._run_text(["powershell", "-NoProfile", "-Command", script], timeout=8)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or "关闭应用失败").strip())
        output = (completed.stdout or "").strip()
        if not output:
            return []
        data = json.loads(output)
        if isinstance(data, dict):
            data = [data]
        return data if isinstance(data, list) else []

    @staticmethod
    def _run_window_action(title: str, action: str, force: bool = False):
        normalized_title = str(title or "").strip()
        if not normalized_title:
            raise ValueError("title 不能为空")
        if platform.system().lower() != "windows":
            raise RuntimeError("当前窗口操作仅支持 Windows")
        title_json = json.dumps(normalized_title, ensure_ascii=False).replace("'", "''")
        action_json = json.dumps(action).replace("'", "''")
        force_literal = "$true" if force else "$false"
        script = f"""
$title = ConvertFrom-Json '{title_json}'
$action = ConvertFrom-Json '{action_json}'
$force = {force_literal}
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {{ [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd); }}
"@
$matches = Get-Process | Where-Object {{ $_.MainWindowTitle -and $_.MainWindowTitle.ToLowerInvariant().Contains($title.ToLowerInvariant()) }} | Select-Object -First 8
$results = @()
foreach ($process in $matches) {{
  $ok = $false
  $errorMessage = ""
  try {{
    if ($action -eq "activate") {{ $ok = [Win32]::SetForegroundWindow($process.MainWindowHandle) }}
    elseif ($action -eq "close") {{
      if ($process.MainWindowHandle -ne 0) {{ $ok = $process.CloseMainWindow() }}
      if ($force -and -not $process.HasExited) {{ Stop-Process -Id $process.Id -Force -ErrorAction Stop; $ok = $true }}
    }}
  }} catch {{ $errorMessage = $_.Exception.Message }}
  $results += [PSCustomObject]@{{ pid=$process.Id; process_name=$process.ProcessName; title=$process.MainWindowTitle; ok=$ok; error=$errorMessage }}
}}
$results | ConvertTo-Json -Compress
"""
        completed = LocalPCDevice._run_text(["powershell", "-NoProfile", "-Command", script], timeout=8)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or "窗口操作失败").strip())
        output = (completed.stdout or "").strip()
        if not output:
            return []
        data = json.loads(output)
        if isinstance(data, dict):
            data = [data]
        return data if isinstance(data, list) else []

    @staticmethod
    def _resize_or_move_window(title: str, *, x: int | None = None, y: int | None = None, width: int | None = None, height: int | None = None):
        normalized_title = str(title or "").strip()
        if not normalized_title:
            raise ValueError("title 不能为空")
        if platform.system().lower() != "windows":
            raise RuntimeError("当前窗口大小/位置调整仅支持 Windows")
        title_json = json.dumps(normalized_title, ensure_ascii=False).replace("'", "''")
        x_literal = "[IntPtr]::Zero" if x is None else str(int(x))
        y_literal = "[IntPtr]::Zero" if y is None else str(int(y))
        w_literal = "[IntPtr]::Zero" if width is None else str(int(width))
        h_literal = "[IntPtr]::Zero" if height is None else str(int(height))
        script = f"""
$title = ConvertFrom-Json '{title_json}'
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Resize {{
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
    public struct RECT {{ public int Left; public int Top; public int Right; public int Bottom; }}
}}
"@
$matches = Get-Process | Where-Object {{ $_.MainWindowTitle -and $_.MainWindowTitle.ToLowerInvariant().Contains($title.ToLowerInvariant()) }} | Select-Object -First 8
$results = @()
foreach ($process in $matches) {{
  $ok = $false
  $errorMessage = ""
  try {{
    $rect = New-Object Win32Resize+RECT
    [Win32Resize]::GetWindowRect($process.MainWindowHandle, [ref]$rect) | Out-Null
    $newX = if ({x_literal} -eq [IntPtr]::Zero) {{ $rect.Left }} else {{ {x_literal} }}
    $newY = if ({y_literal} -eq [IntPtr]::Zero) {{ $rect.Top }} else {{ {y_literal} }}
    $newW = if ({w_literal} -eq [IntPtr]::Zero) {{ $rect.Right - $rect.Left }} else {{ {w_literal} }}
    $newH = if ({h_literal} -eq [IntPtr]::Zero) {{ $rect.Bottom - $rect.Top }} else {{ {h_literal} }}
    $ok = [Win32Resize]::MoveWindow($process.MainWindowHandle, $newX, $newY, $newW, $newH, $true)
  }} catch {{ $errorMessage = $_.Exception.Message }}
  $results += [PSCustomObject]@{{ pid=$process.Id; process_name=$process.ProcessName; title=$process.MainWindowTitle; ok=$ok; error=$errorMessage; x=$newX; y=$newY; width=$newW; height=$newH }}
}}
$results | ConvertTo-Json -Compress
"""
        completed = LocalPCDevice._run_text(["powershell", "-NoProfile", "-Command", script], timeout=8)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or "窗口调整失败").strip())
        output = (completed.stdout or "").strip()
        if not output:
            return []
        data = json.loads(output)
        if isinstance(data, dict):
            data = [data]
        return data if isinstance(data, list) else []

    @staticmethod
    def _normalize_hardware_record(item: dict[str, object]) -> dict[str, object]:
        hardware_class = str(item.get("Class") or item.get("class") or "").strip()
        name = str(item.get("FriendlyName") or item.get("Name") or item.get("name") or "").strip()
        capabilities = []
        class_lower = hardware_class.lower()
        name_lower = name.lower()
        if any(token in class_lower or token in name_lower for token in ("camera", "image", "摄像头")):
            capabilities.append("capture_video")
        if any(token in class_lower or token in name_lower for token in ("audio", "microphone", "麦克风")):
            capabilities.append("capture_audio")
        if any(token in class_lower or token in name_lower for token in ("keyboard", "键盘")):
            capabilities.append("input_keyboard")
        if any(token in class_lower or token in name_lower for token in ("mouse", "pointing", "鼠标")):
            capabilities.append("input_pointer")
        return {**item, "name": name, "class": hardware_class, "capabilities": capabilities}

    @staticmethod
    def _extract_executable_path(value: str) -> str:
        raw = os.path.expandvars(str(value or "").strip())
        if not raw:
            return ""
        quoted = re.match(r'^"(?P<path>[^"]+\.exe)"', raw, flags=re.IGNORECASE)
        if quoted:
            return quoted.group("path")
        exe_match = re.search(r"(?P<path>[A-Za-z]:\\[^,;]+?\.exe)", raw, flags=re.IGNORECASE)
        if exe_match:
            return exe_match.group("path").strip().strip('"')
        return raw if raw.lower().endswith(".exe") else ""

    @staticmethod
    def _find_executable_in_install_location(app_name: str, install_location: str) -> str:
        location = Path(os.path.expandvars(str(install_location or "").strip().strip('"')))
        if not location.is_dir():
            return ""
        preferred = LocalPCDevice._build_app_search_terms(app_name)
        ignored_tokens = ("uninstall", "setup", "update", "crash", "helper")
        try:
            executables = list(location.glob("*.exe"))
        except OSError:
            return ""
        for executable in executables:
            stem = LocalPCDevice._strip_generic_app_words(executable.stem)
            if any(token in stem for token in ignored_tokens):
                continue
            if any(term and (term in stem or stem in term) for term in preferred):
                return str(executable)
        for executable in executables:
            stem = executable.stem.lower()
            if not any(token in stem for token in ignored_tokens):
                return str(executable)
        return ""

    @staticmethod
    def _resolve_app_executable(app_name: str, install_location: str, display_icon: str) -> str:
        icon_path = LocalPCDevice._extract_executable_path(display_icon)
        if icon_path and os.path.isfile(icon_path):
            return icon_path
        install_exe = LocalPCDevice._find_executable_in_install_location(app_name, install_location)
        if install_exe and os.path.isfile(install_exe):
            return install_exe
        return ""

    def extract_text(self, region=None, lang: str = "chi_sim+eng") -> str:
        from backend.perception.screen_io import extract_text_from_screen

        return extract_text_from_screen(region=region, lang=lang)

    def understand_screen(self, query: str, region=None) -> str:
        from backend.perception.screen_io import understand_screen_with_qwen

        return understand_screen_with_qwen(query, region=region)
