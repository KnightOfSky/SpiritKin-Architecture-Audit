import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "browser-extension" / "pdd-product-extractor"


class PddBrowserExtensionTests(unittest.TestCase):
    def test_manifest_uses_side_panel_and_scoped_pdd_content_scripts(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["side_panel"]["default_path"], "sidepanel.html")
        self.assertIn("https://*.yangkeduo.com/*", manifest["host_permissions"])
        self.assertIn("http://*/*", manifest["optional_host_permissions"])
        self.assertEqual(manifest["content_scripts"][0]["world"], "MAIN")

    def test_extension_has_no_autoprocess_runtime_ports_or_management_token(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in EXTENSION.glob("*.js"))

        for forbidden in ("localhost:5173", "localhost:5000", "localhost:8013", "localhost:8040", "SPIRITKIN_MANAGEMENT_TOKEN"):
            self.assertNotIn(forbidden, source)
        self.assertIn("browser_extension", (EXTENSION / "README.md").read_text(encoding="utf-8"))

    def test_extension_defaults_to_phone_queue_auto_claim(self):
        background = (EXTENSION / "background.js").read_text(encoding="utf-8")

        self.assertIn("autoPoll: true", background)
        self.assertIn("chrome.runtime.onStartup.addListener", background)
        self.assertIn('api("/extension/links/claim"', background)

    def test_side_panel_exposes_queue_results_settings_and_pairing(self):
        html = (EXTENSION / "sidepanel.html").read_text(encoding="utf-8")

        for expected in ("view-queue", "view-results", "view-settings", "pairingToken", "syncBtn", "extractCurrentBtn"):
            self.assertIn(f'id="{expected}"', html)
        self.assertNotIn("<script>", html)


if __name__ == "__main__":
    unittest.main()
