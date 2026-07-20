from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.experience_loop import verify_feishu_experience_loop
from backend.app.runtime import SpiritKinRuntime
from backend.orchestrator.agent_cluster import AgentCluster


def _offline_llm(prompt: str) -> str:
    raise RuntimeError("体验闭环验证中，飞书发送意图不应退回通用 LLM")


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 SpiritKin 飞书体验闭环：耳/眼/脑/确认/手脚/嘴/脸/事件")
    parser.add_argument("--transcript", default="给张三发飞书，说会议改到三点", help="模拟 ASR 输出的用户语音文本")
    parser.add_argument("--visual-context", default="桌面可见飞书窗口，用户希望发送一条工作通知", help="模拟视觉上下文")
    parser.add_argument("--confirm", default="确认执行", help="二次确认语句")
    args = parser.parse_args()

    os.environ["SPIRIT_FEISHU_DRY_RUN"] = "1"
    os.environ.setdefault("SPIRIT_FEISHU_CONTACTS_JSON", '{"张三":"user_id:demo_zhangsan"}')

    runtime = SpiritKinRuntime(agent=AgentCluster(llm_client=_offline_llm), emit_runtime_events=False)
    report = verify_feishu_experience_loop(
        runtime,
        transcript=args.transcript,
        visual_context=args.visual_context,
        confirmation_text=args.confirm,
    )
    print(report.to_markdown())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())