"""兼容旧路径，实际实现位于 backend.perception.audio.hotword。"""

from backend.perception.audio.hotword import detect_hotword, get_wake_model

__all__ = ["detect_hotword", "get_wake_model"]