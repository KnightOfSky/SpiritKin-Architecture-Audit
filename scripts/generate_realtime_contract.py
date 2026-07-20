"""重新生成前端/桌面端实时契约文件（事件名 + 默认端口）。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.app.realtime_contract import (
    DESKTOP_CONTRACT_PATH,
    FRONTEND_CONTRACT_PATH,
    render_desktop_contract,
    render_frontend_contract,
)


def main() -> int:
    targets = {
        REPO_ROOT / FRONTEND_CONTRACT_PATH: render_frontend_contract(),
        REPO_ROOT / DESKTOP_CONTRACT_PATH: render_desktop_contract(),
    }
    for path, content in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"written: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
