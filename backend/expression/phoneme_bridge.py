from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

PHONEME_MAP: dict[str, str] = {
    "a": "open",
    "o": "round",
    "e": "mid",
    "i": "wide",
    "u": "round_close",
    "v": "round_close",
    "ai": "wide_open",
    "ei": "wide",
    "ao": "open_round",
    "ou": "round_close",
    "an": "open",
    "en": "mid",
    "ang": "open",
    "eng": "mid",
    "b": "closed",
    "p": "closed",
    "m": "closed",
    "f": "closed_dental",
}


@dataclass
class PhonemeEvent:
    phoneme: str
    mouth_shape: str
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 150.0


class PhonemeEventEmitter:
    def __init__(self, on_event: callable | None = None):
        self._on_event = on_event
        self._active = False

    def emit(self, phoneme: str, mouth_shape: str = "", duration_ms: float = 150.0) -> None:
        if not self._active or self._on_event is None:
            return
        shape = mouth_shape or PHONEME_MAP.get(phoneme.lower().strip(), "mid")
        event = PhonemeEvent(phoneme=phoneme, mouth_shape=shape, duration_ms=duration_ms)
        self._on_event(event)

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False


def text_to_phoneme_events(text: str, chars_per_phoneme: float = 0.15) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cleaned = re.sub(r"[^一-鿿㐀-䶿a-zA-Z]", "", text or "")
    for i, char in enumerate(cleaned):
        pinyin = _char_to_pinyin(char)
        shape = PHONEME_MAP.get(pinyin, "mid")
        events.append({
            "char": char,
            "phoneme": pinyin,
            "mouth_shape": shape,
            "timestamp_ms": int(i * chars_per_phoneme * 1000),
            "duration_ms": 150,
        })
    return events


def _char_to_pinyin(char: str) -> str:
    try:
        import pypinyin
        py = pypinyin.pinyin(char, style=pypinyin.Style.TONE3)
        if py and py[0]:
            raw = py[0][0]
            return re.sub(r"[0-9]", "", raw).strip().lower()
    except ImportError:
        pass
    return "a"
