from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


class PlaywrightGameDriver:
    """Playwright adapter that calls only a game's explicit SpiritKin test API."""

    def __init__(self, url: str, *, headless: bool = False, artifact_dir: str | Path = "state/game_automation/frames"):
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise RuntimeError("Playwright is required for browser-game automation; install requirements-dev.txt") from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._page = self._browser.new_page(viewport={"width": 1280, "height": 800})
        self._page.goto(url, wait_until="networkidle")
        self._page.bring_to_front()
        self._page.wait_for_function("() => !!window.spiritkinGame")
        self._artifact_dir = Path(artifact_dir).resolve()
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        self._frame_sequence = 0

    def title(self) -> str:
        return self._page.title()

    def is_focused(self) -> bool:
        return bool(self._page.evaluate("() => document.hasFocus()"))

    def observe(self) -> dict[str, Any]:
        value = self._page.evaluate("() => window.spiritkinGame.status()")
        return dict(value or {})

    def perform(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        value = self._page.evaluate(
            "([action, params]) => window.spiritkinGame.perform(action, params)",
            [action, params],
        )
        return dict(value or {})

    def emergency_stop(self, reason: str) -> None:
        self._page.evaluate("reason => window.spiritkinGame.stop(reason)", reason)

    def capture_keyframe(self, label: str) -> dict[str, Any]:
        safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in label)[:80]
        self._frame_sequence += 1
        path = self._artifact_dir / f"{self._frame_sequence:05d}_{safe}.png"
        data = self._page.screenshot(path=str(path))
        return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}

    def close(self) -> None:
        self._browser.close()
        self._playwright.stop()
