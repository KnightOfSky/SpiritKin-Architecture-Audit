from __future__ import annotations

import os
from pathlib import Path

from backend.app.settings import resolve_tts_settings
from backend.expression.voice_profiles import load_voice_profile, validate_voice_profile

DEFAULT_VOICE_PROFILE_ROOT = "state/voice-profiles"


def resolve_ios_voice_preview(
    *,
    project_root: str | os.PathLike[str] | None = None,
    profile_path: str | os.PathLike[str] | None = None,
) -> tuple[Path, str]:
    root = Path(project_root or Path.cwd()).resolve()
    profile_root = (root / DEFAULT_VOICE_PROFILE_ROOT).resolve()
    configured_value = profile_path if profile_path is not None else resolve_tts_settings().voice_profile_path
    if not str(configured_value or "").strip():
        raise FileNotFoundError("no selected voice profile")
    configured = Path(configured_value).expanduser()
    if not configured.is_absolute():
        configured = root / configured
    configured = configured.resolve()
    if not configured.is_relative_to(profile_root):
        raise PermissionError("voice profile must stay inside the managed profile root")

    profile = load_voice_profile(configured, project_root=root)
    valid, reason = validate_voice_profile(profile)
    if not valid:
        raise ValueError(f"voice profile is not eligible for preview: {reason}")

    candidates = (configured.parent / "preview-v1.wav", profile.reference_audio)
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_relative_to(profile_root) and resolved.is_file() and resolved.suffix.lower() == ".wav":
            return resolved, profile.display_name
    raise FileNotFoundError("voice preview audio is missing")
