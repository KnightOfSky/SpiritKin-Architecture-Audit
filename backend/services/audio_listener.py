"""兼容旧路径，实际实现位于 backend.perception.audio.listener。"""

from backend.perception.audio.listener import calibrate_microphone, get_whisper_model, listen_from_microphone

__all__ = ["calibrate_microphone", "get_whisper_model", "listen_from_microphone"]