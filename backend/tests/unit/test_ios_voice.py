from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.mobile.ios_voice import resolve_ios_voice_preview


class IOSVoicePreviewTests(unittest.TestCase):
    def test_preview_stays_inside_validated_managed_profile(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile_dir = root / "state" / "voice-profiles" / "fairy.v1"
            profile_dir.mkdir(parents=True)
            (profile_dir / "reference.wav").write_bytes(b"reference")
            (profile_dir / "preview-v1.wav").write_bytes(b"preview")
            (profile_dir / "provenance.json").write_text("{}", encoding="utf-8")
            profile_path = profile_dir / "profile.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "voice_id": "fairy.v1",
                        "display_name": "Fairy",
                        "speech_provider": "cosyvoice",
                        "speech_model": "CosyVoice3",
                        "reference_audio": "reference.wav",
                        "reference_text": "test",
                        "allowed_uses": ["assistant_speech_local"],
                        "provenance_record": "provenance.json",
                    }
                ),
                encoding="utf-8",
            )

            preview, display_name = resolve_ios_voice_preview(project_root=root, profile_path=profile_path)

        self.assertEqual(preview.name, "preview-v1.wav")
        self.assertEqual(display_name, "Fairy")

    def test_profile_outside_managed_root_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = root / "outside" / "profile.json"
            outside.parent.mkdir(parents=True)
            outside.write_text("{}", encoding="utf-8")

            with self.assertRaises(PermissionError):
                resolve_ios_voice_preview(project_root=root, profile_path=outside)


if __name__ == "__main__":
    unittest.main()
