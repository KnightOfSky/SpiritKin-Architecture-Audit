import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.prepare_avatar3d_model import (
    BLENDER_PIPELINE,
    DEFAULT_SOURCE,
    build_blender_command,
    build_manifest,
    find_blender,
    frontend_url_for,
)


class Avatar3DPreparePipelineTests(unittest.TestCase):
    def test_find_blender_prefers_explicit_path(self):
        self.assertEqual(find_blender("C:/Tools/Blender/blender.exe"), "C:/Tools/Blender/blender.exe")

    def test_find_blender_uses_env_var(self):
        with patch.dict(os.environ, {"BLENDER_EXE": "D:/Blender/blender.exe"}, clear=False):
            self.assertEqual(find_blender(), "D:/Blender/blender.exe")

    def test_find_blender_uses_path_lookup(self):
        with patch.dict(os.environ, {}, clear=True), patch("scripts.prepare_avatar3d_model.shutil.which", return_value="blender.exe"), patch("scripts.prepare_avatar3d_model.Path.exists", return_value=False):
            self.assertEqual(find_blender(), "blender.exe")

    def test_build_manifest_defaults_to_safe_pose_only_bindings(self):
        manifest = build_manifest("models/spirit3d/SpiritKinAI.rigged.glb")

        self.assertEqual(manifest["format"], "glb")
        self.assertEqual(manifest["model"], "models/spirit3d/SpiritKinAI.rigged.glb")
        self.assertIn("happy", manifest["expressions"])
        self.assertIn("aa", manifest["visemes"])
        self.assertEqual(manifest["expressions"]["happy"]["keywords"], [])
        self.assertEqual(manifest["visemes"]["aa"]["keywords"], [])
        self.assertEqual(manifest["pipeline"]["expressions"], "disabled_by_default")
        self.assertTrue(manifest["pipeline"]["requires_manual_polish"])

    def test_build_manifest_can_enable_morph_bindings_explicitly(self):
        manifest = build_manifest("models/spirit3d/SpiritKinAI.rigged.glb", include_morph_bindings=True)

        self.assertIn("smile", manifest["expressions"]["happy"]["keywords"])
        self.assertIn("aa", manifest["visemes"]["aa"]["keywords"])
        self.assertEqual(manifest["pipeline"]["expressions"], "heuristic_shape_keys")

    def test_frontend_url_for_model_path(self):
        self.assertEqual(frontend_url_for(DEFAULT_SOURCE), "models/spirit3d/SpiritKinAI.fbx")

    def test_build_blender_command_passes_pipeline_arguments(self):
        command = build_blender_command(
            "blender",
            source=Path("input.fbx"),
            output=Path("output.glb"),
            report=Path("report.json"),
            target_height=1.7,
            front_axis="-Y",
        )

        self.assertEqual(command[:3], ["blender", "--background", "--python"])
        self.assertEqual(command[3], str(BLENDER_PIPELINE))
        self.assertIn("--source", command)
        self.assertIn("input.fbx", command)
        self.assertIn("--output", command)
        self.assertIn("output.glb", command)
        self.assertIn("--front-axis=-Y", command)


if __name__ == "__main__":
    unittest.main()
