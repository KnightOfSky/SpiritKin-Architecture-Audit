from __future__ import annotations

from backend.devices.registry import get_device_backend


def move_pointer(x: int, y: int, device_name: str = "local_pc"):
    return get_device_backend(device_name).move_to(x, y)


def click_pointer(x: int, y: int, device_name: str = "local_pc", double: bool = False):
    backend = get_device_backend(device_name)
    return backend.double_click(x, y) if double else backend.click(x, y)


def enter_text(text: str, device_name: str = "local_pc"):
    return get_device_backend(device_name).type_text(text)


def press_keys(*keys: str, device_name: str = "local_pc"):
    backend = get_device_backend(device_name)
    if len(keys) <= 1:
        return backend.press_key(keys[0]) if keys else None
    return backend.hotkey(*keys)


def launch_app(app_name: str, device_name: str = "local_pc"):
    return get_device_backend(device_name).launch_app(app_name)


def read_screen_text(device_name: str = "local_pc", region=None, lang: str = "chi_sim+eng") -> str:
    return get_device_backend(device_name).extract_text(region=region, lang=lang)


def understand_screen(query: str, device_name: str = "local_pc", region=None) -> str:
    return get_device_backend(device_name).understand_screen(query=query, region=region)