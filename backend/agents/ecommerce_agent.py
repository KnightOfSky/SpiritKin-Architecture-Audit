from backend.agents.base import AgentContext, AgentReply, BaseAgent, parse_tagged_response
from backend.prompts.agent_roles import ECOMMERCE_AGENT_PROMPT

ECOMMERCE_KEYWORDS = (
    "电商",
    "淘宝",
    "天猫",
    "京东",
    "拼多多",
    "亚马逊",
    "商品",
    "店铺",
    "sku",
    "客服",
    "售后",
    "投流",
    "投放",
    "roi",
    "gmv",
    "转化率",
    "主图",
    "详情页",
    "选品",
    "直播",
)


class EcommerceAgent(BaseAgent):
    name = "ecommerce"
    domain = "ecommerce"
    routing_priority = 300
    resource_profile = "gpu_heavy"

    def __init__(self, llm_client):
        self._llm_client = llm_client

    def can_handle(self, context: AgentContext) -> bool:
        user_input = context.user_input.lower()
        return any(keyword in user_input for keyword in ECOMMERCE_KEYWORDS)

    def match_score(self, context: AgentContext) -> int:
        user_input = context.user_input.lower()
        return sum(1 for keyword in ECOMMERCE_KEYWORDS if keyword in user_input)

    def handle(self, context: AgentContext) -> AgentReply:
        project = context.metadata.get("project") or {}
        project_context = ""
        if project:
            next_actions = project.get("next_actions", [])[:3]
            project_context = (
                f"\n当前电商项目：{project.get('project_id', '')}"
                f"\n项目类型：{project.get('project_type', '')}"
                f"\n当前阶段：{project.get('current_phase', '')}"
                f"\n下一步建议：{'；'.join(str(item) for item in next_actions)}"
            )
        prompt = ECOMMERCE_AGENT_PROMPT.substitute(
            project_context=project_context,
            prompt_context=context.prompt_context,
        )
        raw = self._llm_client(prompt, agent_name=self.name)
        text, emotion = parse_tagged_response(raw, default_emotion="thinking")
        action = "write_plan" if emotion != "confused" else "tilt_head"
        return AgentReply(text=text, emotion=emotion, action=action, agent_name=self.name)
