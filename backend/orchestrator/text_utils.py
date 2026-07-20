"""Pure text/time helpers extracted from agent_cluster.

Module-level functions with no dependency on AgentCluster state. Kept as a
standalone module so the orchestrator core stays focused on coordination.
"""

from __future__ import annotations

import re
from datetime import datetime


def current_time_context() -> dict[str, str]:
    now = datetime.now().astimezone()
    return {
        "iso": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "timezone": now.tzname() or str(now.utcoffset() or ""),
    }


def format_current_time_context(metadata: dict[str, object]) -> str:
    current = metadata.get("current_time")
    if not isinstance(current, dict):
        current = current_time_context()
    date = str(current.get("date") or "").strip()
    time_text = str(current.get("time") or "").strip()
    weekday = str(current.get("weekday") or "").strip()
    tz = str(current.get("timezone") or "").strip()
    iso = str(current.get("iso") or "").strip()
    return (
        f"当前真实日期时间：{date} {time_text} {tz}（{weekday}）。"
        f"回答今天/现在/当前时间类问题时必须使用这个值；不要使用模型训练截止日期。ISO={iso}\n"
    )


def clean_spoken_text(text: str) -> str:
    value = re.sub(r"<(?:emotion|action):[^>]+>", "", text or "", flags=re.IGNORECASE)
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", value)
    value = re.sub(r"<img\b[^>]*>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"[（(【\[]\s*(?:笑|微笑|开心|哭|流泪|害羞|尴尬|捂脸|表情|emoji|sticker)[^）)】\]]{0,16}[）)】\]]", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\[[^\]]*(?:表情包|表情|图片|image|sticker)[^\]]*\]", " ", value, flags=re.IGNORECASE)
    value = re.sub(r":[a-z0-9_+\-]+:", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"[\U0001F000-\U0010FFFF]", " ", value)
    value = re.sub(r"[☀-➿️‍]", " ", value)
    value = re.sub(r"(?:[;:=8xX][\-oO']?[\)\(DPp/\\]|[\)\(][\-oO']?[;:=8xX])", " ", value)
    value = re.sub(r"https?://\S+", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"file:/+\S+", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\S+\.(?:png|jpe?g|gif|webp|bmp|svg)(?:\?\S*)?", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    return value if re.search(r"[0-9A-Za-z一-鿿]", value) else ""
