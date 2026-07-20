from backend.agents.base import AgentContext, AgentReply, BaseAgent, parse_tagged_response
from backend.prompts.agent_roles import GAME_DEVELOPMENT_AGENT_PROMPT

GAME_DEVELOPMENT_KEYWORDS = (
    "游戏",
    "unity",
    "unreal",
    "ue5",
    "godot",
    "关卡",
    "蓝图",
    "玩法",
    "数值",
    "战斗系统",
    "角色系统",
    "状态机",
    "游戏开发",
    "游戏制作",
)


class GameDevelopmentAgent(BaseAgent):
    name = "game_development"
    domain = "game_development"
    routing_priority = 220
    resource_profile = "gpu_heavy"

    def __init__(self, llm_client):
        self._llm_client = llm_client

    def can_handle(self, context: AgentContext) -> bool:
        user_input = context.user_input.lower()
        return any(keyword in user_input for keyword in GAME_DEVELOPMENT_KEYWORDS)

    def match_score(self, context: AgentContext) -> int:
        user_input = context.user_input.lower()
        return sum(1 for keyword in GAME_DEVELOPMENT_KEYWORDS if keyword in user_input)

    def handle(self, context: AgentContext) -> AgentReply:
        prompt = GAME_DEVELOPMENT_AGENT_PROMPT.substitute(prompt_context=context.prompt_context)
        raw = self._llm_client(prompt, agent_name=self.name)
        text, emotion = parse_tagged_response(raw, default_emotion="thinking")
        action = "design_game_system" if emotion != "confused" else "tilt_head"
        return AgentReply(text=text, emotion=emotion, action=action, agent_name=self.name)
