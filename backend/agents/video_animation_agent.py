from backend.agents.base import AgentContext, AgentReply, BaseAgent, parse_tagged_response
from backend.prompts.agent_roles import VIDEO_ANIMATION_AGENT_PROMPT

VIDEO_ANIMATION_KEYWORDS = (
    "视频",
    "动画",
    "分镜",
    "镜头",
    "剪辑",
    "字幕",
    "配音",
    "旁白",
    "ffmpeg",
    "宣传片",
    "短片",
    "动效",
    "转场",
)


class VideoAnimationAgent(BaseAgent):
    name = "video_animation"
    domain = "video_animation"
    routing_priority = 220
    resource_profile = "gpu_heavy"

    def __init__(self, llm_client):
        self._llm_client = llm_client

    def can_handle(self, context: AgentContext) -> bool:
        user_input = context.user_input.lower()
        return any(keyword in user_input for keyword in VIDEO_ANIMATION_KEYWORDS)

    def match_score(self, context: AgentContext) -> int:
        user_input = context.user_input.lower()
        return sum(1 for keyword in VIDEO_ANIMATION_KEYWORDS if keyword in user_input)

    def handle(self, context: AgentContext) -> AgentReply:
        prompt = VIDEO_ANIMATION_AGENT_PROMPT.substitute(prompt_context=context.prompt_context)
        raw = self._llm_client(prompt, agent_name=self.name)
        text, emotion = parse_tagged_response(raw, default_emotion="thinking")
        action = "storyboard_plan" if emotion != "confused" else "tilt_head"
        return AgentReply(text=text, emotion=emotion, action=action, agent_name=self.name)
