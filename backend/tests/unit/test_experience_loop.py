import unittest

from backend.app.experience_loop import verify_feishu_experience_loop
from backend.app.runtime import SpiritKinRuntime
from backend.orchestrator.agent_cluster import AgentCluster
from backend.services.feishu import FeishuSendResult


class ExperienceLoopTests(unittest.TestCase):
    def test_verify_feishu_experience_loop_reports_each_user_visible_stage(self):
        class FakeFeishuClient:
            def __init__(self):
                self.calls = []

            def send_text_message(self, recipient: str, text: str):
                self.calls.append((recipient, text))
                return FeishuSendResult(True, recipient, f"user_id:{recipient}", "user_id", text, message_id="experience-dry-run")

        client = FakeFeishuClient()
        runtime = SpiritKinRuntime(agent=AgentCluster(llm_client=lambda _: self.fail("should not call llm"), feishu_client=client))

        report = verify_feishu_experience_loop(runtime)

        self.assertTrue(report.passed, report.to_markdown())
        self.assertEqual(client.calls, [("张三", "会议改到三点")])
        stages = set(report.by_stage())
        self.assertTrue({"耳朵/ASR", "眼睛/视觉上下文", "脑/意图解析", "安全/确认门", "手脚/执行器", "嘴/反馈话术", "脸/Live2D", "事件/前端"}.issubset(stages))


if __name__ == "__main__":
    unittest.main()