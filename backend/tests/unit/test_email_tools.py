from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.tools.base import ToolCall
from backend.tools.email_tools import EmailConfig, EmailSendTool


class FakeSender:
    def __init__(self) -> None:
        self.message = None

    def send(self, message):
        self.message = message
        return "message-test-001"


class EmailToolTests(unittest.TestCase):
    def _config(self, root: Path) -> EmailConfig:
        return EmailConfig(
            enabled=True,
            host="smtp.example.test",
            port=465,
            secure=True,
            username="sender@example.test",
            password="secret",
            from_address="sender@example.test",
            from_name='Spirit "Kin"',
            workspace_root=root,
        )

    def test_send_requires_explicit_confirmation(self):
        with TemporaryDirectory() as tmp:
            sender = FakeSender()
            tool = EmailSendTool(config=self._config(Path(tmp)), sender=sender)
            result = tool.invoke(ToolCall("email.send", {"to": ["user@example.test"], "subject": "Hi", "body": "Hello"}))

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "email_confirmation_required")
        self.assertIsNone(sender.message)

    def test_send_builds_safe_message_and_workspace_only_attachment(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            attachment = root / "report.txt"
            attachment.write_text("report", encoding="utf-8")
            sender = FakeSender()
            tool = EmailSendTool(config=self._config(root), sender=sender)
            result = tool.invoke(
                ToolCall(
                    "email.send",
                    {
                        "authz_confirmed": True,
                        "to": ["user@example.test"],
                        "cc": [],
                        "subject": "Weekly report",
                        "body": "See attached.",
                        "attachments": [str(attachment)],
                    },
                )
            )

        self.assertTrue(result.success)
        self.assertEqual(result.data["message_id"], "message-test-001")
        self.assertEqual(sender.message["From"], '"Spirit \\"Kin\\"" <sender@example.test>')
        self.assertEqual(sender.message["To"], "user@example.test")
        self.assertEqual(sender.message["Subject"], "Weekly report")

    def test_attachment_outside_workspace_is_denied(self):
        with TemporaryDirectory() as tmp, TemporaryDirectory() as outside:
            root = Path(tmp)
            attachment = Path(outside) / "secret.txt"
            attachment.write_text("secret", encoding="utf-8")
            tool = EmailSendTool(config=self._config(root), sender=FakeSender())
            result = tool.invoke(
                ToolCall(
                    "email.send",
                    {
                        "authz_confirmed": True,
                        "to": ["user@example.test"],
                        "subject": "Blocked",
                        "body": "Nope",
                        "attachments": [str(attachment)],
                    },
                )
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "email_attachment_denied")


if __name__ == "__main__":
    unittest.main()
