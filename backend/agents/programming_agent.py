from backend.agents.base import AgentContext, AgentReply, BaseAgent, parse_tagged_response
from backend.prompts.agent_roles import PROGRAMMING_AGENT_PROMPT

PROGRAMMING_KEYWORDS = (
    "代码",
    "编程",
    "python",
    "java",
    "报错",
    "bug",
    "函数",
    "接口",
    "脚本",
    "debug",
)


class ProgrammingAgent(BaseAgent):
    name = "programming"
    domain = "programming"
    routing_priority = 210
    resource_profile = "gpu_heavy"

    def __init__(self, llm_client):
        self._llm_client = llm_client

    def can_handle(self, context: AgentContext) -> bool:
        user_input = context.user_input.lower()
        return any(keyword in user_input for keyword in PROGRAMMING_KEYWORDS)

    def match_score(self, context: AgentContext) -> int:
        user_input = context.user_input.lower()
        return sum(1 for keyword in PROGRAMMING_KEYWORDS if keyword in user_input)

    def handle(self, context: AgentContext) -> AgentReply:
        code_context = ""
        code_record = context.metadata.get("code_workspace_context") if isinstance(context.metadata, dict) else None
        if isinstance(code_record, dict):
            summary = str(code_record.get("summary") or "").strip()
            if summary:
                code_context = f"\n{summary}\n"
        prompt = PROGRAMMING_AGENT_PROMPT.substitute(
            code_context=code_context,
            prompt_context=context.prompt_context,
        )
        raw = self._llm_client(prompt, agent_name=self.name)
        text, emotion = parse_tagged_response(raw, default_emotion="thinking")
        action = "write_on_board" if emotion != "confused" else "tilt_head"
        return AgentReply(text=text, emotion=emotion, action=action, agent_name=self.name)
