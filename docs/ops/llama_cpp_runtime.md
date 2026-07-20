# llama.cpp 本地运行时

SpiritKinAI 默认使用 llama.cpp 承载本地文本、多模态、Embedding 和 reranker 请求。LM Studio 只保留为可选兼容 Provider，不应与默认服务同时占用本地模型资源。

## 当前安装

- 版本：`b10058`（commit `788e07dc9`）
- 稳定入口：`runtime/llama.cpp/current/llama-server.exe`
- 实际目录：`runtime/llama.cpp/b10058/`
- 对话/多模态：`http://127.0.0.1:8080/v1`
- Embedding：`http://127.0.0.1:8081/v1`
- 日志与 PID：`state/llama.cpp/`
- 官方发布页：<https://github.com/ggml-org/llama.cpp/releases/tag/b10058>

安装包来自官方 Windows CUDA 12.4 x64 release。下载归档已按 GitHub release API 提供的 SHA-256 校验：

- `llama-b10058-bin-win-cuda-12.4-x64.zip`: `8bafeaabfd8b295d95e007a33581610c5c6869c531eaea681a563551910e0ef4`
- `cudart-llama-bin-win-cuda-12.4-x64.zip`: `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6`

## 模型

文本模型默认自动发现 LM Studio 原有模型库中的 Qwen GGUF，因此不需要复制 20GB 以上的权重：

```text
E:\AIModel\lmstudio-community\Qwen3.6-35B-A3B-GGUF\Qwen3.6-35B-A3B-Q4_K_M.gguf
E:\AIModel\lmstudio-community\Qwen3.6-35B-A3B-GGUF\mmproj-Qwen3.6-35B-A3B-BF16.gguf
```

Embedding 模型位于：

```text
runtime/llama.cpp/models/nomic-embed-text-v1.5.Q4_K_M.gguf
```

可在环境变量中覆盖：

```powershell
$env:SPIRITKIN_LLAMA_CPP_SERVER="D:\path\to\llama-server.exe"
$env:SPIRITKIN_LLAMA_CPP_TEXT_MODEL="D:\models\text.gguf"
$env:SPIRITKIN_LLAMA_CPP_MMPROJ="D:\models\mmproj.gguf"
$env:SPIRITKIN_LLAMA_CPP_EMBEDDING_MODEL="D:\models\embedding.gguf"
$env:SPIRITKIN_LLAMA_CPP_CONTEXT="8192"
$env:SPIRITKIN_LLAMA_CPP_PARALLEL="2"
```

## 启停与检查

WPF 启动时默认拉起两个隐藏的 `llama-server` 进程，并先停止 LM Studio 的本地 HTTP Server。设置 `SPIRITKIN_AUTO_START_LLAMACPP=0` 可关闭自动启动。也可在“管理 -> 学习与纠错”的 Provider 服务按钮中手动启停。

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8081/health
Invoke-RestMethod http://127.0.0.1:8080/v1/models
Invoke-RestMethod http://127.0.0.1:8081/v1/models
```

当前 35B-A3B Q4 模型在 16GB 显存机器上会部分 CPU offload，首个请求可能明显偏慢；这是容量/性能取舍，不是健康检查失败。需要更低延迟时，应换用可完整放入显存的小模型，并通过上述环境变量覆盖。

停止服务时优先使用桌面按钮。桌面只会终止自己记录的 PID 且会核对可执行文件路径，避免误杀其他 llama.cpp 进程。
