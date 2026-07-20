from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DeviceBackend(Protocol):
    name: str

    def get_screen_size(self):
        raise NotImplementedError

    def move_to(self, x: int, y: int):
        raise NotImplementedError

    def click(self, x: int, y: int):
        raise NotImplementedError

    def double_click(self, x: int, y: int):
        raise NotImplementedError

    def extract_text(self, region=None, lang: str = "chi_sim+eng") -> str:
        raise NotImplementedError

    def understand_screen(self, query: str, region=None) -> str:
        raise NotImplementedError

    def capture_screen(self, output_path: str | None = None):
        raise NotImplementedError

    def read_clipboard(self):
        raise NotImplementedError

    def write_clipboard(self, text: str):
        raise NotImplementedError

    def open_url(self, url: str):
        raise NotImplementedError

    def search_web(self, query: str, engine: str = "bing"):
        raise NotImplementedError

    def list_windows(self, limit: int = 40):
        raise NotImplementedError

    def activate_window(self, title: str):
        raise NotImplementedError

    def close_window(self, title: str, force: bool = False):
        raise NotImplementedError

    def search_files(self, query: str, root: str | None = None, limit: int = 20):
        raise NotImplementedError

    def read_file_text(self, path: str, max_chars: int = 4000):
        raise NotImplementedError

    def open_file(self, path: str):
        raise NotImplementedError

    def type_text(self, text: str):
        raise NotImplementedError

    def press_key(self, key: str):
        raise NotImplementedError

    def hotkey(self, *keys: str):
        raise NotImplementedError

    def launch_app(self, app_name: str):
        raise NotImplementedError

    def close_app(self, app_name: str, force: bool = False):
        raise NotImplementedError

    def list_installed_apps(self, limit: int = 80):
        raise NotImplementedError

    def list_hardware_devices(self, limit: int = 80):
        raise NotImplementedError
