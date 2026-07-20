import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.validate_live2d_manifest import validate_manifest


class Live2DManifestTests(unittest.TestCase):
    def test_default_disabled_manifest_is_valid_without_warnings(self):
        report = validate_manifest("frontend/models/manifest.json")

        self.assertTrue(report["ok"])
        self.assertIn("spirit", report["roles"])
        self.assertEqual(report["warnings"], [])

    def test_disabled_role_skips_missing_model(self):
        with TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "frontend" / "models" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"roles": {"spirit": {"enabled": False, "ready": False, "model": "models/spirit/spirit.model3.json"}}}), encoding="utf-8")

            report = validate_manifest(manifest)

        self.assertTrue(report["ok"])
        self.assertEqual(report["warnings"], [])

    def test_ready_role_requires_model_file(self):
        with TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / "frontend" / "models" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"roles": {"spirit": {"ready": True, "model": "models/spirit/spirit.model3.json"}}}), encoding="utf-8")

            report = validate_manifest(manifest)

        self.assertFalse(report["ok"])
        self.assertIn("model file not found", report["errors"][0])

    def test_ready_role_accepts_local_model3_resources(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "frontend"
            model_dir = root / "models" / "spirit"
            model_dir.mkdir(parents=True)
            (model_dir / "spirit.moc3").write_text("moc", encoding="utf-8")
            (model_dir / "texture_00.png").write_text("png", encoding="utf-8")
            (model_dir / "spirit.model3.json").write_text(
                json.dumps({"FileReferences": {"Moc": "spirit.moc3", "Textures": ["texture_00.png"], "Expressions": [{"Name": "Happy"}], "Motions": {"Idle": []}}}),
                encoding="utf-8",
            )
            manifest = root / "models" / "manifest.json"
            manifest.write_text(json.dumps({"roles": {"spirit": {"ready": True, "model": "models/spirit/spirit.model3.json", "expressions": {"happy": "Happy"}, "motions": {"idle": "Idle"}}}}), encoding="utf-8")

            report = validate_manifest(manifest)

        self.assertTrue(report["ok"])
        self.assertEqual(report["warnings"], [])


if __name__ == "__main__":
    unittest.main()
