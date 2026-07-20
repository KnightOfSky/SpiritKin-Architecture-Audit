# M10 手测记录

- 日期：2026-07-17
- 环境：Windows 11 / Python 3.12 / PyInstaller 6.21.0
- 状态：本机 Worker 通过，跨机与真实电商账号待外部验收

| # | 场景 | 实际 | 结果 |
|---|---|---|---|
| 1 | one-file Worker | 构建 `spiritkin-control-plane-worker.exe`，大小 8,750,245 bytes | 通过 |
| 2 | 断联恢复 | EXE 执行中断联后 outbox=1；恢复 875 ms 后 outbox=0，任务完成 | 通过 |
| 3 | 签名发布包 | ZIP 包含 EXE、签名清单和 EXE 优先安装脚本；在线托管包已更新 | 通过 |
| 4 | 物理远端与 PDD | 需要第二台 PC、真实网络断开和已登录拼多多浏览器 Profile | 待外部验收 |

- 禁改区抽查：控制面和 Worker 均执行安全校验，失败审计增量写入。
- 证据：`tmp/control-plane-worker-exe-recovery-20260717/`、`dist/spiritkin-control-plane-worker-onefile.zip`。
- 结论：本机独立 EXE 与恢复链路通过；跨机和真实账号待验收。
