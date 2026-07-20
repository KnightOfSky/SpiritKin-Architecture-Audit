from __future__ import annotations

import os
import re
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from backend.tools.base import BaseTool, ToolCall, ToolResult, ToolSpec

EMAIL_RE = re.compile(r"^[^\s@\r\n]+@[^\s@\r\n]+\.[^\s@\r\n]+$")
MAX_RECIPIENTS = 10
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class EmailConfig:
    enabled: bool = False
    host: str = ""
    port: int = 465
    secure: bool = True
    starttls: bool = True
    username: str = ""
    password: str = ""
    from_address: str = ""
    from_name: str = ""
    workspace_root: Path = Path.cwd()

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> EmailConfig:
        env = os.environ if environ is None else environ
        raw_port = str(env.get("SPIRITKIN_EMAIL_SMTP_PORT") or "465").strip()
        try:
            port = int(raw_port)
        except ValueError:
            port = 465
        root = Path(str(env.get("SPIRITKIN_WORKSPACE_ROOT") or Path.cwd())).resolve()
        from_address = str(env.get("SPIRITKIN_EMAIL_FROM") or env.get("SPIRITKIN_EMAIL_SMTP_USER") or "").strip()
        return cls(
            enabled=str(env.get("SPIRITKIN_EMAIL_ENABLED") or "0").strip().lower() in {"1", "true", "yes", "on"},
            host=str(env.get("SPIRITKIN_EMAIL_SMTP_HOST") or "").strip(),
            port=max(1, min(65535, port)),
            secure=str(env.get("SPIRITKIN_EMAIL_SMTP_SECURE") or "1").strip().lower() in {"1", "true", "yes", "on"},
            starttls=str(env.get("SPIRITKIN_EMAIL_SMTP_STARTTLS") or "1").strip().lower() in {"1", "true", "yes", "on"},
            username=str(env.get("SPIRITKIN_EMAIL_SMTP_USER") or "").strip(),
            password=str(env.get("SPIRITKIN_EMAIL_SMTP_PASSWORD") or ""),
            from_address=from_address,
            from_name=str(env.get("SPIRITKIN_EMAIL_FROM_NAME") or "").strip(),
            workspace_root=root,
        )

    def validate(self) -> str | None:
        if not self.enabled:
            return "邮件功能未启用。"
        if not self.host or not self.username or not self.password:
            return "SMTP 配置不完整：需要主机、用户名和密码。"
        if not valid_email(self.from_address):
            return "发件人地址无效。"
        if "\r" in self.host or "\n" in self.host:
            return "SMTP 主机地址无效。"
        return None


class SmtpEmailSender:
    def __init__(self, config: EmailConfig, *, smtp_factory: Callable[..., smtplib.SMTP] | None = None):
        self.config = config
        self.smtp_factory = smtp_factory

    def send(self, message: EmailMessage) -> str:
        config = self.config
        context = ssl.create_default_context()
        if config.secure:
            factory = self.smtp_factory or smtplib.SMTP_SSL
            with factory(config.host, config.port, context=context, timeout=20) as client:
                client.login(config.username, config.password)
                client.send_message(message)
        else:
            factory = self.smtp_factory or smtplib.SMTP
            with factory(config.host, config.port, timeout=20) as client:
                client.ehlo()
                if config.starttls:
                    client.starttls(context=context)
                    client.ehlo()
                client.login(config.username, config.password)
                client.send_message(message)
        return str(message.get("Message-ID") or "")


