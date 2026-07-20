from __future__ import annotations

from backend.devices.android_device import AndroidDeviceBackend
from backend.devices.local_pc import LocalPCDevice

_DEVICE_FACTORIES = {
    "local_pc": LocalPCDevice,
    "android_device": AndroidDeviceBackend,
}


def register_device_backend(name: str, factory):
    _DEVICE_FACTORIES[name] = factory


def get_device_backend(name: str = "local_pc"):
    factory = _DEVICE_FACTORIES.get(name, LocalPCDevice)
    return factory()