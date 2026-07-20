from __future__ import annotations

import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.app.static_frontend_server import NoCacheStaticHandler


class StaticFrontendServerTests(unittest.TestCase):
    def test_prefixed_frontend_root_does_not_expose_project_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "ios_controller_prototype.html").write_text("safe frontend", encoding="utf-8")
            (root / ".env.cloud").write_text("SPIRITKIN_MANAGEMENT_TOKEN=do-not-serve", encoding="utf-8")
            (root / "state").mkdir()
            (root / "state" / "control.json").write_text("sensitive", encoding="utf-8")

            handler = partial(NoCacheStaticHandler, directory=str(frontend), url_prefix="/frontend")
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urlopen(f"{base_url}/frontend/ios_controller_prototype.html", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b"safe frontend")
                with urlopen(Request(f"{base_url}/frontend/ios_controller_prototype.html", method="HEAD"), timeout=3) as response:
                    self.assertEqual(response.status, 200)
                with urlopen(f"{base_url}/avatar-state/locomotion", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b'"locomotion"', response.read())
                request = Request(
                    f"{base_url}/avatar-state/locomotion",
                    data=b'{"x":0.1,"z":0.2,"yaw":0.3}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with patch("backend.app.static_frontend_server.save_server_state"):
                    with urlopen(request, timeout=3) as response:
                        self.assertEqual(response.status, 200)
                for path in ("/.env.cloud", "/state/control.json", "/frontend/../.env.cloud"):
                    with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                        urlopen(f"{base_url}{path}", timeout=3)
                    self.assertEqual(error.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
