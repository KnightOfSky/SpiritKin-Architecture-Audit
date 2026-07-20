import unittest

from scripts import smoke_asr


class SmokeAsrHelperTests(unittest.TestCase):
    def test_hotword_only_matches_case_and_punctuation(self):
        self.assertTrue(smoke_asr._is_hotword_only(" Spirit。", "Spirit"))
        self.assertTrue(smoke_asr._is_hotword_only("spirit", "Spirit"))

    def test_hotword_with_command_is_not_consumed(self):
        self.assertFalse(smoke_asr._is_hotword_only("Spirit 打开浏览器", "Spirit"))
        self.assertFalse(smoke_asr._is_hotword_only("打开浏览器", "Spirit"))

    def test_strip_hotword_prefix_keeps_actual_command(self):
        text, stripped = smoke_asr._strip_hotword_prefix("SPIRIT 打开浏览器。", "Spirit")

        self.assertTrue(stripped)
        self.assertEqual(text, "打开浏览器。")

    def test_strip_hotword_prefix_leaves_plain_command_unchanged(self):
        text, stripped = smoke_asr._strip_hotword_prefix("打开浏览器", "Spirit")

        self.assertFalse(stripped)
        self.assertEqual(text, "打开浏览器")


if __name__ == "__main__":
    unittest.main()

