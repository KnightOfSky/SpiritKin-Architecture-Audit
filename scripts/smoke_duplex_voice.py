from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.app.runtime import SpiritKinRuntime
from backend.perception.audio.listener import AsrModelUnavailableError, calibrate_microphone, get_whisper_model
from backend.perception.audio.realtime_session import RealtimeDuplexSession, RealtimeSessionConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="LPM-like realtime duplex voice smoke test.")
    parser.add_argument("--max-turns", type=int, default=0, help="最多处理多少个有效语音回合；0 表示常驻监听")
    parser.add_argument("--timeout", type=float, default=8.0, help="每轮等待用户开始说话的秒数")
    parser.add_argument("--phrase-time-limit", type=float, default=4.0, help="每轮最长录音秒数")
    parser.add_argument("--idle-timeouts", type=int, default=0, help="连续几次没听到有效语音后退出；0 表示不因空闲退出")
    parser.add_argument("--wake-window", type=float, default=60.0, help="说出唤醒词后多少秒内可连续输入；超时需重新唤醒")
    parser.add_argument("--no-hotword-required", action="store_true", help="调试模式：不要求先说 Spirit 唤醒")
    parser.add_argument("--no-speak", action="store_true", help="只打印/发事件，不播放 TTS")
    parser.add_argument("--no-calibrate", action="store_true", help="跳过麦克风环境噪声校准")
    parser.add_argument("--no-preload", action="store_true", help="跳过 ASR 模型预热")
    parser.add_argument("--emit-events", action="store_true", help="向 runtime websocket 事件通道发送状态")
    args = parser.parse_args()

    if not args.no_calibrate:
        calibrate_microphone(duration=1)

    if not args.no_preload:
        try:
            print("[🤔] 预热 ASR 模型，避免首轮说完才加载...")
            get_whisper_model(allow_fallback=False)
            print("[OK] ASR 模型已就绪")
        except AsrModelUnavailableError as exc:
            print(f"[FAIL] ASR 模型不可用：{exc}")
            return 2

    runtime = SpiritKinRuntime(emit_runtime_events=args.emit_events)
    session = RealtimeDuplexSession(
        runtime,
        config=RealtimeSessionConfig(
            listen_timeout=args.timeout,
            phrase_time_limit=args.phrase_time_limit,
            max_turns=args.max_turns,
            idle_timeouts=args.idle_timeouts,
            speak_responses=not args.no_speak,
            require_hotword=not args.no_hotword_required,
            wake_window_seconds=args.wake_window,
        ),
    )

    print("\n# LPM-like 双工语音会话")
    print(f"默认常驻监听：先说 Spirit 唤醒；唤醒后 {int(args.wake_window)} 秒内可连续说话，超时需重新唤醒；Ctrl+C 退出。")
    try:
        turns = session.run()
    except KeyboardInterrupt:
        session.stop()
        print("\n[STOP] 用户中断会话")
        return 130

    print("\n# 会话摘要")
    print(f"turns={len(turns)}")
    for turn in turns:
        reply = turn.reply
        print(f"[{turn.index}] raw={turn.text!r} cleaned={turn.cleaned_text!r}")
        if reply is not None:
            print(f"    agent={reply.agent_name} action={reply.action} text={reply.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
