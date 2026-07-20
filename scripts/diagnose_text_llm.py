"""Print the resolved text-LLM connection and make one real request.

Usage:
    python scripts/diagnose_text_llm.py [prompt]

Reveals the actual endpoint, model, HTTP status and raw body so
"no response" failures (wrong base_url, 401 key, 400 param, timeout)
are diagnosable without guessing.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from backend.app.settings import resolve_text_model, resolve_text_provider
from backend.services.conversation_engine import (
    _resolve_text_engine_connection,
)


def main() -> int:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "你好，请用一句话回应。"
    provider = resolve_text_provider()
    model = resolve_text_model()
    engine_provider, base_url, api_key = _resolve_text_engine_connection(provider)

    print("=== resolved text-LLM connection ===")
    print(f"provider (config)   : {provider}")
    print(f"engine provider     : {engine_provider}")
    print(f"model               : {model}")
    print(f"base_url            : {base_url}")
    print(f"api_key present     : {'yes (' + str(len(api_key)) + ' chars)' if api_key else 'NO'}")

    if engine_provider != "openai_compatible":
        print(f"\nengine is {engine_provider}, not an HTTP endpoint; nothing to probe.")
        return 0

    is_ollama = "11434" in base_url
    url = base_url.replace("/v1", "").rstrip("/") + "/api/chat" if is_ollama else base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if not is_ollama:
        body.update({"temperature": 0.4, "max_tokens": 128})
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    print(f"\n=== POST {url} ===")
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            print(f"HTTP {resp.status}")
            print(raw[:2000])
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} {exc.reason}")
        print(exc.read().decode("utf-8", errors="replace")[:2000])
        return 1
    except Exception as exc:  # noqa: BLE001 - diagnostic surface
        print(f"{type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
