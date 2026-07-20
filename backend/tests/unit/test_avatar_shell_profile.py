import unittest

from backend.expression.shell_profile import build_avatar_shell_profile, build_multi_end_avatar_manifest


class AvatarShellProfileTests(unittest.TestCase):
    def test_desktop_profile_uses_transparent_webview_window(self):
        profile = build_avatar_shell_profile("desktop")

        self.assertEqual(profile.shell_type, "desktop_webview")
        self.assertTrue(profile.window["transparent"])
        self.assertTrue(profile.capabilities["always_on_top"])
        self.assertIn("spirit_avatar.html", profile.avatar_url)
        self.assertIn("avatar_3d.html", profile.avatar_3d_url)
        self.assertIn("config=models/spirit3d/manifest.json", profile.avatar_3d_url)
        self.assertIn("live2d.html", profile.live2d_url)

    def test_mobile_profiles_encode_command_and_ws_urls(self):
        profile = build_avatar_shell_profile(
            "android",
            frontend_base_url="https://panel.example.com",
            events_ws_url="wss://events.example.com/ws",
            command_url="https://api.example.com/command",
        )

        self.assertEqual(profile.shell_type, "android_webview")
        self.assertTrue(profile.capabilities["mobile_safe_area"])
        self.assertTrue(profile.capabilities["avatar3d_web"])
        self.assertIn("mobile=1", profile.avatar_url)
        self.assertIn("wss%3A%2F%2Fevents.example.com%2Fws", profile.avatar_url)
        self.assertIn("cmd=https%3A%2F%2Fapi.example.com%2Fcommand", profile.avatar_url)
        self.assertIn("avatar_3d.html", profile.avatar_3d_url)
        self.assertIn("mobile=1", profile.avatar_3d_url)

    def test_multi_end_avatar_manifest_contains_desktop_android_ios(self):
        manifest = build_multi_end_avatar_manifest()

        self.assertEqual(manifest["schema_version"], "v1")
        self.assertEqual(set(manifest["profiles"].keys()), {"desktop", "android", "ios"})
        self.assertEqual(manifest["profiles"]["desktop"]["platform"], "desktop")
        self.assertIn("avatar_3d_url", manifest["profiles"]["desktop"])


if __name__ == "__main__":
    unittest.main()
