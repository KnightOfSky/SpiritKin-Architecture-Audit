from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.command_gateway import build_avatar_asset_import_response
from backend.expression.avatar_assets import import_avatar3d_asset, import_live2d_asset


class AvatarAssetTests(unittest.TestCase):
    def test_import_live2d_asset_copies_model_and_updates_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "avatar.moc3").write_text("moc", encoding="utf-8")
            (source / "texture.png").write_text("png", encoding="utf-8")
            (source / "avatar.model3.json").write_text(
                json.dumps({"FileReferences": {"Moc": "avatar.moc3", "Textures": ["texture.png"]}}),
                encoding="utf-8",
            )
            frontend = root / "frontend"

            result = import_live2d_asset(source, role="Hero", frontend_dir=frontend)
            manifest = json.loads((frontend / "models" / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(result.asset_type, "live2d")
        self.assertEqual(result.role, "hero")
        self.assertEqual(manifest["defaultRole"], "hero")
        self.assertEqual(manifest["roles"]["hero"]["model"], "models/hero/avatar.model3.json")

    def test_import_avatar3d_asset_writes_role_and_default_manifest(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "hero.glb").write_bytes(b"glb")
            frontend = root / "frontend"

            result = import_avatar3d_asset(source, role="Hero3D", frontend_dir=frontend)
            manifest = json.loads((frontend / "models" / "hero3d" / "manifest.json").read_text(encoding="utf-8"))
            default_manifest = json.loads((frontend / "models" / "spirit3d" / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(result.asset_type, "avatar3d")
        self.assertEqual(result.role, "hero3d")
        self.assertEqual(manifest["model"], "models/hero3d/hero.glb")
        self.assertEqual(default_manifest["model"], "models/hero3d/hero.glb")

    def test_command_gateway_import_avatar_asset_rejects_missing_type(self):
        status, payload = build_avatar_asset_import_response({"source_path": "missing"})

        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
