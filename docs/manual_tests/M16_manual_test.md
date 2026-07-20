# M16 手测记录

- 日期：2026-07-18
- 环境：Windows 11 / WPF .NET 8
- 状态：代码通过，真实麦克风长时通话待外部验收

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | 权威状态机 | idle/listening/thinking/speaking/error/end 转换专项通过 | 通过 |
| 2 | 字幕与打断 | VAD、partial/final 字幕、TTS 打断和 phoneme 事件契约通过 | 通过 |
| 3 | 权限与清理 | 未授权麦克风给出可操作错误，结束后释放会话资源 | 通过 |
| 4 | 合成音频闭环 | 中文 TTS MP3 与 VTT 生成成功，faster-whisper 从文件识别成功；验证无需麦克风或扬声器 | 通过 |
| 5 | 连续 10 分钟/拔插 | 环境噪声、系统录音权限、设备拔插、回声和 TTS duck/恢复仍需要真实麦克风与扬声器 | 待外部验收 |

- 禁改区抽查：应用不自动接听，也不在未授权时开启麦克风。
- 证据：`backend/tests/unit/test_voice_call.py`、WPF 桌面回归、`tmp/synthetic-audio-20260718/`。
- 结论：实现通过；真实设备长时体验待验收。
