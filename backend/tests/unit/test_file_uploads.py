from __future__ import annotations

import base64
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.command_gateway import build_attachments_ingest_response
from backend.app.file_uploads import ingest_uploaded_files
from backend.mobile.artifact_store import MobileArtifactStore


class FileUploadTests(unittest.TestCase):
    def test_ingest_uploaded_files_saves_text_and_builds_documents(self):
        with TemporaryDirectory() as tmp:
            report = ingest_uploaded_files(
                [{"path": "docs/readme.md", "text": "# Hello\n需要处理的资料。"}],
                upload_root=Path(tmp),
            )
            saved = Path(report.root) / "docs" / "readme.md"

            self.assertTrue(saved.exists())
            self.assertEqual(len(report.attachments), 1)
            self.assertEqual(len(report.documents), 1)
            self.assertEqual(report.documents[0]["path"], "docs/readme.md")

    def test_ingest_uploaded_files_saves_binary_without_document_text(self):
        with TemporaryDirectory() as tmp:
            report = ingest_uploaded_files(
                [{"path": "../image.png", "content_base64": base64.b64encode(b"png").decode("ascii"), "mime_type": "image/png"}],
                upload_root=Path(tmp),
            )

            self.assertEqual(len(report.attachments), 1)
            self.assertEqual(report.attachments[0]["relative_path"], "image.png")
            self.assertEqual(report.documents, [])

    def test_command_gateway_attachments_ingest_response(self):
        status, payload = build_attachments_ingest_response(
            {"files": [{"path": "note.txt", "text": "拖拽文件内容"}], "purpose": "drag_drop"}
        )

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["upload"]["attachments"][0]["purpose"], "drag_drop")
        self.assertEqual(payload["upload"]["documents"][0]["text"], "拖拽文件内容")

    def test_mobile_artifact_store_ingests_and_cleans_images(self):
        with TemporaryDirectory() as tmp:
            store = MobileArtifactStore(Path(tmp))
            result = store.ingest(
                {
                    "purpose": "ios_work_image",
                    "ttl_hours": 1,
                    "files": [{"path": "product.png", "content_base64": base64.b64encode(b"png").decode("ascii"), "mime_type": "image/png"}],
                },
                source="ios_terminal",
                device_id="iphone",
            )
            snapshot = store.snapshot()
            cleanup = store.cleanup(expired_only=False, keep_recent=0)

        self.assertTrue(result["ok"])
        self.assertEqual(snapshot["image_count"], 1)
        self.assertEqual(snapshot["recent"][0]["source"], "ios_terminal")
        self.assertEqual(cleanup["removed"], 1)


if __name__ == "__main__":
    unittest.main()
