"""Prompts for the voice pipeline (intent resolution + ASR biasing)."""

from __future__ import annotations

from string import Template

INTENT_RESOLVER_PROMPT = Template(
    "You are a voice assistant for a Windows PC. The user is speaking and you must correct ASR errors first.\n"
    "$inv\n\n"
    "$asr_context\n\n"
    "Tools: $tools_text\n\n"
    "User said: $user_input\n\n"
    "Step 1: Fix ASR errors. Common mistakes: 'spring'/'sprint'='Spirit'(wake word, not a command); 'speed tree'/'speech tree' is likely an app name.\n"
    "Step 2: Map the CORRECTED text to a tool. If asking to open/close an app, find the closest match from installed apps above.\n"
    "Step 3: If the user is just greeting or chatting (not a command), set intent=none.\n"
    'Output ONLY JSON: {"intent":"execute|clarify|none","tool_name":"app.launch","confidence":0.8,"corrected_text":"打开SpeedTree for UE4","reason":"matched SpeedTree"}\n'
)

# Whisper/SpeechRecognition initial-prompt biasing texts. Selected by
# backend/perception/audio/listener.py based on the configured ASR language.
ASR_INITIAL_PROMPT_YUE = (
    "呢段係粵語或中英混合嘅個人智能體語音指令，可能涉及軟件控制、跨設備操作、飛書、瀏覽器、"
    "Edge、Chrome、Firefox、Brave、Opera、文件、屏幕理解、打開、開啟、關閉、搜尋、發送消息、確認執行、取消執行。"
    "請保留英文軟件名，唔好將 Edge、Chrome、Firefox、Brave、Opera 翻譯成無關中文詞。"
)

ASR_INITIAL_PROMPT_AUTO = (
    "This is a Mandarin or Cantonese personal assistant voice command, possibly mixed with English app names. "
    "It may mention Edge, Chrome, Firefox, Brave, Opera, Feishu/Lark, files, screen understanding, open, close, search, send message, confirm execution, or cancel execution. "
    "Keep app names literal."
)

ASR_INITIAL_PROMPT_ZH = (
    "这是一段普通话个人智能体语音指令，可能中英混合，涉及软件控制、跨设备操作、飞书、浏览器、Edge、Chrome、Firefox、Brave、Opera、火豹浏览器、VSCode、微信、钉钉、文件、屏幕理解、机械臂、OpenClaw、打开、关闭、搜索、发送消息。请保留英文软件名，不要把 Edge 听写成新的，不要把 Chrome/Firefox/Brave/Opera 翻译成无关中文词，不要把火豹浏览器听写成火爆浏览器。"
)
