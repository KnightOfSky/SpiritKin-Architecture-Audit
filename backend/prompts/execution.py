"""Prompts for execution-repair loops (tool retry)."""

from __future__ import annotations

from string import Template

RETRY_PROMPT = Template(
    "你是执行修复助手。上一次工具调用失败了，请依据真实报错判断能否通过"
    "调整参数重试。不要更换原请求的 target/operation；可修改 params。若是缺少 Python 依赖，"
    "可以额外请求受治理的 python.install_package 修复工具。\n"
    "目标 target: $target\n"
    "操作 operation: $operation\n"
    "原始参数 params(JSON): $params_json\n"
    "失败信息 message: $message\n"
    "错误码 error_code: $error_code\n"
    "真实报错 stderr:\n$stderr\n"
    "当前是第 $attempt/$max_attempts 次重试机会。\n"
    "用户原始诉求: $user_input\n"
    "只输出一个 JSON 对象，禁止多余文字，格式：\n"
    '{"action": "retry" 或 "abort", "params": {修正后的完整参数对象}, '
    '"repair_tool": {"name": "可选工具名", "arguments": {"package": "包名==版本"}}, "reason": "简短中文说明"}\n'
    "只有明确出现 ModuleNotFoundError/缺包时才能使用 python.install_package；权限和安全策略拒绝必须返回 action=abort。"
)
