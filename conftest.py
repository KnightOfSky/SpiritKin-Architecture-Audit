"""Pytest bootstrap shared by the whole suite.

Keeps the repo root importable (tests use ``from backend...`` absolute imports)
and disables live device/network probing by default so read-only snapshots
never block on external tooling such as ``adb`` or ``tailscale``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("SPIRITKIN_DISABLE_DEVICE_PROBES", "1")
