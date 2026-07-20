from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.agents.base import AgentReply
from backend.app.runtime import InteractionInput, SpiritKinRuntime, dispatch_runtime_event, resolve_event_sink_url
from backend.expression.performance import PerformanceState


def _demo_metadata(**metadata) -> dict[str, object]:
    return {"demo": "lpm_duplex_effect", **metadata}


def _performance_events(phase: str, message: str, **metadata):
    state = PerformanceState(phase=phase, message=message, metadata=_demo_metadata(**metadata))
    return [state.to_event(), state.to_avatar_event()]


def _input_event(text: str) -> dict[str, object]:
    return SpiritKinRuntime.build_input_payload(
        InteractionInput(text=text, channel="voice", metadata=_demo_metadata(simulated_voice=True))
    )


def _reply_events(text: str, *, spoken: str | None = None, emotion: str = "happy", action: str = "speak"):
    return SpiritKinRuntime.build_response_events(
        AgentReply(
            text=text,
            spoken_text=spoken or text,
            emotion=emotion,
            action=action,
            agent_name="demo_lpm_cluster",
            metadata=_demo_metadata(),
        )
    )


def _execution_reply_events():
    return SpiritKinRuntime.build_response_events(
        AgentReply(
            text="已切换计划：优先打开飞书，浏览器任务暂停。",
            spoken_text="收到，我改成先打开飞书。",
            emotion="happy",
            action="execute_task",
            agent_name="executor_local_pc",
            metadata={
                **_demo_metadata(),
                "execution": {
                    "target": "local_pc",
                    "operation": "launch_app",
                    "success": True,
                    "data": {"app_name": "feishu", "resolved_app": "feishu"},
                    "metadata": _demo_metadata(duplex_interrupt=True),
                }
            },
        )
    )


def build_demo_sequence() -> list[tuple[float, dict[str, object]]]:
    runtime = SpiritKinRuntime(agent=object())
    events: list[tuple[float, dict[str, object]]] = [(0.2, runtime.build_capabilities_payload())]
    scripted_groups = [
        _performance_events("silence_idle", "我在线，等待唤醒。"),
        [_input_event("Spirit 打开浏览器")],
        _performance_events("listening", "我在听，你可以继续补充。", duplex=True),
        _performance_events("thinking", "我听到了：打开浏览器。正在理解。", turn=1),
        _reply_events("好的，我准备打开浏览器。", spoken="好的，我准备打开浏览器。"),
        _performance_events("speaking", "好的，我准备打开浏览器。", turn=1),
        [_input_event("等等，先别打开浏览器，打开飞书")],
        _performance_events("interrupted", "收到插话，我先停下上一句。", barge_in=True),
        _performance_events("thinking", "已采纳最新意图：打开飞书。", turn=2),
        _performance_events("acting", "正在调用本地 PC 执行器。", target="local_pc", operation="launch_app"),
        _execution_reply_events(),
        _performance_events("speaking", "收到，我改成先打开飞书。", turn=2),
        _performance_events("silence_idle", "演示结束：这只是模拟事件，不代表真实麦克风任务完成。"),
    ]
    for group in scripted_groups:
        for event in group:
            events.append((0.65, event))
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit a visible LPM-like duplex interaction demo to the realtime event bridge.")
    parser.add_argument("--url", default=resolve_event_sink_url(), help="事件桥 WebSocket 地址")
    parser.add_argument("--delay-scale", type=float, default=1.0, help="演示速度倍率，越小越快")
    parser.add_argument("--loop", action="store_true", help="循环播放演示")
    args = parser.parse_args()

    print(f"# LPM-like 双工效果演示 -> {args.url}")
    print("请先打开 frontend/index.html，并确保事件桥已启动：python -m backend.app.realtime_bridge")
    sent_any = False
    try:
        while True:
            for delay, event in build_demo_sequence():
                time.sleep(max(0.0, delay * args.delay_scale))
                ok = dispatch_runtime_event(args.url, event)
                sent_any = sent_any or ok
                label = event.get("type", "event")
                payload = event.get("payload", {}) if isinstance(event.get("payload"), dict) else {}
                print(f"{'OK' if ok else 'MISS'} {label} {payload.get('phase') or payload.get('text') or ''}")
            if not args.loop:
                break
    except KeyboardInterrupt:
        return 130
    return 0 if sent_any else 2


if __name__ == "__main__":
    raise SystemExit(main())