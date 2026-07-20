from __future__ import annotations

import argparse
import ipaddress
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

NO_STORE_EXTENSIONS = {".html", ".js", ".css", ".json", ".wasm"}
STATE_FILE = Path(__file__).resolve().parents[2] / "runtime" / "avatar_locomotion_state.json"
SERVER_STATE = {
    "locomotion": None,
}


def load_server_state() -> None:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        raw = data.get("locomotion")
        if isinstance(raw, dict):
            SERVER_STATE["locomotion"] = {
                "x": float(raw["x"]),
                "z": float(raw["z"]),
                "yaw": float(raw["yaw"]),
            }
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        SERVER_STATE["locomotion"] = None


def save_server_state() -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"locomotion": SERVER_STATE.get("locomotion")}, separators=(",", ":")), encoding="utf-8")


class NoCacheStaticHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, url_prefix: str = "", **kwargs) -> None:
        normalized = "/" + str(url_prefix or "").strip().strip("/") if str(url_prefix or "").strip().strip("/") else ""
        self.url_prefix = normalized
        super().__init__(*args, **kwargs)

    def _mapped_static_path(self, path: str) -> str | None:
        if not self.url_prefix:
            return path
        parsed = urlparse(path)
        request_path = parsed.path.rstrip("/") or "/"
        if request_path == self.url_prefix:
            mapped = "/"
        elif parsed.path.startswith(f"{self.url_prefix}/"):
            mapped = parsed.path[len(self.url_prefix) :]
        else:
            return None
        return f"{mapped}?{parsed.query}" if parsed.query else mapped

    def translate_path(self, path: str) -> str:
        mapped = self._mapped_static_path(path)
        if mapped is None:
            return str(Path(self.directory) / ".spiritkin-not-found")
        return super().translate_path(mapped)

    def log_message(self, format: str, *args: object) -> None:
        """Keep the development log useful without recording every asset hit."""
        status = args[1] if len(args) > 1 else None
        try:
            status_code = int(status)
        except (TypeError, ValueError):
            status_code = 0
        if status_code < 400:
            return
        super().log_message(format, *args)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _trusted_local_client(self) -> bool:
        try:
            return ipaddress.ip_address(str(self.client_address[0])).is_loopback
        except ValueError:
            return False

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/avatar-state/locomotion":
            if not self._trusted_local_client():
                self.send_error(403)
                return
            self._send_json(200, {"locomotion": SERVER_STATE.get("locomotion")})
            return
        if self.url_prefix and self._mapped_static_path(self.path) is None:
            self.send_error(404)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/avatar-state/locomotion":
            self.send_error(404)
            return
        if not self._trusted_local_client():
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(min(length, 4096))
            data = json.loads(raw.decode("utf-8") or "{}")
            if data.get("clear") is True:
                SERVER_STATE["locomotion"] = None
            else:
                x = float(data["x"])
                z = float(data["z"])
                yaw = float(data["yaw"])
                SERVER_STATE["locomotion"] = {"x": x, "z": z, "yaw": yaw}
            save_server_state()
            self._send_json(200, {"ok": True, "locomotion": SERVER_STATE.get("locomotion")})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})

    def end_headers(self) -> None:
        path = Path(self.path.split("?", 1)[0])
        if path.suffix.lower() in NO_STORE_EXTENSIONS:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve SpiritKin frontend with development no-cache headers.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--directory", default=str(Path(__file__).resolve().parents[2] / "frontend"))
    parser.add_argument("--url-prefix", default="", help="Optional URL prefix mapped to the static directory, for example /frontend.")
    args = parser.parse_args()

    load_server_state()
    handler = partial(NoCacheStaticHandler, directory=str(Path(args.directory).resolve()), url_prefix=args.url_prefix)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"[frontend] serving {args.directory} on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
