from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VoiceProfile:
    voice_id: str
    display_name: str
    speech_provider: str
    speech_model: str
    reference_audio: Path
    reference_text: str
    language: str
    allowed_uses: tuple[str, ...]
    reference_sha256: str
    provenance_record: Path | None
    status: str
    source_path: Path

    @property
    def permits_assistant_speech(self) -> bool:
        return "assistant_speech" in self.allowed_uses or "assistant_speech_local" in self.allowed_uses


def load_voice_profile(profile_path: str | Path, *, project_root: str | Path | None = None) -> VoiceProfile:
    source_path = Path(profile_path).expanduser()
    root = Path(project_root).expanduser() if project_root is not None else Path.cwd()
    if not source_path.is_absolute():
        source_path = root / source_path
    source_path = source_path.resolve()
    payload = json.loads(source_path.read_text(encoding="utf-8"))

    reference_audio = _resolve_profile_path(payload.get("reference_audio"), root=root, profile_path=source_path)
    provenance_record = _resolve_optional_profile_path(
        payload.get("provenance_record") or payload.get("consent_record"),
        root=root,
        profile_path=source_path,
    )
    return VoiceProfile(
        voice_id=_required_string(payload, "voice_id"),
        display_name=_required_string(payload, "display_name"),
        speech_provider=_required_string(payload, "speech_provider").lower(),
        speech_model=_required_string(payload, "speech_model"),
        reference_audio=reference_audio,
        reference_text=_required_string(payload, "reference_text"),
        language=str(payload.get("language") or "zh-CN").strip() or "zh-CN",
        allowed_uses=tuple(str(item).strip() for item in payload.get("allowed_uses", []) if str(item).strip()),
        reference_sha256=str(payload.get("reference_sha256") or "").strip().upper(),
        provenance_record=provenance_record,
        status=str(payload.get("status") or "selected").strip().lower(),
        source_path=source_path,
    )


def validate_voice_profile(profile: VoiceProfile, *, required_use: str = "assistant_speech") -> tuple[bool, str]:
    if not profile.reference_audio.is_file():
        return False, "reference_audio_missing"
    if required_use == "assistant_speech" and not profile.permits_assistant_speech:
        return False, "assistant_speech_not_allowed"
    if profile.provenance_record is None or not profile.provenance_record.is_file():
        return False, "provenance_record_missing"
    if profile.reference_sha256:
        digest = hashlib.sha256(profile.reference_audio.read_bytes()).hexdigest().upper()
        if digest != profile.reference_sha256:
            return False, "reference_hash_mismatch"
    return True, "ready"


def voice_profile_summary(profile: VoiceProfile) -> dict[str, Any]:
    valid, reason = validate_voice_profile(profile)
    return {
        "voice_id": profile.voice_id,
        "display_name": profile.display_name,
        "speech_provider": profile.speech_provider,
        "speech_model": profile.speech_model,
        "language": profile.language,
        "allowed_uses": list(profile.allowed_uses),
        "status": profile.status,
        "valid": valid,
        "validation_reason": reason,
        "reference_sha256": profile.reference_sha256,
    }


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"voice profile missing {key}")
    return value


def _resolve_profile_path(value: Any, *, root: Path, profile_path: Path) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("voice profile missing reference_audio")
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    root_candidate = (root / path).resolve()
    if root_candidate.exists():
        return root_candidate
    return (profile_path.parent / path).resolve()


def _resolve_optional_profile_path(value: Any, *, root: Path, profile_path: Path) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    root_candidate = (root / path).resolve()
    if root_candidate.exists():
        return root_candidate
    return (profile_path.parent / path).resolve()