class EmailSendTool(BaseTool):
    spec = ToolSpec(
        name="email.send",
        description="通过受控 SMTP 发送邮件，可附加工作区内文件；必须经过本次用户确认。",
        target="network",
        operation="email_send",
        risk_level="high",
        schema={
            "to": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_RECIPIENTS},
            "cc": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_RECIPIENTS},
            "subject": {"type": "string", "maxLength": 240},
            "body": {"type": "string", "maxLength": 100000},
            "html": {"type": "string", "maxLength": 200000},
            "attachments": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_ATTACHMENTS},
        },
    )

    def __init__(self, *, config: EmailConfig | None = None, sender: SmtpEmailSender | None = None):
        self.config = config or EmailConfig.from_env()
        self.sender = sender or SmtpEmailSender(self.config)

    def invoke(self, call: ToolCall) -> ToolResult:
        if not self.supports(call):
            return ToolResult(False, f"不支持的工具: {call.name}", error_code="tool_not_supported")
        if not bool((call.arguments or {}).get("authz_confirmed")):
            return ToolResult(False, "发送邮件需要本次显式确认。", error_code="email_confirmation_required")
        try:
            message, recipients = self._build_message(dict(call.arguments or {}))
            message_id = self.sender.send(message)
        except PermissionError as exc:
            return ToolResult(False, str(exc), error_code="email_attachment_denied")
        except (OSError, smtplib.SMTPException, TypeError, ValueError) as exc:
            return ToolResult(False, str(exc), error_code="email_send_failed")
        return ToolResult(
            True,
            f"邮件已发送给 {', '.join(recipients)}。",
            data={"message_id": message_id, "recipients": recipients, "subject": message.get("Subject", "")},
            metadata={"email_message_id": message_id},
        )

    def _build_message(self, arguments: dict[str, Any]) -> tuple[EmailMessage, list[str]]:
        error = self.config.validate()
        if error:
            raise ValueError(error)
        recipients = _addresses(arguments.get("to"), "收件人")
        cc = _addresses(arguments.get("cc"), "抄送", allow_empty=True)
        if not recipients:
            raise ValueError("收件人列表不能为空。")
        if len(recipients) + len(cc) > MAX_RECIPIENTS:
            raise ValueError(f"收件人与抄送总数不能超过 {MAX_RECIPIENTS}。")
        subject = _header_text(arguments.get("subject"), "主题", max_length=240)
        body = str(arguments.get("body") or "")
        html = str(arguments.get("html") or "")
        if not body.strip() and not html.strip():
            raise ValueError("邮件正文不能为空。")

        message = EmailMessage()
        message["From"] = _display_address(self.config.from_name, self.config.from_address)
        message["To"] = ", ".join(recipients)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message.set_content(body)
        if html:
            message.add_alternative(html, subtype="html")
        for raw_path in _string_list(arguments.get("attachments")):
            path = _safe_attachment(Path(raw_path), self.config.workspace_root)
            message.add_attachment(path.read_bytes(), maintype="application", subtype="octet-stream", filename=path.name)
        return message, [*recipients, *cc]


def get_email_tools(*, config: EmailConfig | None = None, sender: SmtpEmailSender | None = None) -> list[BaseTool]:
    return [EmailSendTool(config=config, sender=sender)]


def valid_email(value: str) -> bool:
    return bool(EMAIL_RE.fullmatch(str(value or "").strip()))


def _addresses(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if value is None and allow_empty:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{label}必须是邮箱地址数组。")
    if len(value) > MAX_RECIPIENTS:
        raise ValueError(f"{label}不能超过 {MAX_RECIPIENTS} 个。")
    addresses = [str(item or "").strip() for item in value]
    invalid = next((item for item in addresses if not valid_email(item)), None)
    if invalid:
        raise ValueError(f"{label}地址无效：{invalid}")
    return addresses


def _header_text(value: Any, label: str, *, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"邮件{label}不能为空。")
    if "\r" in text or "\n" in text:
        raise ValueError(f"邮件{label}不能包含换行。")
    if len(text) > max_length:
        raise ValueError(f"邮件{label}过长。")
    return text


def _safe_attachment(path: Path, workspace_root: Path) -> Path:
    target = path.expanduser().resolve()
    root = workspace_root.resolve()
    if target != root and root not in target.parents:
        raise PermissionError("附件必须位于工作区目录内。")
    if not target.is_file():
        raise FileNotFoundError(f"附件不存在：{target}")
    if target.stat().st_size > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"附件不能超过 {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB。")
    return target


def _display_address(name: str, address: str) -> str:
    if not name:
        return address
    return f'"{name.replace(chr(34), chr(92) + chr(34))}" <{address}>'


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("附件必须是路径数组。")
    if len(value) > MAX_ATTACHMENTS:
        raise ValueError(f"附件不能超过 {MAX_ATTACHMENTS} 个。")
    return [str(item or "").strip() for item in value if str(item or "").strip()]
