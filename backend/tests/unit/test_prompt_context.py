from __future__ import annotations

import unittest

from backend.orchestrator.prompt_context import (
    build_attachment_context,
    build_goal_context,
    build_plan_mode_steps,
    extract_backend_web_search_query,
    format_inventory_hardware,
    format_inventory_software,
    format_plan_mode_text,
    goal_metadata,
    looks_like_action_request,
    looks_like_backend_web_search,
    web_search_requested,
)


class PromptContextTests(unittest.TestCase):
    def test_plan_mode_steps_have_four_pending_stages(self):
        steps = build_plan_mode_steps("部署上线")
        self.assertEqual([s["index"] for s in steps], [1, 2, 3, 4])
        self.assertTrue(all(s["status"] == "pending" for s in steps))
        self.assertIn("部署上线", steps[0]["detail"])

    def test_plan_mode_steps_fall_back_to_placeholder_target(self):
        steps = build_plan_mode_steps("   ")
        self.assertIn("当前请求", steps[0]["detail"])

    def test_format_plan_mode_text_numbers_each_step(self):
        text = format_plan_mode_text(build_plan_mode_steps("x"))
        self.assertTrue(text.startswith("计划如下："))
        self.assertIn("1. 确认目标", text)
        self.assertIn("4. 执行前确认", text)

    def test_goal_metadata_reads_either_key(self):
        self.assertEqual(goal_metadata({"goal_text": "写代码"})["text"], "写代码")
        self.assertEqual(goal_metadata({"active_goal": "写代码"})["text"], "写代码")
        self.assertEqual(goal_metadata(None)["text"], "")

    def test_build_goal_context_empty_when_no_goal(self):
        self.assertEqual(build_goal_context({}), "")
        ctx = build_goal_context({"goal_text": "上线", "project_title": "SpiritKin"})
        self.assertIn("持续目标：上线", ctx)
        self.assertIn("项目：SpiritKin", ctx)

    def test_build_attachment_context_skips_empty_previews(self):
        self.assertEqual(build_attachment_context({}), "")
        self.assertEqual(build_attachment_context({"attachment_documents": [{"path": "a.txt"}]}), "")
        out = build_attachment_context({"attachment_documents": [{"path": "a.txt", "text_preview": "hello"}]})
        self.assertIn("a.txt", out)
        self.assertIn("hello", out)

    def test_looks_like_action_request_keyword_and_visual_combo(self):
        self.assertTrue(looks_like_action_request("帮我打开浏览器"))
        self.assertTrue(looks_like_action_request("看一下屏幕"))
        self.assertFalse(looks_like_action_request("你好呀"))
        self.assertFalse(looks_like_action_request("看看天气"))

    def test_format_inventory_software_marks_launchable(self):
        items = [
            {"name": "VSCode", "can_launch": True},
            {"display_name": "Blender"},
            {"name": ""},
            "not-a-dict",
        ]
        self.assertEqual(format_inventory_software(items), ["VSCode(可启动)", "Blender(已发现)"])
        self.assertEqual(format_inventory_software(None), [])

    def test_format_inventory_hardware_prefers_friendly_name(self):
        items = [
            {"FriendlyName": "USB Camera", "name": "cam0"},
            {"name": "GPU"},
            {"Class": "AudioEndpoint"},
            {},
        ]
        self.assertEqual(format_inventory_hardware(items), ["USB Camera", "GPU", "AudioEndpoint"])
        self.assertEqual(format_inventory_hardware(items, limit=1), ["USB Camera"])

    def test_looks_like_backend_web_search(self):
        self.assertTrue(looks_like_backend_web_search("联网搜一下天气"))
        self.assertTrue(looks_like_backend_web_search("查一下 Python 官网"))
        self.assertFalse(looks_like_backend_web_search("你好"))

    def test_extract_backend_web_search_query_strips_prefixes(self):
        self.assertEqual(extract_backend_web_search_query("搜索 Python 教程"), "Python 教程")
        self.assertEqual(extract_backend_web_search_query("查一下天气"), "天气")
        self.assertEqual(extract_backend_web_search_query("查询：天气"), "天气")
        self.assertEqual(extract_backend_web_search_query("搜索"), "搜索")

    def test_web_search_requested_metadata_overrides_heuristic(self):
        self.assertTrue(web_search_requested({"web_search_enabled": True}, "你好"))
        self.assertTrue(web_search_requested({"web_search_enabled": "on"}, "你好"))
        self.assertFalse(web_search_requested({"web_search_enabled": False}, "联网搜索新闻"))
        self.assertFalse(web_search_requested({"web_search_enabled": "off"}, "联网搜索新闻"))
        self.assertTrue(web_search_requested({}, "联网搜索新闻"))
        self.assertFalse(web_search_requested({}, "你好"))


if __name__ == "__main__":
    unittest.main()
