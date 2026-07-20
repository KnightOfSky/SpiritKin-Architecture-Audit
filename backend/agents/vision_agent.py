from backend.agents.base import AgentContext, AgentReply, BaseAgent

VISION_KEYWORDS = (
    "屏幕",
    "界面",
    "截图",
    "画面",
    "按钮",
    "窗口",
    "图像",
    "图片",
    "ocr",
)

OCR_KEYWORDS = ("文字", "文本", "ocr", "识别")


class VisionAgent(BaseAgent):
    name = "vision"
    domain = "vision"
    routing_priority = 200
    resource_profile = "gpu_heavy"

    def __init__(self, device_backend):
        self._device_backend = device_backend

    def can_handle(self, context: AgentContext) -> bool:
        user_input = context.user_input.lower()
        return any(keyword in user_input for keyword in VISION_KEYWORDS)

    def match_score(self, context: AgentContext) -> int:
        user_input = context.user_input.lower()
        return sum(1 for keyword in VISION_KEYWORDS if keyword in user_input)

    def handle(self, context: AgentContext) -> AgentReply:
        user_input = context.user_input.lower()
        if any(keyword in user_input for keyword in OCR_KEYWORDS):
            text = self._device_backend.extract_text()
        else:
            text = self._device_backend.understand_screen(context.user_input)

        if not text:
            text = context.visual_context or "我暂时没有拿到可用的视觉结果。"
            emotion = "confused"
        else:
            emotion = "thinking"

        return AgentReply(text=text, emotion=emotion, action="scan_screen", agent_name=self.name)