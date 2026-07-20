from __future__ import annotations

import re
from dataclasses import dataclass

from backend.agents.base import AgentContext, BaseAgent
from backend.executors.base import ExecutionRequest
from backend.tools.base import ToolCall, ToolSpec


@dataclass
class ExecutionPlan:
    route: str
    reason: str
    domain: str = "general"
    priority_score: int = 0
    resource_profile: str = "interactive"
    builtin_name: str | None = None
    agent: BaseAgent | None = None
    execution_request: ExecutionRequest | None = None
    tool_call: ToolCall | None = None
    development_request: str | None = None


class Planner:
    """第一版规划器：先做路由规划，后续可演进为多步任务拆解。"""

    AGENT_MATCH_WEIGHT = 30

    def plan(self, context: AgentContext, agents: list[BaseAgent], available_tools: list[ToolSpec] | None = None) -> ExecutionPlan:
        forced_agent_id = str((context.metadata or {}).get("forced_agent_id") or (context.metadata or {}).get("target_agent_id") or "").strip()
        if forced_agent_id:
            selected_agent = self._agent_by_id(forced_agent_id, agents)
            if selected_agent is not None:
                return ExecutionPlan(
                    route="agent",
                    agent=selected_agent,
                    reason=f"用户 @ 指定 {forced_agent_id} agent",
                    domain=getattr(selected_agent, "domain", selected_agent.name),
                    priority_score=1000 + int(getattr(selected_agent, "routing_priority", 0)),
                    resource_profile=getattr(selected_agent, "resource_profile", "gpu_heavy"),
                )

        if self._looks_like_time_query(context.user_input):
            return ExecutionPlan(route="builtin", builtin_name="time", reason="命中时间工具", domain="utility", priority_score=1000, resource_profile="interactive")

        if self._looks_like_calc_query(context.user_input):
            return ExecutionPlan(route="builtin", builtin_name="calc", reason="命中计算工具", domain="utility", priority_score=1000, resource_profile="interactive")

        development_request = self._build_development_request(context.user_input)
        if development_request is not None:
            return ExecutionPlan(
                route="development_plan",
                development_request=development_request,
                reason="命中新接入开发计划",
                domain="programming",
                priority_score=260,
                resource_profile="cpu_io",
            )

        tool_call = self._build_tool_call(context.user_input, available_tools or [])
        if tool_call is not None:
            return ExecutionPlan(
                route="tool",
                tool_call=tool_call,
                reason=f"命中工具调用: {tool_call.name}",
                domain="search",
                priority_score=180,
                resource_profile="cpu_io",
            )

        execution_request = self._build_execution_request(context.user_input)
        if execution_request is not None:
            return ExecutionPlan(
                route="executor",
                execution_request=execution_request,
                reason=f"命中动作执行: {execution_request.target}.{execution_request.operation}",
                domain="execution",
                priority_score=220,
                resource_profile="cpu_io",
            )

        if self._looks_like_ambiguous_openclaw_request(context.user_input.strip().lower()):
            return ExecutionPlan(
                route="clarify_openclaw",
                reason="疑似 OpenClaw 指令但动作不明确",
                domain="execution",
                priority_score=210,
                resource_profile="interactive",
            )

        selected_agent, score = self._select_agent(context, agents)
        if selected_agent is not None:
            return ExecutionPlan(
                route="agent",
                agent=selected_agent,
                reason=f"命中 {selected_agent.name} agent，优先级得分 {score}",
                domain=getattr(selected_agent, "domain", selected_agent.name),
                priority_score=score,
                resource_profile=getattr(selected_agent, "resource_profile", "gpu_heavy"),
            )

        return ExecutionPlan(route="general", reason="未命中专业 agent，走通用回答", domain="general", priority_score=100, resource_profile="gpu_heavy")

    def _select_agent(self, context: AgentContext, agents: list[BaseAgent]) -> tuple[BaseAgent | None, int]:
        ranked: list[tuple[int, int, int, int, BaseAgent]] = []
        for index, agent in enumerate(agents):
            if not agent.can_handle(context):
                continue
            match_score = max(1, int(agent.match_score(context)))
            priority = int(getattr(agent, "routing_priority", 0))
            score = priority + match_score * self.AGENT_MATCH_WEIGHT
            ranked.append((score, match_score, priority, -index, agent))

        if not ranked:
            return None, 0

        score, _, _, _, agent = max(ranked, key=lambda item: item[:4])
        return agent, score

    @staticmethod
    def _agent_by_id(agent_id: str, agents: list[BaseAgent]) -> BaseAgent | None:
        target = agent_id.strip().lower()
        for agent in agents:
            if str(getattr(agent, "name", "") or "").strip().lower() == target:
                return agent
        return None

    @staticmethod
    def _looks_like_time_query(user_input: str) -> bool:
        return any(keyword in user_input for keyword in ["时间", "几点"])

    @staticmethod
    def _looks_like_calc_query(user_input: str) -> bool:
        has_operator = any(op in user_input for op in ["+", "-", "*", "/"])
        asks_result = any(keyword in user_input for keyword in ["等于", "多少"])
        return has_operator and asks_result and bool(re.search(r"[0-9]", user_input))

    def _build_development_request(self, user_input: str) -> str | None:
        normalized = user_input.strip()
        if not normalized:
            return None
        if self._looks_like_development_plan_request(normalized):
            return normalized
        return None

    @staticmethod
    def _looks_like_development_plan_request(user_input: str) -> bool:
        normalized = user_input.strip().lower()
        if not normalized:
            return False

        specific_targets = [
            "飞书",
            "feishu",
            "lark",
            "抖店",
            "抖音小店",
            "douyin",
            "tiktok shop",
            "vscode",
            "visual studio code",
            "代码编辑器",
            "编辑器",
        ]
        generic_targets = [
            "api接入",
            "软件接入",
            "外部api",
            "外部接口",
            "第三方接口",
            "第三方软件",
            "saas接入",
        ]
        planning_keywords = ["开发计划", "接入计划", "对接计划", "集成计划", "方案", "规划", "审核", "roadmap"]
        development_keywords = ["接入", "对接", "集成", "开发", "新增", "增加", "扩展", "插件", "桥接"]
        capability_keywords = ["api", "sdk", "接口", "webhook", "回调", "扩展", "插件", "bridge", "桥接", "工具"]

        mentions_target = any(keyword in normalized for keyword in specific_targets) or any(keyword in normalized for keyword in generic_targets)
        asks_for_plan = any(keyword in normalized for keyword in planning_keywords)
        asks_for_integration = any(keyword in normalized for keyword in development_keywords)
        mentions_integration_shape = any(keyword in normalized for keyword in capability_keywords)
        return mentions_target and ((asks_for_plan and asks_for_integration) or (asks_for_integration and mentions_integration_shape))

    def _build_tool_call(self, user_input: str, available_tools: list[ToolSpec]) -> ToolCall | None:
        available_tool_names = {tool.name for tool in available_tools}
        if "kb.search" not in available_tool_names:
            return None

        if not self._looks_like_knowledge_query(user_input):
            return None

        return ToolCall(name="kb.search", arguments={"query": user_input, "top_k": 3})

    @staticmethod
    def _looks_like_knowledge_query(user_input: str) -> bool:
        query = user_input.strip().lower()
        knowledge_keywords = [
            "知识库",
            "文档",
            "资料",
            "手册",
            "架构",
            "开发记录",
            "路线",
            "流程",
            "训练集",
            "训练数据",
            "模型训练",
            "roadmap",
        ]
        return any(keyword in query for keyword in knowledge_keywords)

    def _build_execution_request(self, user_input: str) -> ExecutionRequest | None:
        target, command_text = self._extract_execution_target(user_input)
        normalized = command_text.strip().lower()

        feishu_request = self._build_feishu_request(normalized, command_text)
        if feishu_request is not None:
            return feishu_request

        desktop_request = self._build_desktop_utility_request(normalized, command_text, target)
        if desktop_request is not None:
            return desktop_request

        software_request = self._build_software_request(normalized, command_text, target)
        if software_request is not None:
            return software_request

        pointer_request = self._build_pointer_request(normalized, command_text, target)
        if pointer_request is not None:
            return pointer_request

        input_request = self._build_text_input_request(command_text, target)
        if input_request is not None:
            return input_request

        arm_request = self._build_arm_request(normalized, command_text)
        if arm_request is not None:
            return arm_request

        return None

    @staticmethod
    def _extract_execution_target(user_input: str) -> tuple[str, str]:
        target = "local_pc"
        text = user_input.strip()
        action_words = "打开|打開|開啟|启动|啟動|运行|運行|关闭|關閉|关掉|關掉|退出|结束|結束|扫描|掃描|列出|枚举|枚舉|查看|看一下|看看|睇下|睇吓|看|睇|读|讀|读取|讀取|识别|識別|截图|截屏|搜索|搜尋|复制|複製|写入|寫入|切换|切換|激活|按一下|按下|按|输入|輸入|键入|鍵入|打字|点击|點擊|点一下|點一下|双击|雙擊|移动到|移動到|移到|移动|移動"
        patterns = [
            rf"(?:在|让|叫)(?P<target>.+?)(?:上)?(?P<command>{action_words}).*$",
            rf"(?P<target>本机|本地|这台电脑|当前电脑|远端|远程|公司电脑|办公室电脑|家里电脑)(?:上)?(?P<command>{action_words}).*$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is None:
                continue
            raw_target = match.group("target").strip().removesuffix("上")
            if raw_target in {"本机", "本地", "这台电脑", "当前电脑"}:
                target = "local_pc"
            elif raw_target in {"远端", "远程"}:
                target = "remote"
            else:
                target = raw_target
            return target, text[match.start("command") :]
        return target, text

    @staticmethod
    def _extract_numbers(user_input: str) -> list[float]:
        return [float(value) for value in re.findall(r"-?[0-9]+(?:\.[0-9]+)?", user_input)]

    @staticmethod
    def _normalize_openclaw_asr_text(text: str) -> str:
        normalized = text.strip().lower()
        replacements = {
            "機械": "机械",
            "狀態": "状态",
            "現在": "现在",
            "戴开": "打开",
            "戴開": "打开",
            "打開": "打开",
            "開啟": "打开",
            "關閉": "关闭",
            "關掉": "关闭",
            "搜尋": "搜索",
            "睇下": "看一下",
            "睇吓": "看一下",
            "怎麼": "怎么",
            "裝它": "状态",
            "装它": "状态",
            "裝胎": "状态",
            "装胎": "状态",
            "机械b": "机械臂",
            "機械b": "机械臂",
            "机械毕竟": "机械臂",
            "機械畢竟": "机械臂",
        }
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        return normalized

    @staticmethod
    def _compact_voice_text(text: str) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.lower())

    def _looks_like_ambiguous_openclaw_request(self, normalized: str) -> bool:
        normalized = self._normalize_openclaw_asr_text(normalized)
        hardware_hints = ("机械臂", "机械", "機械", "openclaw", "夹爪", "夾爪", "机械手", "機械手")
        return any(keyword in normalized for keyword in hardware_hints)

    def _build_pointer_request(self, normalized: str, raw_input: str, target: str = "local_pc") -> ExecutionRequest | None:
        pointer_keywords = ("鼠标", "光标", "指针")
        if not any(keyword in normalized for keyword in pointer_keywords):
            return None

        if any(keyword in normalized for keyword in ["移动到", "移到", "移动至", "挪到"]):
            coords = self._extract_numbers(raw_input)
            if len(coords) >= 2:
                return ExecutionRequest(
                    target=target,
                    operation="move_pointer",
                    params={"x": int(coords[0]), "y": int(coords[1])},
                )

        if any(keyword in normalized for keyword in ["双击", "点两下"]):
            coords = self._extract_numbers(raw_input)
            if len(coords) >= 2:
                return ExecutionRequest(
                    target=target,
                    operation="click_pointer",
                    params={"x": int(coords[0]), "y": int(coords[1]), "double": True},
                )

        if any(keyword in normalized for keyword in ["点击", "点一下", "单击"]):
            coords = self._extract_numbers(raw_input)
            if len(coords) >= 2:
                return ExecutionRequest(
                    target=target,
                    operation="click_pointer",
                    params={"x": int(coords[0]), "y": int(coords[1]), "double": False},
                )

        return None

    def _build_desktop_utility_request(self, normalized: str, raw_input: str, target: str = "local_pc") -> ExecutionRequest | None:
        if any(keyword in normalized for keyword in ["截图", "截屏", "屏幕截图", "抓屏"]):
            return ExecutionRequest(target=target, operation="screen_capture")

        if any(keyword in normalized for keyword in ["拍照", "摄像头", "自拍", "相机", "拍一张", "camera", "cam"]):
            return ExecutionRequest(target=target, operation="camera_capture")

        file_request = self._build_file_request(normalized, raw_input, target)
        if file_request is not None:
            return file_request

        if "剪贴板" in normalized or "剪切板" in normalized:
            if any(keyword in normalized for keyword in ["读取", "读", "查看", "看看", "什么"]):
                return ExecutionRequest(target=target, operation="clipboard_read")
            if any(keyword in normalized for keyword in ["写入", "复制", "放到", "设置"]):
                text = self._extract_text_argument(raw_input)
                if text:
                    return ExecutionRequest(target=target, operation="clipboard_write", params={"text": text})

        if any(keyword in normalized for keyword in ["窗口", "窗体"]):
            if any(keyword in normalized for keyword in ["列出", "查看", "有哪些", "当前"]):
                return ExecutionRequest(target=target, operation="window_list")
            activate_title = self._extract_window_title(raw_input, verbs=("切换到", "激活", "打开"))
            if activate_title:
                return ExecutionRequest(target=target, operation="window_activate", params={"title": activate_title})
            close_title = self._extract_window_title(raw_input, verbs=("关闭", "关掉", "退出"))
            if close_title:
                return ExecutionRequest(target=target, operation="window_close", params={"title": close_title})
            resize_title = self._extract_window_title(raw_input, verbs=("调整", "resize", "调大", "调小"))
            if resize_title:
                dims = self._extract_dimensions(raw_input)
                if dims:
                    return ExecutionRequest(target=target, operation="window_resize", params={"title": resize_title, "width": dims[0], "height": dims[1]})
            move_title = self._extract_window_title(raw_input, verbs=("移动", "移到", "move"))
            if move_title:
                coords = self._extract_numbers(raw_input)
                if len(coords) >= 2:
                    return ExecutionRequest(target=target, operation="window_move", params={"title": move_title, "x": int(coords[0]), "y": int(coords[1])})

        if any(keyword in normalized for keyword in ["通知", "提醒", "弹窗"]):
            notification = self._extract_notification(raw_input)
            if notification:
                return ExecutionRequest(target=target, operation="notification_send", params=notification)

        url_match = re.search(r"(?P<url>https?://[^\s，。,.!?！？]+|(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s，。]*)?)", raw_input, flags=re.IGNORECASE)
        if url_match and any(keyword in normalized for keyword in ["打开", "打開", "開啟", "访问", "訪問", "浏览", "瀏覽"]):
            return ExecutionRequest(target=target, operation="browser_open_url", params={"url": url_match.group("url")})

        if any(keyword in normalized for keyword in ["搜索", "搜尋", "查一下", "搜一下", "搵"]):
            query = self._extract_search_query(raw_input)
            if query:
                return ExecutionRequest(target=target, operation="browser_search", params={"query": query, "engine": self._infer_search_engine(normalized)})

        if any(keyword in normalized for keyword in ["标签页", "tab", "tabs"]):
            if any(keyword in normalized for keyword in ["列出", "查看", "有哪些", "当前"]):
                return ExecutionRequest(target=target, operation="browser_tab_list")
            activate_tab = self._extract_tab_identifier(raw_input, verbs=("切换到", "激活", "打开"))
            if activate_tab:
                return ExecutionRequest(target=target, operation="browser_tab_activate", params=activate_tab)
            close_tab = self._extract_tab_identifier(raw_input, verbs=("关闭", "关掉"))
            if close_tab:
                return ExecutionRequest(target=target, operation="browser_tab_close", params=close_tab)

        return None

    def _build_file_request(self, normalized: str, raw_input: str, target: str = "local_pc") -> ExecutionRequest | None:
        if "文件" not in normalized and "文档" not in normalized and ".py" not in raw_input and ".md" not in raw_input:
            return None

        if any(keyword in normalized for keyword in ["搜索", "查找", "找", "列出"]):
            query = self._extract_file_query(raw_input)
            if query:
                return ExecutionRequest(target=target, operation="file_search", params={"query": query})

        if any(keyword in normalized for keyword in ["读取", "读", "查看内容", "打开内容"]):
            path = self._extract_path_argument(raw_input)
            if path:
                return ExecutionRequest(target=target, operation="file_read", params={"path": path})

        if any(keyword in normalized for keyword in ["打开", "打开文件", "打开文档"]):
            path = self._extract_path_argument(raw_input)
            if path:
                return ExecutionRequest(target=target, operation="file_open", params={"path": path})

        if any(keyword in normalized for keyword in ["写入", "写入文件", "保存到", "存到"]):
            path = self._extract_path_argument(raw_input)
            text = self._extract_text_argument(raw_input)
            if path and text:
                if any(keyword in normalized for keyword in ["保存", "存到", "另存为"]):
                    return ExecutionRequest(target=target, operation="file_save_as", params={"path": path, "text": text})
                return ExecutionRequest(target=target, operation="file_write", params={"path": path, "text": text})

        return None

    @staticmethod
    def _extract_text_argument(raw_input: str) -> str:
        content_quoted = re.search(r"(?:内容|文本)[：:\s]*[\"“'‘](.+?)[\"”'']", raw_input)
        if content_quoted is not None:
            return content_quoted.group(1).strip()
        quoted = re.search(r"[\"“'‘](.+?)[\"”'’]", raw_input)
        if quoted is not None:
            return quoted.group(1).strip()
        match = re.search(r"(?:写入|复制|放到|设置)(?:到|进)?(?:剪贴板|剪切板)?[：:\s]*(?P<text>.+)$", raw_input)
        return match.group("text").strip() if match else ""

    @staticmethod
    def _extract_path_argument(raw_input: str) -> str:
        quoted = re.search(r"[\"“'‘](?P<path>.+?)[\"”'’]", raw_input)
        if quoted is not None:
            return quoted.group("path").strip()
        path_match = re.search(r"(?P<path>[A-Za-z]:\\[^，。]+|[^，。\s]+\.(?:py|md|txt|json|yaml|yml|csv|log))", raw_input)
        return path_match.group("path").strip() if path_match else ""

    @staticmethod
    def _extract_file_query(raw_input: str) -> str:
        quoted = re.search(r"[\"“'‘](?P<query>.+?)[\"”'’]", raw_input)
        if quoted is not None:
            return quoted.group("query").strip()
        match = re.search(r"(?:搜索|查找|找|列出)(?:文件|文档)?[：:\s]*(?P<query>.+)$", raw_input)
        if match is None:
            return ""
        return match.group("query").strip(" ：:，,。")

    @staticmethod
    def _extract_window_title(raw_input: str, verbs: tuple[str, ...]) -> str:
        verb_pattern = "|".join(re.escape(verb) for verb in verbs)
        match = re.search(rf"(?:{verb_pattern})(?:到)?[：:\s]*(?P<title>.+?)(?:窗口|窗体)", raw_input)
        if match is None:
            return ""
        return match.group("title").strip(" ：:，,。")

    @staticmethod
    def _extract_search_query(raw_input: str) -> str:
        match = re.search(r"(?:搜索|查一下|搜一下)(?:网页|浏览器|百度|谷歌|bing)?[：:\s]*(?P<query>.+)$", raw_input, flags=re.IGNORECASE)
        if match is None:
            return ""
        return match.group("query").strip(" ：:，,。")

    @staticmethod
    def _infer_search_engine(normalized: str) -> str:
        if "百度" in normalized or "baidu" in normalized:
            return "baidu"
        if "谷歌" in normalized or "google" in normalized:
            return "google"
        return "bing"

    @staticmethod
    def _extract_dimensions(raw_input: str) -> tuple[int, int] | None:
        numbers = re.findall(r"\b(\d+)\b", raw_input)
        if len(numbers) >= 2:
            try:
                return (int(numbers[0]), int(numbers[1]))
            except ValueError:
                pass
        dims = re.search(r"(\d+)\s*[xX×]\s*(\d+)", raw_input)
        if dims:
            return (int(dims.group(1)), int(dims.group(2)))
        return None

    @staticmethod
    def _extract_numbers(raw_input: str) -> list[str]:
        return re.findall(r"\b(\d+)\b", raw_input)

    @staticmethod
    def _extract_notification(raw_input: str) -> dict[str, str] | None:
        title_match = re.search(r"(?:通知|提醒)[：:\s]*(?:标题为|叫|叫)?[\s]*[\"“'‘](?P<title>[^\"”'’]+)[\"”'’]?[\s]*[,，]?(?:内容为|内容是|说|写)?[\s]*[\"“'‘](?P<text>[^\"”'’]+)[\"”'’]?", raw_input)
        if title_match:
            return {"title": title_match.group("title").strip("\"“'’"), "text": title_match.group("text").strip("\"“'’")}
        match = re.search(r"(?:通知|提醒)(?:我|你)?[：:\s]*[\"“'‘](?P<text>[^\"”'’]+)[\"”'']", raw_input)
        if match:
            text = match.group("text").strip("\"“'’")
            return {"title": "SpiritKin", "text": text}
        simple = re.search(r"(?:通知|提醒)[：:\s]*(?P<text>.+)$", raw_input)
        if simple:
            text = simple.group("text").strip(" ，:：")
            return {"title": "SpiritKin", "text": text}
        return None

    @staticmethod
    def _extract_tab_identifier(raw_input: str, verbs: tuple[str, ...]) -> dict[str, str | int] | None:
        verb_pattern = "|".join(re.escape(verb) for verb in verbs)
        match = re.search(rf"(?:{verb_pattern})[：:\s]*[\"“'‘](?P<title>.+?)[\"”'']", raw_input)
        if match:
            return {"title": match.group("title").strip("\"“'’")}
        index_match = re.search(rf"(?:{verb_pattern})[：:\s]*(?:第)?(?P<index>\d+)(?:个)?(?:标签页|tab|Tab)", raw_input)
        if index_match:
            return {"index": int(index_match.group("index"))}
        return None

    @staticmethod
    def _build_text_input_request(raw_input: str, target: str = "local_pc") -> ExecutionRequest | None:
        if not any(keyword in raw_input for keyword in ["输入", "键入", "打字"]):
            return None

        match = re.search(r"(?:输入|键入|打字)(?:一下)?[：:\s]*[\"“'‘](.+?)[\"”'’]", raw_input)
        if match is None:
            return None

        return ExecutionRequest(target=target, operation="enter_text", params={"text": match.group(1)})

    def _build_software_request(self, normalized: str, raw_input: str, target: str = "local_pc") -> ExecutionRequest | None:
        hardware_hints = ("机械臂", "機械臂", "夹爪", "夾爪", "openclaw", "机械手", "機械手")
        if any(keyword in normalized for keyword in hardware_hints):
            return None

        if any(keyword in normalized for keyword in ["扫描", "列出", "枚举", "查看"]):
            if any(keyword in normalized for keyword in ["软件", "应用", "程序", "app"]):
                return ExecutionRequest(target=target, operation="list_installed_apps")
            if any(keyword in normalized for keyword in ["硬件", "设备", "外设", "usb", "摄像头", "麦克风"]):
                return ExecutionRequest(target=target, operation="list_hardware_devices")

        feishu_request = self._build_feishu_request(normalized, raw_input)
        if feishu_request is not None:
            return feishu_request

        if any(keyword in normalized for keyword in ["屏幕", "画面"]):
            if any(keyword in normalized for keyword in ["读", "读取", "识别文字", "ocr", "上面的字", "有什么字"]):
                return ExecutionRequest(target=target, operation="screen_extract_text")
            if any(keyword in normalized for keyword in ["看", "看看", "分析", "识别", "理解", "有什么", "现在是什么"]):
                return ExecutionRequest(target=target, operation="screen_understand", params={"query": raw_input})

        close_match = re.search(r"(?:关闭|關閉|关掉|關掉|退出|结束|結束)(?:软件|軟件|应用|應用|程序|app)?[：:\s]*(?P<app>[^，。,.!?！？]+)", raw_input, flags=re.IGNORECASE)
        if close_match is not None:
            app_name = self._normalize_app_name(close_match.group("app"))
            if app_name:
                return ExecutionRequest(target=target, operation="close_app", params={"app_name": app_name})

        app_match = re.search(r"(?:打开|打開|開啟|启动|啟動|运行|運行|帮我开一下|幫我開一下|开一下|開一下)(?:软件|軟件|应用|應用|程序|app)?[：:\s]*(?P<app>[^，。,.!?！？]+)", raw_input, flags=re.IGNORECASE)
        if app_match is not None:
            app_name = self._normalize_app_name(app_match.group("app"))
            if app_name:
                return ExecutionRequest(target=target, operation="launch_app", params={"app_name": app_name})

        key_match = re.search(r"(?:按一下|按下|按|敲一下)[：:\s]*(?P<keys>ctrl\+c|ctrl\+v|ctrl\+s|alt\+tab|回车|确认|空格|退出|删除|退格|复制|粘贴|保存)", normalized, flags=re.IGNORECASE)
        if key_match is not None:
            keys = self._normalize_key_sequence(key_match.group("keys"))
            if keys:
                return ExecutionRequest(target=target, operation="press_keys", params={"keys": keys})

        return None

    @staticmethod
    def _build_feishu_request(normalized: str, raw_input: str) -> ExecutionRequest | None:
        normalized = Planner._normalize_feishu_asr_text(normalized)
        raw_input = Planner._normalize_feishu_asr_text(raw_input)
        explicit_feishu = any(keyword in normalized for keyword in ["飞书", "feishu", "lark"])
        quoted_desktop_notification = re.search(
            r"(?:发送)?(?:通知|提醒)[：:\s]*[\"“'‘][^\"”'’]+[\"”'’][\s,，]*[\"“'‘][^\"”'’]+[\"”'’]",
            raw_input,
        )
        if quoted_desktop_notification and not explicit_feishu:
            return None
        send_hints = ["发", "发送", "消息", "通知", "告诉", "转告", "跟", "对", "说", "提醒", "带话", "留言"]
        if not any(keyword in normalized for keyword in send_hints):
            return None

        patterns = [
            r"(?:帮我)?给(?P<recipient>[^，。,.!?！？\s]+)(?:发|发送|写|传)(?:个|一条)?(?:飞书|feishu|lark)?(?:消息)?(?:[，,：:\s]*(?:说|内容是|告诉他|告诉她)?[：:\s]*)?(?P<text>.+)",
            r"(?:飞书|feishu|lark)(?:上)?(?:给|发给)(?P<recipient>[^，。,.!?！？\s]+?)(?:[，,：:\s]*(?:说|内容是)?[：:\s]+|说|讲)(?P<text>.+)",
            r"(?:飞书|feishu|lark)(?:上)?(?:跟|告诉)(?P<recipient>[^，。,.!?！？\s]+?)(?:说|讲|发)(?P<text>.+)",
            r"(?:帮我|麻烦|请)?(?:用|在|通过)?(?:飞书|feishu|lark)?(?:上)?(?:通知|提醒|告诉|转告)(?:一下)?(?P<recipient>[^，。,.!?！？\s]+?)(?:[，,：:\s]+|说|讲|内容是)(?P<text>.+)",
            r"(?:帮我|麻烦|请)?(?:跟|对)(?P<recipient>[^，。,.!?！？\s]+?)(?:说|讲|通知|提醒)(?P<text>.+)",
            r"(?:帮我|麻烦|请)?(?:发|发送)(?:个|一条|一下)?(?:飞书|feishu|lark)?(?:消息)?(?:给|到)(?P<recipient>[^，。,.!?！？\s]+)(?:[，,：:\s]*(?:说|讲|内容是)?[：:\s]*)?(?P<text>.+)",
            r"(?:帮我|麻烦|请)?(?:用|在|通过)?(?:飞书|feishu|lark)(?:上)?(?:通知|提醒|告诉|转告)(?:一下)?(?P<recipient>[\u4e00-\u9fffA-Za-z0-9_]{2,4}?)(?P<text>.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw_input, flags=re.IGNORECASE)
            if match is None:
                continue
            recipient = match.group("recipient").strip().strip("“”\"'‘’")
            text = match.group("text").strip().strip("“”\"'‘’")
            text = re.sub(r"^(?:说|讲|内容是|告诉他|告诉她|通知他|通知她|提醒他|提醒她)[：:\s]*", "", text).strip()
            if recipient and text:
                return ExecutionRequest(target="feishu", operation="send_message", params={"recipient": recipient, "text": text})

        return None

    @staticmethod
    def _normalize_feishu_asr_text(text: str) -> str:
        replacements = {
            "飛書": "飞书",
            "非书": "飞书",
            "飞鼠": "飞书",
            "飞输": "飞书",
            "飞叔": "飞书",
            "菲书": "飞书",
        }
        normalized = text.strip()
        for source, target in replacements.items():
            normalized = normalized.replace(source, target)
        return normalized

    @staticmethod
    def _normalize_app_name(app_name: str) -> str:
        cleaned = app_name.strip().strip("“”\"'‘’")
        cleaned = re.sub(r"^(?:一下|帮我|请|软件|应用|程序|app)", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"(?:这个|这个软件|这个应用)$", "", cleaned, flags=re.IGNORECASE).strip()
        alias_map = {
            "飞书": "Feishu",
            "飛書": "Feishu",
            "lark": "Lark",
            "记事本": "notepad",
            "浏览器": "browser",
            "瀏覽器": "browser",
            "游覽器": "browser",
            "默认浏览器": "browser",
            "默認瀏覽器": "browser",
            "游览器": "browser",
            "留览器": "browser",
            "新的浏览器": "msedge",
            "新浏览器": "msedge",
            "edge": "msedge",
            "edge浏览器": "msedge",
            "Edge浏览器": "msedge",
            "msedge": "msedge",
            "microsoftedge": "msedge",
            "microsoftedge浏览器": "msedge",
            "微软edge": "msedge",
            "谷歌": "chrome",
            "谷歌浏览器": "chrome",
            "google": "chrome",
            "googlechrome": "chrome",
            "google浏览器": "chrome",
            "chrome浏览器": "chrome",
            "firefox": "firefox",
            "firefox浏览器": "firefox",
            "mozilla": "firefox",
            "mozillafirefox": "firefox",
            "火狐": "firefox",
            "火狐浏览器": "firefox",
            "brave": "brave",
            "brave浏览器": "brave",
            "bravebrowser": "brave",
            "opera": "opera",
            "opera浏览器": "opera",
            "360": "360浏览器",
            "360浏览器": "360浏览器",
            "360安全浏览器": "360浏览器",
            "360极速浏览器": "360浏览器",
            "qq浏览器": "qq浏览器",
            "qqbrowser": "qq浏览器",
            "腾讯浏览器": "qq浏览器",
            "搜狗浏览器": "搜狗浏览器",
            "sogou": "搜狗浏览器",
            "sogouexplorer": "搜狗浏览器",
            "火豹": "火豹浏览器",
            "火豹浏览器": "火豹浏览器",
            "火爆": "火豹浏览器",
            "火爆浏览器": "火豹浏览器",
            "火暴": "火豹浏览器",
            "火暴浏览器": "火豹浏览器",
            "火包浏览器": "火豹浏览器",
            "命令提示符": "cmd",
            "命令行": "cmd",
            "控制台": "cmd",
            "cmd": "cmd",
            "cmdexe": "cmd",
            "powershell": "powershell",
            "pwsh": "pwsh",
            "终端": "terminal",
            "windowsterminal": "terminal",
            "vscode": "code",
            "vsCode": "code",
            "visualstudiocode": "code",
            "微信": "微信",
            "wechat": "wechat",
            "钉钉": "钉钉",
            "dingtalk": "dingtalk",
        }
        lowered = cleaned.lower().replace(" ", "")
        return alias_map.get(cleaned, alias_map.get(lowered, cleaned))

    @staticmethod
    def _normalize_key_sequence(keys: str) -> list[str]:
        normalized = keys.lower().replace(" ", "")
        alias_map = {
            "回车": ["enter"],
            "确认": ["enter"],
            "空格": ["space"],
            "退出": ["esc"],
            "删除": ["delete"],
            "退格": ["backspace"],
            "复制": ["ctrl", "c"],
            "粘贴": ["ctrl", "v"],
            "保存": ["ctrl", "s"],
        }
        if normalized in alias_map:
            return alias_map[normalized]
        if "+" in normalized:
            return [part for part in normalized.split("+") if part]
        return [normalized] if normalized else []

    def _build_arm_request(self, normalized: str, raw_input: str) -> ExecutionRequest | None:
        normalized = self._normalize_openclaw_asr_text(normalized)
        compact = self._compact_voice_text(normalized)
        if compact in {"关闭移动", "關閉移動"}:
            return ExecutionRequest(target="openclaw", operation="close_gripper")
        if compact in {"打开夹爪", "打開夾爪"}:
            return ExecutionRequest(target="openclaw", operation="open_gripper")

        arm_keywords = ("机械臂", "機械臂", "机械b", "機械b", "机械", "機械", "夹爪", "夾爪", "openclaw", "机械手", "機械手")
        if not any(keyword in normalized for keyword in arm_keywords):
            return None

        if any(keyword in normalized for keyword in ["状态", "狀態", "就绪", "就緒", "忙吗", "忙嗎", "当前位置", "當前位置", "在哪"]):
            return ExecutionRequest(target="openclaw", operation="status")

        if any(keyword in normalized for keyword in ["回零", "归位", "复位", "home"]):
            return ExecutionRequest(target="openclaw", operation="home")

        if "夹爪" in normalized and any(keyword in normalized for keyword in ["打开", "张开", "松开"]):
            return ExecutionRequest(target="openclaw", operation="open_gripper")

        if "夹爪" in normalized and any(keyword in normalized for keyword in ["关闭", "合上", "夹紧"]):
            return ExecutionRequest(target="openclaw", operation="close_gripper")

        if any(keyword in normalized for keyword in ["移动到", "移到", "移动至"]):
            coords = self._extract_numbers(raw_input)
            if len(coords) >= 3:
                return ExecutionRequest(
                    target="openclaw",
                    operation="move_to",
                    params={"x": coords[0], "y": coords[1], "z": coords[2]},
                )

        return None
