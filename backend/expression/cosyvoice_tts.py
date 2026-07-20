from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from backend.expression.edge_tts import clean_speech_text
from backend.expression.voice_profiles import VoiceProfile, load_voice_profile, validate_voice_profile


class CosyVoiceProvider:
    """Loopback-only client for the isolated SpiritKin CosyVoice service."""

    def __init__(
        self,
        *,
        base_url: str,
        profile_path: str | Path,
        timeout_seconds: float = 30.0,
        project_root: str | Path | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.profile: VoiceProfile = load_voice_profile(profile_path, project_root=project_root)
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._stop_event = threading.Event()

    def is_available(self) -> bool:
        valid, _reason = validate_voice_profile(self.profile)
        if not valid or not _is_loopback_url(self.base_url):
            return False
        try:
            request = Request(f"{self.base_url}/health", headers={"Accept": "application/json"})
            with urlopen(request, timeout=min(self.timeout_seconds, 1.5)) as response:  # noqa: S310 - loopback enforced
                return 200 <= int(getattr(response, "status", 0)) < 300
        except Exception:
            return False

    def speak_to_file(self, text: str, output_path: str | Path) -> bool:
        cleaned = clean_speech_text(text)
        if not cleaned or self._stop_event.is_set() or not _is_loopback_url(self.base_url):
            return False
        payload = {
            "text": cleaned,
            "voice_id": self.profile.voice_id,
            "language": self.profile.language,
            "model": self.profile.speech_model,
            "reference_audio": str(self.profile.reference_audio),
            "reference_text": self.profile.reference_text,
            "format": "wav",
        }
        request = Request(
            f"{self.base_url}/v1/synthesize",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Accept": "audio/wav", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - loopback enforced
                audio = response.read()
            if len(audio) < 44 or not audio.startswith(b"RIFF"):
                return False
            Path(output_path).write_bytes(audio)
            return True
        except Exception:
            return False

    def speak_and_play(self, text: str, on_segment_start: Callable[[str, float], None] | None = None) -> bool:
        self._stop_event.clear()
        output = Path(tempfile.gettempdir()) / f"spiritkin_cosyvoice_{os.getpid()}_{threading.get_ident()}.wav"
        if not self.speak_to_file(text, output):
            return False
        try:
            if on_segment_start is not None:
                on_segment_start(clean_speech_text(text), 0.0)
            if os.name == "nt":
                import winsound

                winsound.PlaySound(str(output), winsound.SND_FILENAME)
                return not self._stop_event.is_set()
            result = subprocess.run(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(output)], check=False)
            return result.returncode == 0 and not self._stop_event.is_set()
        except Exception:
            return False
        finally:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass

    def stop(self) -> None:
        self._stop_event.set()
        if os.name == "nt":
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass


def _is_loopback_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower() in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
